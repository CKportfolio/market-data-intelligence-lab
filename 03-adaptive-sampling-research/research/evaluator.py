from __future__ import annotations
import math
from dataclasses import dataclass
import numpy as np
import pandas as pd


def percentile(v, p, default=math.nan):
    return float(np.percentile(v, p)) if len(v) else default


@dataclass(frozen=True)
class Episode:
    start_ms: int
    end_ms: int
    tier: str
    points: int


def event_masks(df: pd.DataFrame, cal: dict):
    """Tiered proxy ground truth.

    A: price/structure event (range OR tick travel).
    B: non-price microstructure confluence: at least one FLOW sensor AND one BOOK sensor.
    C: single-family / single-sensor event; diagnostic only, never a PASS gate.
    """
    e=cal['event_thresholds']
    price_range=(df.fast_price_range_bp>=e['price_range_bp']).to_numpy(bool)
    tick_travel=(df.fast_tick_travel_bp>=e['tick_travel_bp']).to_numpy(bool)
    trade=(df.fast_trade_rate_s>=e['trade_rate_s']).to_numpy(bool)
    volume=(df.fast_quote_volume_s>=e['quote_volume_s']).to_numpy(bool)
    churn=(df.fast_book_churn_s>=e['book_churn_s']).to_numpy(bool)
    imbalance=(df.fast_imbalance_delta>=e['imbalance_delta']).to_numpy(bool)
    price=price_range | tick_travel
    flow=trade | volume
    book=churn | imbalance
    cross=flow & book
    tier_a=price
    tier_b=(~price) & cross
    tier_c=(~price) & (~cross) & (flow | book)
    absorption=tier_b & (df.fast_price_range_bp.to_numpy(float) < float(e['price_range_bp'])*0.60)
    return {
        'tier_a':tier_a,'tier_b':tier_b,'tier_c':tier_c,
        'price_range':price_range,'tick_travel':tick_travel,
        'trade':trade,'volume':volume,'churn':churn,'imbalance':imbalance,
        'absorption':absorption,
    }


def _cluster_mask(mask: np.ndarray, ts: np.ndarray, gap_ms: int) -> list[tuple[int,int,int,int]]:
    idx=np.flatnonzero(mask)
    if len(idx)==0: return []
    out=[]; start_i=prev_i=int(idx[0]); points=1
    for raw_i in idx[1:]:
        i=int(raw_i)
        if int(ts[i])-int(ts[prev_i])>gap_ms:
            out.append((start_i,prev_i,int(ts[start_i]),int(ts[prev_i])))
            start_i=i; points=1
        else:
            points+=1
        prev_i=i
    out.append((start_i,prev_i,int(ts[start_i]),int(ts[prev_i])))
    return out


def build_ground_truth(df: pd.DataFrame, cal: dict, cfg: dict):
    ev=cfg.get('evaluation',{}); gap=int(ev.get('episode_gap_ms',1500)); context=int(ev.get('event_context_ms',1000))
    ts=df.ts_ms.to_numpy(dtype='int64'); masks=event_masks(df,cal)
    important=masks['tier_a'] | masks['tier_b']
    episodes=[]
    for i0,i1,s,e in _cluster_mask(important,ts,gap):
        # Priority A: if a price event appears anywhere in a clustered episode, the whole episode is Tier A.
        tier='A' if masks['tier_a'][i0:i1+1].any() else 'B'
        episodes.append(Episode(s,e,tier,int(important[i0:i1+1].sum())))
    c_eps=[]
    for i0,i1,s,e in _cluster_mask(masks['tier_c'],ts,gap):
        # Keep Tier C purely diagnostic and don't duplicate time already inside an important A/B episode.
        overlaps=any(not (e < ep.start_ms or s > ep.end_ms) for ep in episodes)
        if not overlaps:
            c_eps.append(Episode(s,e,'C',int(masks['tier_c'][i0:i1+1].sum())))
    context_mask=np.zeros(len(df),dtype=bool)
    for ep in episodes:
        context_mask |= (ts>=ep.start_ms-context) & (ts<=ep.end_ms+context)
    return {'episodes':episodes,'tier_c_episodes':c_eps,'context_mask':context_mask,'masks':masks,'ts':ts}


def _align_decisions(df: pd.DataFrame, dec: pd.DataFrame) -> pd.DataFrame:
    if len(df)==len(dec) and np.array_equal(df.ts_ms.to_numpy(dtype='int64'),dec.ts_ms.to_numpy(dtype='int64')):
        return dec.reset_index(drop=True)
    return df[['ts_ms']].merge(dec,on='ts_ms',how='left').fillna({'interval_ms':5000,'snapshot':0})


def _episode_capture(ep: Episode, ts: np.ndarray, interval: np.ndarray, snapshot: np.ndarray, required: int, grace_ms: int):
    inside=(ts>=ep.start_ms)&(ts<=ep.end_ms)
    qualifying=inside & (interval<=required)
    hit_idx=np.flatnonzero(qualifying)
    covered=len(hit_idx)>0
    reaction=max(0,int(ts[hit_idx[0]]-ep.start_ms)) if covered else None
    pre=(ts>=ep.start_ms-1000)&(ts<ep.start_ms)&(interval<=required)
    prearmed=bool(pre.any())
    snapwin=(ts>=ep.start_ms)&(ts<=ep.end_ms+grace_ms)&(interval<=required)&(snapshot>0)
    snap_idx=np.flatnonzero(snapwin)
    snap_covered=len(snap_idx)>0
    snap_reaction=max(0,int(ts[snap_idx[0]]-ep.start_ms)) if snap_covered else None
    return covered,reaction,prearmed,snap_covered,snap_reaction


def _find_stable_release(ts: np.ndarray, interval: np.ndarray, start_ms: int, stop_ms: int, slow_interval: int, stable_ms: int, tick_ms: int):
    idx=np.flatnonzero((ts>start_ms)&(ts<stop_ms)&(interval>=slow_interval))
    if len(idx)==0: return None
    need=max(1,int(math.ceil(stable_ms/max(tick_ms,1))))
    run_start=None; run_len=0; prev=None
    for raw_i in idx:
        i=int(raw_i)
        if prev is not None and int(ts[i])-int(ts[prev])<=tick_ms*1.5:
            run_len+=1
        else:
            run_start=i; run_len=1
        if run_len>=need:
            return int(ts[run_start])
        prev=i
    return None


def _oversampling(episodes: list[Episode], ts: np.ndarray, interval: np.ndarray, cfg: dict):
    ev=cfg.get('evaluation',{}); slow=int(ev.get('oversampling_slow_interval_ms',2500)); stable=int(ev.get('oversampling_stable_ms',1000)); horizon=int(ev.get('oversampling_horizon_ms',30000)); tick=int(cfg.get('replay',{}).get('tick_ms',250))
    vals=[]; censored_end=0; ended_by_next=0; released=0; horizon_hits=0
    data_end=int(ts[-1]) if len(ts) else 0
    for j,ep in enumerate(episodes):
        next_start=episodes[j+1].start_ms if j+1<len(episodes) else None
        natural_stop=ep.end_ms+horizon+tick
        stop=min(natural_stop, next_start if next_start is not None else natural_stop, data_end+tick)
        rel=_find_stable_release(ts,interval,ep.end_ms,stop,slow,stable,tick)
        if rel is not None:
            vals.append(max(0,rel-ep.end_ms)); released+=1; continue
        if next_start is not None and next_start < natural_stop and next_start <= data_end:
            vals.append(max(0,next_start-ep.end_ms)); ended_by_next+=1; continue
        if data_end >= ep.end_ms+horizon:
            vals.append(horizon); horizon_hits+=1; continue
        censored_end+=1
    return vals, {'oversampling_observed_count':len(vals),'oversampling_censored_dataset_end':censored_end,'oversampling_ended_by_next_event':ended_by_next,'oversampling_released_count':released,'oversampling_horizon_hit_count':horizon_hits}


def prepare_ground_truth(df: pd.DataFrame, cal: dict, cfg: dict):
    gt=build_ground_truth(df,cal,cfg)
    ts=gt['ts']; duration=(int(ts[-1])-int(ts[0])) if len(ts)>1 else 0
    gt['fixed1s_snapshot_count']=max(1,int(math.floor(duration/1000))+1)
    return gt


def evaluate_prepared(df: pd.DataFrame, dec: pd.DataFrame, cal: dict, cfg: dict, gt: dict):
    d=_align_decisions(df,dec); ts=gt['ts']; interval=pd.to_numeric(d.interval_ms,errors='coerce').fillna(5000).to_numpy(float); snapshot=pd.to_numeric(d.snapshot,errors='coerce').fillna(0).to_numpy(float)
    ev=cfg.get('evaluation',{}); req={'A':int(ev.get('tier_a_required_interval_ms',800)),'B':int(ev.get('tier_b_required_interval_ms',1500)),'C':int(ev.get('tier_c_required_interval_ms',2500))}; grace=int(ev.get('snapshot_grace_ms',750))
    buckets={'A':[],'B':[],'C':[]}
    for ep in gt['episodes']: buckets[ep.tier].append(ep)
    buckets['C']=gt['tier_c_episodes']
    stats={}
    all_reactions=[]
    for tier,eps in buckets.items():
        cap=[]; reactions=[]; pre=[]; snapcap=[]; snapre=[]
        for ep in eps:
            c,r,p,sc,sr=_episode_capture(ep,ts,interval,snapshot,req[tier],grace)
            cap.append(c); pre.append(p); snapcap.append(sc)
            if r is not None: reactions.append(r); all_reactions.append(r)
            if sr is not None: snapre.append(sr)
        n=len(eps)
        stats[tier]={
            'count':n,
            'recall':float(sum(cap)/n) if n else 1.0,
            'snapshot_recall':float(sum(snapcap)/n) if n else 1.0,
            'prearmed_recall':float(sum(pre)/n) if n else 1.0,
            'reaction_p95_ms':percentile(reactions,95,0),
            'snapshot_reaction_p95_ms':percentile(snapre,95,0),
        }
    weights=ev.get('tier_weights',{'A':2.0,'B':1.0}); wa=float(weights.get('A',2)); wb=float(weights.get('B',1)); denom=wa*stats['A']['count']+wb*stats['B']['count']
    weighted=(wa*stats['A']['recall']*stats['A']['count']+wb*stats['B']['recall']*stats['B']['count'])/denom if denom else 1.0
    overs,over_meta=_oversampling(gt['episodes'],ts,interval,cfg)
    fast=(interval<=int(ev.get('tier_b_required_interval_ms',1500)))
    outside=~gt['context_mask']; false_fast=float((fast & outside).sum()/max(1,outside.sum()))
    snaps=int((snapshot>0).sum()); baseline=int(gt['fixed1s_snapshot_count']); reduction=1-snaps/max(1,baseline)
    # Reconstruction diagnostics: hold last sampled orderbook values.
    x=df.copy()
    sampled=(snapshot>0)
    mid_sample=pd.Series(np.where(sampled,pd.to_numeric(x['mid'],errors='coerce'),np.nan)).ffill()
    denom_mid=pd.to_numeric(x['mid'],errors='coerce').replace(0,np.nan)
    mid_err=((mid_sample-denom_mid).abs()/denom_mid*10000).replace([np.inf,-np.inf],np.nan)
    imb_sample=pd.Series(np.where(sampled,pd.to_numeric(x['imbalance'],errors='coerce'),np.nan)).ffill()
    imb_err=(imb_sample-pd.to_numeric(x['imbalance'],errors='coerce')).abs()
    metrics={
      'tier_a_event_count':stats['A']['count'],'tier_b_event_count':stats['B']['count'],'tier_c_event_count':stats['C']['count'],
      'tier_a_recall':stats['A']['recall'],'tier_b_recall':stats['B']['recall'],'tier_c_recall':stats['C']['recall'],
      'tier_a_snapshot_recall':stats['A']['snapshot_recall'],'tier_b_snapshot_recall':stats['B']['snapshot_recall'],'tier_c_snapshot_recall':stats['C']['snapshot_recall'],
      'tier_a_prearmed_recall':stats['A']['prearmed_recall'],'tier_b_prearmed_recall':stats['B']['prearmed_recall'],
      'ab_weighted_recall':float(weighted),
      'reaction_median_ms':percentile(all_reactions,50,0),'reaction_p95_ms':percentile(all_reactions,95,0),
      'tier_a_reaction_p95_ms':stats['A']['reaction_p95_ms'],'tier_b_reaction_p95_ms':stats['B']['reaction_p95_ms'],
      'oversampling_median_ms':percentile(overs,50,0),'oversampling_p95_ms':percentile(overs,95,0),
      **over_meta,
      'false_fast_time':false_fast,'fast_time_fraction':float(fast.mean()) if len(fast) else 0.0,
      'snapshot_count':snaps,'fixed1s_snapshot_count':baseline,'snapshot_reduction':float(reduction),
      'mid_reconstruction_mae_bp':float(mid_err.mean(skipna=True)),'imbalance_reconstruction_mae':float(imb_err.mean(skipna=True)),
    }
    ac=cfg.get('acceptance',{})
    checks={
      'tier_a_recall':metrics['tier_a_recall']>=float(ac.get('tier_a_recall_min',0)),
      'tier_b_recall':metrics['tier_b_recall']>=float(ac.get('tier_b_recall_min',0)),
      'ab_weighted_recall':metrics['ab_weighted_recall']>=float(ac.get('ab_weighted_recall_min',0)),
      'tier_a_snapshot_recall':metrics['tier_a_snapshot_recall']>=float(ac.get('tier_a_snapshot_recall_min',0)),
      'tier_b_snapshot_recall':metrics['tier_b_snapshot_recall']>=float(ac.get('tier_b_snapshot_recall_min',0)),
      'reaction':metrics['reaction_p95_ms']<=float(ac.get('reaction_p95_ms_max',1e18)),
      'oversampling':metrics['oversampling_p95_ms']<=float(ac.get('oversampling_p95_ms_max',1e18)),
      'false_fast':metrics['false_fast_time']<=float(ac.get('false_fast_time_max',1)),
      'reduction':metrics['snapshot_reduction']>=float(ac.get('snapshot_reduction_min',-1)),
    }
    metrics['gates_passed']=int(sum(bool(v) for v in checks.values())); metrics['gate_count']=len(checks); metrics['pass']=all(checks.values()); metrics['checks']=checks
    return metrics


def evaluate(df: pd.DataFrame, dec: pd.DataFrame, cal: dict, cfg: dict):
    return evaluate_prepared(df,dec,cal,cfg,prepare_ground_truth(df,cal,cfg))


def episodes_frame(df: pd.DataFrame, cal: dict, cfg: dict) -> pd.DataFrame:
    gt=prepare_ground_truth(df,cal,cfg); rows=[]
    for ep in gt['episodes']+gt['tier_c_episodes']:
        rows.append({'start_ms':ep.start_ms,'end_ms':ep.end_ms,'tier':ep.tier,'points':ep.points,'duration_ms':ep.end_ms-ep.start_ms})
    return pd.DataFrame(rows).sort_values(['start_ms','tier']).reset_index(drop=True) if rows else pd.DataFrame(columns=['start_ms','end_ms','tier','points','duration_ms'])
