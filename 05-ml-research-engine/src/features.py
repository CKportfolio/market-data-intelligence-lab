from __future__ import annotations
import numpy as np
import pandas as pd


def add_time_series_features(df: pd.DataFrame, lookbacks: list[int]) -> tuple[pd.DataFrame, list[str]]:
    x = df.copy()
    features: list[str] = []
    close = pd.to_numeric(x["close"], errors="coerce")
    high = pd.to_numeric(x["high"], errors="coerce")
    low = pd.to_numeric(x["low"], errors="coerce")
    volume = pd.to_numeric(x.get("spot_kline_volume"), errors="coerce") if "spot_kline_volume" in x else pd.Series(np.nan, index=x.index)

    x["ret_1m_bps"] = close.pct_change(fill_method=None) * 10000
    features.append("ret_1m_bps")
    x["range_bps"] = (high - low) / close.replace(0, np.nan) * 10000
    features.append("range_bps")

    for w in lookbacks:
        if w > 1:
            c = f"ret_{w}m_bps"
            x[c] = close.pct_change(w, fill_method=None) * 10000
            features.append(c)
        c = f"rv_{w}m_bps"
        x[c] = x["ret_1m_bps"].rolling(w, min_periods=1 if w == 1 else max(2, min(w, 3))).std() * np.sqrt(max(w, 1))
        features.append(c)
        c = f"range_mean_{w}m_bps"
        x[c] = x["range_bps"].rolling(w, min_periods=1).mean()
        features.append(c)
        c = f"dist_high_{w}m_bps"
        rh = high.rolling(w, min_periods=1).max()
        x[c] = (close / rh - 1.0) * 10000
        features.append(c)
        c = f"dist_low_{w}m_bps"
        rl = low.rolling(w, min_periods=1).min()
        x[c] = (close / rl - 1.0) * 10000
        features.append(c)
        c = f"ema_gap_{w}m_bps"
        ema = close.ewm(span=max(2, w), adjust=False).mean()
        x[c] = (close / ema - 1.0) * 10000
        features.append(c)

        if volume.notna().any():
            vm = volume.rolling(w, min_periods=1 if w == 1 else 2).mean()
            vs = volume.rolling(w, min_periods=1 if w == 1 else 2).std().replace(0, np.nan)
            c = f"volume_z_{w}m"
            x[c] = (volume - vm) / vs
            features.append(c)

        for p in ("spot", "perp"):
            d = f"{p}_delta"
            if d in x:
                c = f"{p}_delta_sum_{w}m"
                x[c] = x[d].rolling(w, min_periods=1).sum()
                features.append(c)
            dr = f"{p}_delta_ratio"
            if dr in x:
                c = f"{p}_delta_ratio_mean_{w}m"
                x[c] = x[dr].rolling(w, min_periods=1).mean()
                features.append(c)

        for p in ("spot_ob", "perp_ob"):
            for suffix in ("imbalance_l50_mean", "imbalance_10bps_mean", "imbalance_25bps_mean", "spread_bps_mean", "micro_dev_bps_mean"):
                src = f"{p}_{suffix}"
                if src in x:
                    c = f"{src}_roll{w}"
                    x[c] = x[src].rolling(w, min_periods=1).mean()
                    features.append(c)

    # Cechy REST/mikrostruktury, jesli sa dostepne.
    direct = [
        "spot_delta_ratio", "perp_delta_ratio", "spot_trade_count", "perp_trade_count",
        "spot_volume", "perp_volume", "perp_spot_basis_bps", "mark_index_basis_bps",
        "premium_close", "oi_openInterest", "long_short_net", "funding_fundingRate",
        "liq_count", "liq_volume", "liq_buy_volume", "liq_sell_volume",
    ]
    for c in direct:
        if c in x and c not in features:
            features.append(c)

    for p in ("spot_ob", "perp_ob"):
        for suffix in (
            "spread_bps_mean", "spread_bps_max", "imbalance_l50_mean", "imbalance_l50_last",
            "imbalance_5bps_mean", "imbalance_10bps_mean", "imbalance_25bps_mean", "imbalance_50bps_mean",
            "micro_dev_bps_mean", "bid_qty_l50_mean", "ask_qty_l50_mean",
        ):
            c = f"{p}_{suffix}"
            if c in x and c not in features:
                features.append(c)

    if "oi_openInterest" in x:
        for w in [5, 15, 60, 240]:
            if w <= max(lookbacks):
                c = f"oi_change_{w}m_bps"
                x[c] = pd.to_numeric(x["oi_openInterest"], errors="coerce").pct_change(w, fill_method=None) * 10000
                features.append(c)

    # kalendarz: cykliczne kodowanie czasu, bez wiedzy o przyszlosci
    dt = pd.to_datetime(x["minute_ms"], unit="ms", utc=True)
    minute_day = dt.dt.hour * 60 + dt.dt.minute
    x["tod_sin"] = np.sin(2 * np.pi * minute_day / 1440)
    x["tod_cos"] = np.cos(2 * np.pi * minute_day / 1440)
    x["dow_sin"] = np.sin(2 * np.pi * dt.dt.dayofweek / 7)
    x["dow_cos"] = np.cos(2 * np.pi * dt.dt.dayofweek / 7)
    features += ["tod_sin", "tod_cos", "dow_sin", "dow_cos"]

    # inf -> nan, model HGB obsluzy braki
    x[features] = x[features].replace([np.inf, -np.inf], np.nan)
    return x, list(dict.fromkeys(features))
