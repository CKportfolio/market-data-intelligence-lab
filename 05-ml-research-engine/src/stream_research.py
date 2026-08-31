from __future__ import annotations
from datetime import datetime,timezone
from pathlib import Path
import json
import numpy as np
import pandas as pd
from .dataset import load_enriched_window
from .features import add_time_series_features
from .candidates import detect_candidates, attach_features
from .labels import label_candidates_by_time
from .rest_cache import RestDayCache, DAY_MS, days_between


def day_start_ms(day:str)->int:
    return int(datetime.strptime(day,'%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp()*1000)


def context_minutes(profiles:list[dict],lookbacks:list[int])->int:
    max_gap=max(int(x['max_gap']) for x in profiles)
    max_confirm=max(int(x['confirm']) for x in profiles)
    # triple formation can span two max gaps; feature rolling needs its own left context
    return max(max(lookbacks)+10,2*max_gap+2*max_confirm+10)


def spot_price_range(cache:RestDayCache,start_ms:int,end_ms:int)->pd.DataFrame:
    d=cache.read_range('spot_1m',start_ms,end_ms)
    if d.empty:return pd.DataFrame(columns=['minute_ms','open','high','low','close','price_missing'])
    d['minute_ms']=(pd.to_numeric(d['startMs'],errors='coerce').astype('int64')//60000)*60000
    cols=[c for c in ['minute_ms','open','high','low','close','volume','turnover'] if c in d]
    d=d[cols].sort_values('minute_ms').drop_duplicates('minute_ms',keep='last')
    grid=pd.DataFrame({'minute_ms':np.arange((start_ms//60000)*60000,(end_ms//60000)*60000+1,60000,dtype='int64')})
    out=grid.merge(d,on='minute_ms',how='left');out['price_missing']=out['close'].isna().astype(int)
    return out.reset_index(drop=True)


def build_candidate_parts(enriched_dir:Path,cache:RestDayCache,out_dir:Path,raw_start_ms:int,raw_end_ms:int,
                          profiles:list[dict],lookbacks:list[int],targets:list[dict])->dict:
    out_dir.mkdir(parents=True,exist_ok=True)
    ctx=context_minutes(profiles,lookbacks); max_h=max(int(t['horizon_min']) for t in targets)
    profile_order=[x['name'] for x in profiles]
    all_features=[]; total=0; parts=[]; counts={}
    for day in days_between(raw_start_ms,raw_end_ms):
        ds=day_start_ms(day); de=ds+DAY_MS-60000
        emit_s=max(ds,raw_start_ms); emit_e=min(de,raw_end_ms)
        if emit_e<emit_s:continue
        ws=max(0,emit_s-ctx*60000); we=emit_e
        frame=load_enriched_window(enriched_dir,ws,we)
        if frame.empty: continue
        feat,ts_features=add_time_series_features(frame,lookbacks)
        cand=detect_candidates(feat,profiles)
        if cand.empty:
            print(f'  [CAND] {day}: 0'); continue
        cand,feature_cols=attach_features(cand,feat,ts_features,profile_order=profile_order)
        cand=cand[(cand.signal_ms>=emit_s)&(cand.signal_ms<=emit_e)].copy()
        if cand.empty:
            print(f'  [CAND] {day}: 0'); continue
        cand['signal_available_ms']=cand['signal_ms'].astype('int64')+60000
        # signal_idx is local chunk position and must never be used outside this chunk
        cand.drop(columns=['signal_idx'],inplace=True,errors='ignore')
        price=spot_price_range(cache,emit_s,min(raw_end_ms+max_h*60000,emit_e+max_h*60000))
        cand=label_candidates_by_time(cand,price,targets)
        p=out_dir/f'{day}.csv.gz'; cand.to_csv(p,index=False,compression='gzip');parts.append(p)
        all_features.extend(feature_cols); total+=len(cand)
        vc=cand['formation'].value_counts().to_dict()
        for k,v in vc.items(): counts[k]=counts.get(k,0)+int(v)
        print(f'  [CAND] {day}: {len(cand)}')
    features=list(dict.fromkeys(all_features))
    return {'candidate_rows':total,'parts':[str(x) for x in parts],'features':features,'formation_counts':counts,'context_minutes':ctx}


def load_candidate_parts(parts_dir:Path)->pd.DataFrame:
    frames=[pd.read_csv(p) for p in sorted(parts_dir.glob('*.csv.gz'))]
    if not frames:return pd.DataFrame()
    d=pd.concat(frames,ignore_index=True).sort_values('signal_ms').reset_index(drop=True)
    # Defensive dedup across day/chunk boundaries
    keys=[c for c in ['signal_ms','direction','formation'] if c in d]
    if keys:d=d.drop_duplicates(keys,keep='first').reset_index(drop=True)
    return d


def write_price_replay(cache:RestDayCache,path:Path,start_ms:int,end_ms:int):
    p=spot_price_range(cache,start_ms,end_ms);path.parent.mkdir(parents=True,exist_ok=True)
    p['ts']=pd.to_datetime(p['minute_ms'],unit='ms',utc=True)
    p.to_csv(path,index=False,compression='gzip');return len(p)


def mark_independent_episodes(candidates:pd.DataFrame,gap_min:int=15)->pd.DataFrame:
    """Conservative de-dup for evaluation: nearby same-direction signals are one market episode.
    The first signal is representative, so no future candidate is used to choose the representative.
    """
    if candidates.empty:return candidates.copy()
    d=candidates.sort_values(['signal_ms','direction']).copy(); d['episode_id']='';d['is_episode_first']=0
    counters={1:0,-1:0};last={1:None,-1:None};ids={}
    for idx,r in d.iterrows():
        direction=int(r['direction']);ts=int(r['signal_ms']);prev=last.get(direction)
        if prev is None or ts-prev>gap_min*60000:
            counters[direction]=counters.get(direction,0)+1
            ids[direction]=f"{'L' if direction==1 else 'S'}{counters[direction]:07d}"
            d.at[idx,'is_episode_first']=1
        d.at[idx,'episode_id']=ids[direction];last[direction]=ts
    return d.sort_values('signal_ms').reset_index(drop=True)


def historical_feature_subset(feature_cols:list[str])->list[str]:
    """Features reproducible from cheap historical REST/price data. Used only for ablation."""
    micro_prefix=('spot_delta','perp_delta','spot_trade_count','perp_trade_count','spot_volume','perp_volume','spot_ob_','perp_ob_','liq_')
    geom={'direction_long','formation_triple','profile_code','confirm_bars','gap_1_min','gap_2_min','level_deviation_bps','rebound_bps'}
    return [f for f in feature_cols if f in geom or not f.startswith(micro_prefix)]
