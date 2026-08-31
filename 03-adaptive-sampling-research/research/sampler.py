from __future__ import annotations
import math
import pandas as pd

COLS={
 'price_range':'fast_price_range_bp','tick_travel':'fast_tick_travel_bp','trade_rate':'fast_trade_rate_s',
 'quote_volume':'fast_quote_volume_s','book_churn':'fast_book_churn_s','imbalance_delta':'fast_imbalance_delta'}
PRICE=('price_range','tick_travel')
FLOW=('trade_rate','quote_volume')
BOOK=('book_churn','imbalance_delta')
EVENT_KEYS={
 'price_range':'price_range_bp','tick_travel':'tick_travel_bp','trade_rate':'trade_rate_s',
 'quote_volume':'quote_volume_s','book_churn':'book_churn_s','imbalance_delta':'imbalance_delta'}

def transform_ratio(ratio:float, dead:float, kind:str)->float:
    """Energy starts only after a family-specific dead-zone."""
    if ratio <= dead:
        return 0.0
    # Normalize so energy is continuous from zero at the dead-zone.
    e=max(0.0, ratio/dead - 1.0)
    if kind=='sqrt_excess': return math.sqrt(e)
    if kind=='linear_excess': return e
    return math.log1p(e)

def interval_from_heat(heat, interval_map):
    for x in sorted(interval_map,key=lambda z:float(z['min_heat']),reverse=True):
        if heat >= float(x['min_heat']): return x['name'],int(x['interval_ms'])
    x=interval_map[-1]; return x['name'],int(x['interval_ms'])

def _event_ratio(k:str,val:float,cal:dict)->float:
    ek=EVENT_KEYS[k]
    t=float(cal.get('event_thresholds',{}).get(ek,1e99))
    return val/max(t,1e-12)

def simulate(df:pd.DataFrame, cfg:dict, calibration:dict):
    """v0.4 asymmetric sampler.

    ATTACK:
      * price shock -> force 800ms on the same 250ms replay tick;
      * flow+book confluence -> force <=1500ms.
    SUSTAIN:
      * only deviations beyond a dead-zone add meaningful heat;
      * isolated flow/book deviations are heavily discounted.
    RELEASE:
      * when there is no price signal and no flow+book confluence, heat decays
        with a shorter tau (quiet_tau_multiplier).

    This deliberately separates "react fast" from "stay fast".
    """
    scfg=cfg['sampler']; hcfg=scfg['heat']; tick=int(cfg.get('replay',{}).get('tick_ms',250))
    tau=float(hcfg['tau_ms']); quiet_mult=float(hcfg.get('quiet_tau_multiplier',0.45)); gain=float(hcfg['gain'])
    fun=hcfg.get('function','log_excess'); bonus=float(hcfg.get('confluence_bonus',0.0)); single=float(hcfg.get('single_sensor_multiplier',0.12)); maxh=float(scfg.get('max_heat',8.0))
    weights=hcfg['weights']; base=calibration['baselines']; imap=scfg['interval_map']; dz=hcfg.get('dead_zone',{})
    shock_frac=hcfg.get('shock_event_fraction',{})
    heat=0.0; last_ts=None; last_snap=None; prev_interval=5000; out=[]
    for row in df.itertuples(index=False):
        ts=int(row.ts_ms); dt=tick if last_ts is None else max(1,ts-last_ts); last_ts=ts
        vals={k:float(getattr(row,c)) for k,c in COLS.items()}
        ratios={k: vals[k]/max(float(base.get(k,1e-9)),1e-9) for k in COLS}
        event_ratios={k:_event_ratio(k,vals[k],calibration) for k in COLS}

        price_present=any(ratios[k]>float(dz.get('price',1.15)) for k in PRICE)
        flow_present=any(ratios[k]>float(dz.get('flow',1.35)) for k in FLOW)
        book_present=any(ratios[k]>float(dz.get('book',1.35)) for k in BOOK)
        confluence=flow_present and book_present

        # Fast release is applied only outside a meaningful price/confluence state.
        effective_tau=tau if (price_present or confluence) else max(100.0,tau*quiet_mult)
        heat*=math.exp(-dt/effective_tau)

        contrib={}; energy=0.0
        for k in COLS:
            family='price' if k in PRICE else ('flow' if k in FLOW else 'book')
            z=transform_ratio(ratios[k],float(dz.get(family,1.0)),fun)
            mult=1.0
            # A lone flow/book sensor is useful diagnostically but should not sustain fast capture for long.
            if family in ('flow','book') and not confluence:
                mult=single
            v=float(weights.get(k,0.0))*z*mult
            contrib[k]=v; energy+=v
        if confluence:
            energy+=bonus
        heat=min(maxh,max(0.0,heat+gain*energy*(dt/1000.0)))
        regime,interval=interval_from_heat(heat,imap)

        # Shock channels use only information available on this same replay tick.
        price_shock=(event_ratios['price_range']>=float(shock_frac.get('price',0.9)) or event_ratios['tick_travel']>=float(shock_frac.get('price',0.9)))
        flow_shock=(event_ratios['trade_rate']>=float(shock_frac.get('flow',0.9)) or event_ratios['quote_volume']>=float(shock_frac.get('flow',0.9)))
        book_shock=(event_ratios['book_churn']>=float(shock_frac.get('book',0.9)) or event_ratios['imbalance_delta']>=float(shock_frac.get('book',0.9)))
        micro_shock=flow_shock and book_shock
        forced=''
        if price_shock and interval>800:
            interval=800; regime='EXTREME'; forced='price_shock'
        elif micro_shock and interval>1500:
            interval=1500; regime='ACTIVE'; forced='micro_shock'

        speedup=interval<prev_interval
        due=last_snap is None or (ts-last_snap)>=interval
        snap=bool(due or (scfg.get('snapshot_on_speedup',True) and speedup))
        reason=(forced+'+speedup') if forced and speedup and snap else ('speedup' if speedup and snap else ('due' if snap else forced))
        if snap:last_snap=ts
        out.append({'ts_ms':ts,'heat':heat,'regime':regime,'interval_ms':interval,'snapshot':int(snap),'reason':reason,'energy':energy,
                    'price_shock':int(price_shock),'micro_shock':int(micro_shock),'confluence':int(confluence),'effective_tau_ms':effective_tau,
                    **{f'c_{k}':v for k,v in contrib.items()}})
        prev_interval=interval
    return pd.DataFrame(out)

def simulate_fixed(df, interval_ms:int):
    last=None; out=[]
    for ts in df.ts_ms.astype('int64'):
        snap=last is None or ts-last>=interval_ms
        if snap:last=ts
        out.append({'ts_ms':int(ts),'heat':0.0,'regime':f'FIXED_{interval_ms}','interval_ms':int(interval_ms),'snapshot':int(snap),'reason':'due' if snap else '','energy':0.0})
    return pd.DataFrame(out)
