from __future__ import annotations
from collections import defaultdict
from pathlib import Path
import numpy as np
import pandas as pd
from .archive_stream import iter_archive_rows, record_timestamp, ArchiveInfo


def _ob_snapshot_stats(row: dict) -> dict:
    bids = row.get('bids') or []
    asks = row.get('asks') or []
    mid = float(row.get('mid') or 0.0)
    best_bid = float(row.get('bestBid') or (bids[0][0] if bids else 0.0))
    best_ask = float(row.get('bestAsk') or (asks[0][0] if asks else 0.0))
    bid_qty = float(sum(float(x[1]) for x in bids if len(x)>=2))
    ask_qty = float(sum(float(x[1]) for x in asks if len(x)>=2))
    denom = bid_qty + ask_qty
    imbalance = (bid_qty-ask_qty)/denom if denom>0 else np.nan
    bq1 = float(bids[0][1]) if bids else 0.0
    aq1 = float(asks[0][1]) if asks else 0.0
    micro = np.nan
    if best_bid and best_ask and bq1+aq1>0:
        micro = (best_ask*bq1 + best_bid*aq1)/(bq1+aq1)
    out = {
        'spread_bps': float(row.get('spreadBps')) if row.get('spreadBps') is not None else ((best_ask-best_bid)/mid*10000 if mid else np.nan),
        'bid_qty_l50': bid_qty, 'ask_qty_l50': ask_qty, 'imbalance_l50': imbalance,
        'micro_dev_bps': ((micro/mid)-1)*10000 if mid>0 and np.isfinite(micro) else np.nan,
    }
    for band in (5,10,25,50):
        if mid<=0:
            out[f'bid_qty_{band}bps']=out[f'ask_qty_{band}bps']=out[f'imbalance_{band}bps']=np.nan
            continue
        lo,hi=mid*(1-band/10000),mid*(1+band/10000)
        b=sum(float(q) for p,q in bids if float(p)>=lo)
        a=sum(float(q) for p,q in asks if float(p)<=hi)
        d=b+a
        out[f'bid_qty_{band}bps']=b; out[f'ask_qty_{band}bps']=a; out[f'imbalance_{band}bps']=(b-a)/d if d>0 else np.nan
    return out


def consume_row(agg: dict[int,dict], row: dict):
    ch=row.get('_channel')
    ts=record_timestamp(row)
    if ts is None: return
    minute=(int(ts)//60000)*60000
    a=agg.setdefault(minute,{})
    if ch in ('spot_trades','perp_trades'):
        p='spot' if ch=='spot_trades' else 'perp'
        side=str(row.get('side') or '').lower(); size=float(row.get('size') or 0); price=float(row.get('price') or 0)
        a[f'{p}_trade_count']=a.get(f'{p}_trade_count',0)+1
        a[f'{p}_volume']=a.get(f'{p}_volume',0.0)+size
        a[f'{p}_turnover']=a.get(f'{p}_turnover',0.0)+size*price
        if side=='buy': a[f'{p}_buy_volume']=a.get(f'{p}_buy_volume',0.0)+size
        elif side=='sell': a[f'{p}_sell_volume']=a.get(f'{p}_sell_volume',0.0)+size
    elif ch in ('spot_orderbook','perp_orderbook'):
        p='spot_ob' if ch=='spot_orderbook' else 'perp_ob'; st=_ob_snapshot_stats(row)
        n=a.get(f'{p}_samples',0)+1; a[f'{p}_samples']=n
        for k,v in st.items():
            if not np.isfinite(v): continue
            a[f'{p}_{k}_sum']=a.get(f'{p}_{k}_sum',0.0)+float(v)
            a[f'{p}_{k}_last']=float(v)
            a[f'{p}_{k}_min']=min(a.get(f'{p}_{k}_min',float(v)),float(v))
            a[f'{p}_{k}_max']=max(a.get(f'{p}_{k}_max',float(v)),float(v))
    elif ch=='liquidations':
        side=str(row.get('positionSide') or '').lower(); size=float(row.get('size') or 0)
        a['liq_count']=a.get('liq_count',0)+1; a['liq_volume']=a.get('liq_volume',0.0)+size
        if side=='buy': a['liq_buy_volume']=a.get('liq_buy_volume',0.0)+size
        elif side=='sell': a['liq_sell_volume']=a.get('liq_sell_volume',0.0)+size


def finalize_minutes(agg: dict[int,dict], minutes: list[int] | None=None) -> pd.DataFrame:
    keys=sorted(minutes if minutes is not None else agg.keys())
    out=[]
    for minute in keys:
        if minute not in agg: continue
        row={'minute_ms':minute,**agg[minute]}
        for p in ('spot_ob','perp_ob'):
            n=row.get(f'{p}_samples',0)
            if n:
                for k in list(row):
                    if k.startswith(p+'_') and k.endswith('_sum'):
                        row[k[:-4]+'_mean']=row[k]/n
        out.append(row)
    return pd.DataFrame(out).sort_values('minute_ms').reset_index(drop=True) if out else pd.DataFrame(columns=['minute_ms'])


def stream_archives_to_daily_micro(infos: list[ArchiveInfo], out_dir: Path, allow_dense: bool=False, progress_every: int=1_000_000) -> dict:
    """One sequential RAW pass. Keeps only a small minute dictionary and writes daily gzip partitions."""
    out_dir.mkdir(parents=True,exist_ok=True)
    agg: dict[int,dict]={}; rows=0; parts=[]; max_seen=None
    current_day=None

    def flush_days(before_day: int | None, force=False):
        nonlocal agg
        days=sorted(set(m//86_400_000 for m in agg))
        flush=[d for d in days if force or (before_day is not None and d<before_day)]
        for d in flush:
            mins=[m for m in agg if m//86_400_000==d]
            df=finalize_minutes(agg,mins)
            day=pd.to_datetime(d*86_400_000,unit='ms',utc=True).strftime('%Y-%m-%d')
            path=out_dir/f'{day}.csv.gz'
            # If a day was touched by multiple flushes, merge compact minute partitions only.
            if path.exists():
                old=pd.read_csv(path)
                df=pd.concat([old,df],ignore_index=True).groupby('minute_ms',as_index=False).last().sort_values('minute_ms')
            df.to_csv(path,index=False,compression='gzip')
            parts.append(str(path))
            for m in mins: agg.pop(m,None)

    for ai,info in enumerate(infos,1):
        print(f"  [RAW {ai}/{len(infos)}] {info.path.name}")
        if info.format=='dense_truth_v1' and not allow_dense:
            raise RuntimeError('Wskazano archiwum dense truth recordera. Do ML research uzyj data/raw/archives collectora; dense recorder sluzy do strojenia adaptive sampling.')
        for row in iter_archive_rows(info.path,allow_dense=allow_dense):
            rows+=1
            ts=record_timestamp(row)
            if ts is not None:
                max_seen=ts if max_seen is None else max(max_seen,ts)
            consume_row(agg,row)
            if progress_every and rows%progress_every==0: print(f"    {rows:,} raw records")
        # Keep current UTC day AND the previous day in RAM. This tolerates small timestamp
        # reordering around midnight without ever holding the whole corpus.
        flush_days((info.end_ms//86_400_000)-1)
    flush_days(None,force=True)
    return {'raw_records':rows,'partitions':sorted(set(parts)),'minutes':sum(len(pd.read_csv(p,usecols=['minute_ms'])) for p in sorted(set(parts)))}
