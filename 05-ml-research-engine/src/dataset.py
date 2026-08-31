from __future__ import annotations
from datetime import datetime, timezone, timedelta
from pathlib import Path
import numpy as np
import pandas as pd
from .rest_cache import RestDayCache, DAY_MS, days_between


def read_micro_day(micro_dir:Path,day:str)->pd.DataFrame:
    p=micro_dir/f'{day}.csv.gz'
    if not p.exists(): return pd.DataFrame(columns=['minute_ms'])
    d=pd.read_csv(p); d['minute_ms']=pd.to_numeric(d['minute_ms'],errors='coerce').astype('Int64')
    return d.dropna(subset=['minute_ms']).sort_values('minute_ms')


def _rest_to_minute(df:pd.DataFrame,ts_field:str)->pd.DataFrame:
    if df.empty or ts_field not in df: return pd.DataFrame(columns=['minute_ms'])
    d=df.copy(); d[ts_field]=pd.to_numeric(d[ts_field],errors='coerce'); d=d.dropna(subset=[ts_field])
    d['minute_ms']=(d[ts_field].astype('int64')//60000)*60000
    return d.sort_values(ts_field).drop_duplicates('minute_ms',keep='last')


def build_enriched_range(cache:RestDayCache,micro_dir:Path,start_ms:int,end_ms:int)->pd.DataFrame:
    """Build only requested compact minute window from partitioned cache + micro partitions."""
    # Read one prior minute so a rare missing first candle can be carried from the last known close.
    try:
        spot_raw=cache.read_range('spot_1m',max(0,start_ms-60000),end_ms)
    except RuntimeError:
        spot_raw=cache.read_range('spot_1m',start_ms,end_ms)
    spot=_rest_to_minute(spot_raw,'startMs')
    if spot.empty: return pd.DataFrame()
    keep=[c for c in ['minute_ms','open','high','low','close','volume','turnover'] if c in spot]
    spot=spot[keep].copy().rename(columns={'volume':'spot_kline_volume','turnover':'spot_kline_turnover'})
    grid=pd.DataFrame({'minute_ms':np.arange((start_ms//60000)*60000,(end_ms//60000)*60000+1,60000,dtype='int64')})
    base=grid.merge(spot,on='minute_ms',how='left')
    base['spot_kline_missing']=base['close'].isna().astype(int)
    last_close=pd.to_numeric(base['close'],errors='coerce').ffill()
    for c in ('open','high','low','close'):
        base[c]=pd.to_numeric(base[c],errors='coerce').fillna(last_close)
    for c in ('spot_kline_volume','spot_kline_turnover'):
        if c in base: base[c]=pd.to_numeric(base[c],errors='coerce').fillna(0.0)

    # micro can span a few day files; only compact minute rows are loaded
    micros=[]
    for day in days_between(start_ms,end_ms):
        m=read_micro_day(micro_dir,day)
        if not m.empty: micros.append(m)
    if micros:
        micro=pd.concat(micros,ignore_index=True).drop_duplicates('minute_ms',keep='last')
        micro=micro[(micro.minute_ms>=start_ms)&(micro.minute_ms<=end_ms)]
        base=base.merge(micro,on='minute_ms',how='left')

    specs=[
        ('perp_1m','startMs','perp',['close','volume','turnover']),
        ('mark_1m','startMs','mark',['close']),('index_1m','startMs','index',['close']),('premium_1m','startMs','premium',['close']),
        ('open_interest_5m','timestampMs','oi',['openInterest','singleOpenInterest']),
        ('long_short_5m','timestampMs','ls',['buyRatio','sellRatio']),('funding','timestampMs','funding',['fundingRate'])]
    base=base.sort_values('minute_ms')
    for series,tsf,prefix,cols in specs:
        coarse = series in ('open_interest_5m','long_short_5m','funding')
        rs=max(0,start_ms-DAY_MS) if coarse else start_ms
        try:
            raw=cache.read_range(series,rs,end_ms)
        except RuntimeError:
            raw=cache.read_range(series,start_ms,end_ms)
        d=_rest_to_minute(raw,tsf)
        if d.empty: continue
        use=['minute_ms']+[c for c in cols if c in d]
        d=d[use].rename(columns={c:f'{prefix}_{c}' for c in use if c!='minute_ms'}).sort_values('minute_ms')
        # backward/as-of: only a value whose timestamp is <= current minute is used
        base=pd.merge_asof(base,d,on='minute_ms',direction='backward')

    # deterministic base micro features
    for c in list(base.columns):
        if any(x in c for x in ('_count','_volume','_turnover')) and not c.startswith(('oi_','spot_kline')):
            base[c]=pd.to_numeric(base[c],errors='coerce').fillna(0.0)
    for p in ('spot','perp'):
        bv=pd.to_numeric(base.get(f'{p}_buy_volume',pd.Series(0.0,index=base.index)),errors='coerce').fillna(0)
        sv=pd.to_numeric(base.get(f'{p}_sell_volume',pd.Series(0.0,index=base.index)),errors='coerce').fillna(0)
        base[f'{p}_delta']=bv-sv
        base[f'{p}_delta_ratio']=(bv-sv)/(bv+sv).replace(0,np.nan)
        vol=pd.to_numeric(base.get(f'{p}_volume',pd.Series(0.0,index=base.index)),errors='coerce')
        turn=pd.to_numeric(base.get(f'{p}_turnover',pd.Series(0.0,index=base.index)),errors='coerce')
        base[f'{p}_trade_vwap']=turn/vol.replace(0,np.nan)
    if 'perp_close' in base: base['perp_spot_basis_bps']=(base['perp_close']/base['close']-1)*10000
    if 'mark_close' in base and 'index_close' in base: base['mark_index_basis_bps']=(base['mark_close']/base['index_close']-1)*10000
    if 'ls_buyRatio' in base and 'ls_sellRatio' in base: base['long_short_net']=base['ls_buyRatio']-base['ls_sellRatio']
    base['ts']=pd.to_datetime(base['minute_ms'],unit='ms',utc=True)
    base['available_ms']=base['minute_ms']+60000  # full 1m candle/aggregates known at close
    return base.reset_index(drop=True)


def save_enriched_daily_partitions(cache:RestDayCache,micro_dir:Path,out_dir:Path,start_ms:int,end_ms:int)->list[Path]:
    out_dir.mkdir(parents=True,exist_ok=True); out=[]
    for day in days_between(start_ms,end_ms):
        s=int(datetime.strptime(day,'%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp()*1000); e=min(s+DAY_MS-60000,end_ms)
        s=max(s,start_ms)
        if e<s: continue
        print(f'  [ENRICH] {day}')
        df=build_enriched_range(cache,micro_dir,s,e)
        p=out_dir/f'{day}.csv.gz'; df.to_csv(p,index=False,compression='gzip'); out.append(p)
    return out


def load_enriched_window(parts_dir:Path,start_ms:int,end_ms:int)->pd.DataFrame:
    frames=[]
    for day in days_between(start_ms,end_ms):
        p=parts_dir/f'{day}.csv.gz'
        if p.exists(): frames.append(pd.read_csv(p))
    if not frames:return pd.DataFrame()
    d=pd.concat(frames,ignore_index=True); d['minute_ms']=pd.to_numeric(d['minute_ms'],errors='coerce')
    d=d[(d.minute_ms>=start_ms)&(d.minute_ms<=end_ms)].sort_values('minute_ms').drop_duplicates('minute_ms',keep='last').reset_index(drop=True)
    d['ts']=pd.to_datetime(d['minute_ms'],unit='ms',utc=True)
    return d
