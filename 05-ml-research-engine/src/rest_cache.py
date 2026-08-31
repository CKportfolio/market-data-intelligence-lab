from __future__ import annotations
import gzip
import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pandas as pd

DAY_MS=86_400_000

@dataclass
class RestConfig:
    base_url: str = os.environ.get('BYBIT_REST_BASE','https://api.bybit.com')
    spot_symbol: str = os.environ.get('SPOT_SYMBOL','BTCUSDC')
    perp_symbol: str = os.environ.get('PERP_SYMBOL','BTCUSDT')
    request_delay_ms: int = int(os.environ.get('REST_DELAY_MS','90'))
    retries: int = int(os.environ.get('REST_RETRIES','4'))

class BybitPublicRest:
    def __init__(self,cfg:RestConfig): self.cfg=cfg
    def get(self,endpoint:str,params:dict)->dict:
        q=urllib.parse.urlencode({k:v for k,v in params.items() if v is not None and v!=''})
        url=self.cfg.base_url.rstrip('/')+endpoint+'?'+q
        last=None
        for attempt in range(self.cfg.retries+1):
            try:
                req=urllib.request.Request(url,headers={'User-Agent':'market-ml-research-streaming/0.2'})
                with urllib.request.urlopen(req,timeout=30) as r:
                    body=json.loads(r.read().decode('utf-8'))
                if int(body.get('retCode',0))!=0:
                    raise RuntimeError(f"Bybit retCode={body.get('retCode')} retMsg={body.get('retMsg')}")
                if self.cfg.request_delay_ms: time.sleep(self.cfg.request_delay_ms/1000)
                return body
            except Exception as e:
                last=e
                if attempt>=self.cfg.retries: break
                time.sleep(min(8,0.75*(2**attempt)))
        raise RuntimeError(f"REST {url}: {last}")


def _num(x):
    try:
        v=float(x)
        return int(v) if float(v).is_integer() else v
    except: return None

def _write_gz(path:Path,rows:list[dict],meta:dict):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    with gzip.open(tmp,'wt',encoding='utf-8') as f:
        for r in rows: f.write(json.dumps(r,separators=(',',':'))+'\n')
    tmp.replace(path)
    path.with_suffix(path.suffix+'.meta.json').write_text(json.dumps(meta,indent=2)+'\n',encoding='utf-8')

def _read_gz(path:Path)->pd.DataFrame:
    if not path.exists(): return pd.DataFrame()
    rows=[]
    with gzip.open(path,'rt',encoding='utf-8') as f:
        for line in f:
            if line.strip(): rows.append(json.loads(line))
    return pd.DataFrame(rows)

def _day_bounds(day:str)->tuple[int,int]:
    dt=datetime.strptime(day,'%Y-%m-%d').replace(tzinfo=timezone.utc)
    s=int(dt.timestamp()*1000); return s,s+DAY_MS-1

def days_between(start_ms:int,end_ms:int):
    d0=datetime.fromtimestamp(start_ms/1000,tz=timezone.utc).date()
    d1=datetime.fromtimestamp(end_ms/1000,tz=timezone.utc).date()
    d=d0
    while d<=d1:
        yield d.isoformat(); d+=timedelta(days=1)

class RestDayCache:
    SERIES={
        'spot_1m':('kline','startMs'), 'perp_1m':('kline','startMs'), 'mark_1m':('kline','startMs'),
        'index_1m':('kline','startMs'), 'premium_1m':('kline','startMs'),
        'open_interest_5m':('oi','timestampMs'), 'long_short_5m':('ls','timestampMs'), 'funding':('funding','timestampMs'),
    }
    def __init__(self,root:Path,cfg:RestConfig|None=None,offline:bool=False):
        self.cfg=cfg or RestConfig(); self.offline=offline
        # Cache namespace includes symbols, otherwise BTCUSDC and BTCUSDT research could silently mix.
        safe=lambda x: ''.join(ch if ch.isalnum() or ch in ('-','_') else '_' for ch in str(x))
        self.root=Path(root)/f'{safe(self.cfg.spot_symbol)}__{safe(self.cfg.perp_symbol)}'
        self.client=BybitPublicRest(self.cfg)
        self.root.mkdir(parents=True,exist_ok=True)

    def path(self,series:str,day:str)->Path: return self.root/series/f'{day}.jsonl.gz'

    def _fresh_enough(self,path:Path,day:str)->bool:
        if not path.exists(): return False
        # Closed UTC days are immutable. Current UTC day is refreshed if cache is older than 5 min.
        today=datetime.now(timezone.utc).date().isoformat()
        if day<today: return True
        return time.time()-path.stat().st_mtime<300

    def ensure_day(self,series:str,day:str)->Path:
        if series not in self.SERIES: raise KeyError(series)
        p=self.path(series,day)
        if self._fresh_enough(p,day): return p
        if self.offline:
            if p.exists(): return p
            raise RuntimeError(f"Brak cache offline: {series} {day}")
        s,e=_day_bounds(day); now=int(time.time()*1000); e=min(e,now)
        if e<s:
            _write_gz(p,[],{'series':series,'day':day,'future':True,'fetched_at':datetime.now(timezone.utc).isoformat()}); return p
        print(f"    [REST] {series} {day}")
        rows=self._fetch(series,s,e)
        _write_gz(p,rows,{'series':series,'day':day,'rows':len(rows),'start_ms':s,'end_ms':e,'fetched_at':datetime.now(timezone.utc).isoformat(),
                          'spot_symbol':self.cfg.spot_symbol,'perp_symbol':self.cfg.perp_symbol,'base_url':self.cfg.base_url})
        return p

    def ensure_range(self,series:str,start_ms:int,end_ms:int):
        return [self.ensure_day(series,d) for d in days_between(start_ms,end_ms)]

    def read_range(self,series:str,start_ms:int,end_ms:int)->pd.DataFrame:
        frames=[]
        for d in days_between(start_ms,end_ms):
            df=_read_gz(self.ensure_day(series,d))
            if not df.empty: frames.append(df)
        if not frames: return pd.DataFrame()
        df=pd.concat(frames,ignore_index=True)
        tsf=self.SERIES[series][1]
        if tsf not in df: return pd.DataFrame()
        df[tsf]=pd.to_numeric(df[tsf],errors='coerce')
        return df[(df[tsf]>=start_ms)&(df[tsf]<=end_ms)].sort_values(tsf).drop_duplicates(tsf,keep='last').reset_index(drop=True)

    def ensure_feature_day(self,day:str):
        for s in self.SERIES: self.ensure_day(s,day)

    def _fetch(self,series:str,start_ms:int,end_ms:int)->list[dict]:
        if series in ('spot_1m','perp_1m','mark_1m','index_1m','premium_1m'):
            if series=='spot_1m': endpoint='/v5/market/kline'; category='spot'; symbol=self.cfg.spot_symbol; label='spot_trade_price'
            elif series=='perp_1m': endpoint='/v5/market/kline'; category='linear'; symbol=self.cfg.perp_symbol; label='perp_trade_price'
            elif series=='mark_1m': endpoint='/v5/market/mark-price-kline'; category='linear'; symbol=self.cfg.perp_symbol; label='mark_price'
            elif series=='index_1m': endpoint='/v5/market/index-price-kline'; category='linear'; symbol=self.cfg.perp_symbol; label='index_price'
            else: endpoint='/v5/market/premium-index-price-kline'; category='linear'; symbol=self.cfg.perp_symbol; label='premium_index'
            return self._fetch_klines(endpoint,category,symbol,label,start_ms,end_ms)
        if series=='open_interest_5m': return self._fetch_cursor('/v5/market/open-interest',{'category':'linear','symbol':self.cfg.perp_symbol,'intervalTime':'5min','startTime':start_ms,'endTime':end_ms,'limit':200},'oi')
        if series=='long_short_5m': return self._fetch_cursor('/v5/market/account-ratio',{'category':'linear','symbol':self.cfg.perp_symbol,'period':'5min','startTime':start_ms,'endTime':end_ms,'limit':500},'ls')
        if series=='funding': return self._fetch_funding(start_ms,end_ms)
        raise KeyError(series)

    def _fetch_klines(self,endpoint,category,symbol,label,start_ms,end_ms):
        rows=[]; cursor_end=end_ms
        while cursor_end>=start_ms:
            body=self.client.get(endpoint,{'category':category,'symbol':symbol,'interval':'1','start':start_ms,'end':cursor_end,'limit':1000})
            arr=body.get('result',{}).get('list') or []
            if not arr: break
            parsed=[]
            for x in arr:
                if not x: continue
                t=int(x[0]);
                if t<start_ms or t>end_ms: continue
                r={'series':label,'category':category,'symbol':symbol,'interval':'1','startMs':t,'open':_num(x[1]),'high':_num(x[2]),'low':_num(x[3]),'close':_num(x[4])}
                if len(x)>5:r['volume']=_num(x[5])
                if len(x)>6:r['turnover']=_num(x[6])
                parsed.append(r)
            if not parsed: break
            rows.extend(parsed); earliest=min(r['startMs'] for r in parsed)
            if earliest<=start_ms: break
            if earliest>=cursor_end: raise RuntimeError(f'{label}: pagination did not move')
            cursor_end=earliest-1
        return list({r['startMs']:r for r in rows}.values()) if not rows else sorted({r['startMs']:r for r in rows}.values(),key=lambda r:r['startMs'])

    def _fetch_cursor(self,endpoint,params,kind):
        rows=[]; cursor=None; seen=set()
        while True:
            p=dict(params); p['cursor']=cursor
            body=self.client.get(endpoint,p); result=body.get('result',{}); arr=result.get('list') or []
            for x in arr:
                ts=_num(x.get('timestamp'))
                if ts is None: continue
                if kind=='oi': rows.append({'timestampMs':int(ts),'openInterest':_num(x.get('openInterest')),'singleOpenInterest':_num(x.get('singleOpenInterest'))})
                else: rows.append({'timestampMs':int(ts),'buyRatio':_num(x.get('buyRatio')),'sellRatio':_num(x.get('sellRatio'))})
            nxt=result.get('nextPageCursor') or None
            if not nxt: break
            if nxt in seen: raise RuntimeError(f'{kind}: cursor loop')
            seen.add(nxt); cursor=nxt
        s=int(params['startTime']);e=int(params['endTime'])
        return sorted({r['timestampMs']:r for r in rows if s<=r['timestampMs']<=e}.values(),key=lambda r:r['timestampMs'])

    def _fetch_funding(self,start_ms,end_ms):
        rows=[]; cursor_end=end_ms
        while cursor_end>=start_ms:
            body=self.client.get('/v5/market/funding/history',{'category':'linear','symbol':self.cfg.perp_symbol,'startTime':start_ms,'endTime':cursor_end,'limit':200})
            arr=body.get('result',{}).get('list') or []
            if not arr: break
            parsed=[]
            for x in arr:
                ts=_num(x.get('fundingRateTimestamp'))
                if ts is None or not(start_ms<=ts<=end_ms): continue
                parsed.append({'timestampMs':int(ts),'fundingRate':_num(x.get('fundingRate'))})
            if not parsed: break
            rows.extend(parsed); earliest=min(r['timestampMs'] for r in parsed)
            if earliest<=start_ms: break
            if earliest>=cursor_end: break
            cursor_end=earliest-1
        return sorted({r['timestampMs']:r for r in rows}.values(),key=lambda r:r['timestampMs'])
