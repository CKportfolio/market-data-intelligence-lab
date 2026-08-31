from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd

@dataclass
class Pivot:
    kind: str
    pivot_idx: int
    confirm_idx: int
    price: float


def confirmed_pivots(df: pd.DataFrame, confirm: int) -> tuple[list[Pivot], list[Pivot]]:
    """Pivot jest wykrywany dopiero confirm minut PO ekstremum.
    Signal time nigdy nie jest cofany do czasu samego dolka/szczytu.
    """
    lows = pd.to_numeric(df["low"], errors="coerce").to_numpy(float)
    highs = pd.to_numeric(df["high"], errors="coerce").to_numpy(float)
    n = len(df)
    low_p, high_p = [], []
    left = max(confirm, 2)
    for i in range(left, n - confirm):
        lo_window = lows[i-left:i+confirm+1]
        hi_window = highs[i-left:i+confirm+1]
        if np.isfinite(lows[i]) and lows[i] <= np.nanmin(lo_window):
            low_p.append(Pivot("low", i, i + confirm, float(lows[i])))
        if np.isfinite(highs[i]) and highs[i] >= np.nanmax(hi_window):
            high_p.append(Pivot("high", i, i + confirm, float(highs[i])))
    return low_p, high_p


def _bps_diff(a: float, b: float) -> float:
    m = (abs(a) + abs(b)) / 2
    return abs(a - b) / m * 10000 if m else np.inf


def _rebound_bps(df: pd.DataFrame, p1: Pivot, p2: Pivot, direction: int) -> tuple[float, float]:
    if p2.pivot_idx <= p1.pivot_idx + 1:
        return 0.0, np.nan
    if direction == 1:
        neck = float(pd.to_numeric(df["high"].iloc[p1.pivot_idx:p2.pivot_idx+1], errors="coerce").max())
        base = min(p1.price, p2.price)
        return ((neck / base) - 1) * 10000 if base else 0.0, neck
    neck = float(pd.to_numeric(df["low"].iloc[p1.pivot_idx:p2.pivot_idx+1], errors="coerce").min())
    base = max(p1.price, p2.price)
    return (1 - neck / base) * 10000 if base else 0.0, neck


def detect_candidates(df: pd.DataFrame, profiles: list[dict]) -> pd.DataFrame:
    all_rows = []
    for profile in profiles:
        lows, highs = confirmed_pivots(df, int(profile["confirm"]))
        for direction, pivots in ((1, lows), (-1, highs)):
            # double
            for j in range(1, len(pivots)):
                a, b = pivots[j-1], pivots[j]
                gap = b.pivot_idx - a.pivot_idx
                if gap < profile["min_gap"] or gap > profile["max_gap"]:
                    continue
                dev = _bps_diff(a.price, b.price)
                if dev > profile["tol_bps"]:
                    continue
                rebound, neckline = _rebound_bps(df, a, b, direction)
                if rebound < profile["min_rebound_bps"]:
                    continue
                signal_idx = b.confirm_idx
                if signal_idx >= len(df):
                    continue
                all_rows.append({
                    "signal_idx": signal_idx, "signal_ms": int(df.iloc[signal_idx]["minute_ms"]),
                    "pivot_ms": int(df.iloc[b.pivot_idx]["minute_ms"]), "direction": direction,
                    "formation": "double_bottom" if direction == 1 else "double_top",
                    "profile": profile["name"], "confirm_bars": profile["confirm"],
                    "gap_1_min": gap, "gap_2_min": np.nan, "level_deviation_bps": dev,
                    "rebound_bps": rebound, "neckline": neckline,
                    "pivot_price": b.price, "previous_pivot_price": a.price,
                    "third_previous_pivot_price": np.nan,
                })
            # triple
            for j in range(2, len(pivots)):
                a, b, c = pivots[j-2], pivots[j-1], pivots[j]
                g1, g2 = b.pivot_idx-a.pivot_idx, c.pivot_idx-b.pivot_idx
                if not (profile["min_gap"] <= g1 <= profile["max_gap"] and profile["min_gap"] <= g2 <= profile["max_gap"]):
                    continue
                dev = max(_bps_diff(a.price,b.price), _bps_diff(b.price,c.price), _bps_diff(a.price,c.price))
                if dev > profile["tol_bps"]:
                    continue
                r1, n1 = _rebound_bps(df, a, b, direction)
                r2, n2 = _rebound_bps(df, b, c, direction)
                rebound = min(r1, r2)
                if rebound < profile["min_rebound_bps"]:
                    continue
                signal_idx = c.confirm_idx
                if signal_idx >= len(df):
                    continue
                all_rows.append({
                    "signal_idx": signal_idx, "signal_ms": int(df.iloc[signal_idx]["minute_ms"]),
                    "pivot_ms": int(df.iloc[c.pivot_idx]["minute_ms"]), "direction": direction,
                    "formation": "triple_bottom" if direction == 1 else "triple_top",
                    "profile": profile["name"], "confirm_bars": profile["confirm"],
                    "gap_1_min": g1, "gap_2_min": g2, "level_deviation_bps": dev,
                    "rebound_bps": rebound, "neckline": max(n1,n2) if direction == 1 else min(n1,n2),
                    "pivot_price": c.price, "previous_pivot_price": b.price,
                    "third_previous_pivot_price": a.price,
                })
    if not all_rows:
        return pd.DataFrame()
    c = pd.DataFrame(all_rows)
    # Wiele profili moze opisac ten sam setup. Zostawiamy najlepsza geometrie per czas/kierunek/typ.
    c["quality_geom"] = c["rebound_bps"] / (1.0 + c["level_deviation_bps"])
    c = c.sort_values(["signal_ms", "direction", "formation", "quality_geom"], ascending=[True, True, True, False])
    c = c.drop_duplicates(["signal_ms", "direction", "formation"], keep="first").reset_index(drop=True)
    return c


def attach_features(candidates: pd.DataFrame, minute_df: pd.DataFrame, feature_cols: list[str], profile_order: list[str] | None = None) -> tuple[pd.DataFrame, list[str]]:
    if candidates.empty:
        return candidates, []
    idx = candidates["signal_idx"].astype(int).to_numpy()
    feat = minute_df.iloc[idx][feature_cols].reset_index(drop=True)
    out = pd.concat([candidates.reset_index(drop=True), feat], axis=1)
    out["direction_long"] = (out["direction"] == 1).astype(int)
    out["formation_triple"] = out["formation"].str.startswith("triple").astype(int)
    names = profile_order or sorted(out["profile"].unique())
    profile_map = {name: i for i, name in enumerate(names)}
    out["profile_code"] = out["profile"].map(profile_map).astype(int)
    geom = ["direction_long", "formation_triple", "profile_code", "confirm_bars", "gap_1_min", "gap_2_min", "level_deviation_bps", "rebound_bps"]
    return out, geom + feature_cols
