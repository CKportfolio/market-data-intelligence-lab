from __future__ import annotations
import numpy as np
import pandas as pd


def target_name(t: dict) -> str:
    return f"tp{int(t['tp_bps'])}_sl{int(t['sl_bps'])}_h{int(t['horizon_min'])}"


def label_candidates(candidates: pd.DataFrame, minute_df: pd.DataFrame, targets: list[dict]) -> pd.DataFrame:
    if candidates.empty:
        return candidates
    out = candidates.copy()
    highs = pd.to_numeric(minute_df["high"], errors="coerce").to_numpy(float)
    lows = pd.to_numeric(minute_df["low"], errors="coerce").to_numpy(float)
    closes = pd.to_numeric(minute_df["close"], errors="coerce").to_numpy(float)
    n = len(minute_df)

    max_h = max(int(t["horizon_min"]) for t in targets)
    mfe, mae = [], []
    for _, r in out.iterrows():
        i = int(r["signal_idx"])
        d = int(r["direction"])
        entry = closes[i]
        j2 = min(n - 1, i + max_h)
        if not np.isfinite(entry) or entry <= 0 or j2 <= i:
            mfe.append(np.nan); mae.append(np.nan); continue
        fh = highs[i+1:j2+1]
        fl = lows[i+1:j2+1]
        if d == 1:
            mfe.append((np.nanmax(fh) / entry - 1) * 10000)
            mae.append((1 - np.nanmin(fl) / entry) * 10000)
        else:
            mfe.append((1 - np.nanmin(fl) / entry) * 10000)
            mae.append((np.nanmax(fh) / entry - 1) * 10000)
    out[f"mfe_{max_h}m_bps"] = mfe
    out[f"mae_{max_h}m_bps"] = mae

    for t in targets:
        tp, sl, h = float(t["tp_bps"]), float(t["sl_bps"]), int(t["horizon_min"])
        name = target_name(t)
        labels, hit_mins, terminal = [], [], []
        for _, r in out.iterrows():
            i = int(r["signal_idx"]); d = int(r["direction"]); entry = closes[i]
            j2 = min(n - 1, i + h)
            if i + h >= n:
                labels.append(np.nan); hit_mins.append(np.nan); terminal.append("censored")
                continue
            label = 0; hit = np.nan; term = "timeout"
            if np.isfinite(entry) and entry > 0 and j2 > i:
                for j in range(i + 1, j2 + 1):
                    hi, lo = highs[j], lows[j]
                    if d == 1:
                        tp_hit = np.isfinite(hi) and hi >= entry * (1 + tp / 10000)
                        sl_hit = np.isfinite(lo) and lo <= entry * (1 - sl / 10000)
                    else:
                        tp_hit = np.isfinite(lo) and lo <= entry * (1 - tp / 10000)
                        sl_hit = np.isfinite(hi) and hi >= entry * (1 + sl / 10000)
                    # jesli obie bariery w tej samej swiecy 1m - konserwatywnie traktujemy jako stop-first
                    if sl_hit:
                        label = 0; hit = j - i; term = "sl"; break
                    if tp_hit:
                        label = 1; hit = j - i; term = "tp"; break
            labels.append(label); hit_mins.append(hit); terminal.append(term)
        out[f"y_{name}"] = labels
        out[f"hit_min_{name}"] = hit_mins
        out[f"terminal_{name}"] = terminal
    return out


def label_candidates_by_time(candidates: pd.DataFrame, price_df: pd.DataFrame, targets: list[dict]) -> pd.DataFrame:
    """Timestamp-based labeler for chunked processing. Features are fixed at signal minute close;
    future begins strictly with the next 1m candle. Missing future -> censored.
    """
    if candidates.empty:
        return candidates.copy()
    out=candidates.copy()
    cols=['minute_ms','high','low','close']+(['price_missing'] if 'price_missing' in price_df.columns else [])
    p=price_df[cols].copy().sort_values('minute_ms').drop_duplicates('minute_ms',keep='last')
    p['minute_ms']=pd.to_numeric(p['minute_ms'],errors='coerce').astype('int64')
    lookup={int(t):i for i,t in enumerate(p['minute_ms'].to_numpy())}
    hi=pd.to_numeric(p['high'],errors='coerce').to_numpy(float); lo=pd.to_numeric(p['low'],errors='coerce').to_numpy(float); cl=pd.to_numeric(p['close'],errors='coerce').to_numpy(float)
    missing=pd.to_numeric(p.get('price_missing',pd.Series(0,index=p.index)),errors='coerce').fillna(1).to_numpy(int)
    miss_cum=np.concatenate([[0],np.cumsum(missing)])
    mts=p['minute_ms'].to_numpy('int64'); n=len(p); max_h=max(int(t['horizon_min']) for t in targets)
    mfe=[];mae=[]
    target_vals={target_name(t):([],[],[]) for t in targets}
    for _,r in out.iterrows():
        sm=int(r['signal_ms']); i=lookup.get(sm); d=int(r['direction'])
        if i is None or not np.isfinite(cl[i]) or cl[i]<=0:
            mfe.append(np.nan);mae.append(np.nan)
            for name in target_vals:
                target_vals[name][0].append(np.nan);target_vals[name][1].append(np.nan);target_vals[name][2].append('censored')
            continue
        entry=cl[i]
        end_needed=sm+max_h*60000
        j2=int(np.searchsorted(mts,end_needed,side='right')-1)
        if j2<=i: mfe.append(np.nan);mae.append(np.nan)
        else:
            fh=hi[i+1:j2+1];fl=lo[i+1:j2+1]
            if d==1:
                mfe.append((np.nanmax(fh)/entry-1)*10000 if len(fh) else np.nan);mae.append((1-np.nanmin(fl)/entry)*10000 if len(fl) else np.nan)
            else:
                mfe.append((1-np.nanmin(fl)/entry)*10000 if len(fl) else np.nan);mae.append((np.nanmax(fh)/entry-1)*10000 if len(fh) else np.nan)
        for t in targets:
            name=target_name(t);tp=float(t['tp_bps']);sl=float(t['sl_bps']);h=int(t['horizon_min']); need=sm+h*60000
            # require exact availability through horizon; a gap at the end censors rather than inventing a timeout
            if mts[-1] < need:
                target_vals[name][0].append(np.nan);target_vals[name][1].append(np.nan);target_vals[name][2].append('censored');continue
            jj=int(np.searchsorted(mts,need,side='right')-1)
            if jj<=i or (miss_cum[jj+1]-miss_cum[i+1])>0:
                target_vals[name][0].append(np.nan);target_vals[name][1].append(np.nan);target_vals[name][2].append('censored_gap');continue
            label=0;hit=np.nan;term='timeout'
            for j in range(i+1,jj+1):
                hh,ll=hi[j],lo[j]
                if d==1:
                    tph=np.isfinite(hh) and hh>=entry*(1+tp/10000); slh=np.isfinite(ll) and ll<=entry*(1-sl/10000)
                else:
                    tph=np.isfinite(ll) and ll<=entry*(1-tp/10000); slh=np.isfinite(hh) and hh>=entry*(1+sl/10000)
                if slh: label=0;hit=(mts[j]-sm)/60000;term='sl';break
                if tph: label=1;hit=(mts[j]-sm)/60000;term='tp';break
            target_vals[name][0].append(label);target_vals[name][1].append(hit);target_vals[name][2].append(term)
    out[f'mfe_{max_h}m_bps']=mfe;out[f'mae_{max_h}m_bps']=mae
    for name,(ys,hits,terms) in target_vals.items():
        out[f'y_{name}']=ys;out[f'hit_min_{name}']=hits;out[f'terminal_{name}']=terms
    return out
