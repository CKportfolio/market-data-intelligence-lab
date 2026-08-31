from __future__ import annotations
import numpy as np
import pandas as pd


def split_selection_holdout(df: pd.DataFrame, holdout_fraction: float, max_horizon_min: int):
    d = df.sort_values("signal_ms").reset_index(drop=True)
    if len(d) < 20:
        return d.copy(), d.iloc[0:0].copy(), None
    cutoff_pos = max(1, min(len(d)-1, int(len(d) * (1 - holdout_fraction))))
    holdout_start = int(d.iloc[cutoff_pos]["signal_ms"])
    embargo_ms = max_horizon_min * 60_000
    selection = d[d["signal_ms"] < holdout_start - embargo_ms].copy()
    holdout = d[d["signal_ms"] >= holdout_start].copy()
    return selection, holdout, holdout_start


def walk_forward_folds(df: pd.DataFrame, n_splits: int, embargo_min: int, min_train_fraction: float = 0.45):
    d = df.sort_values("signal_ms").reset_index(drop=True)
    n = len(d)
    if n < 30:
        return []
    first_test = max(10, int(n * min_train_fraction))
    remaining = n - first_test
    n_splits = max(2, min(n_splits, max(2, remaining // 10)))
    edges = np.linspace(first_test, n, n_splits + 1).astype(int)
    folds = []
    embargo_ms = embargo_min * 60_000
    for k in range(n_splits):
        a, b = int(edges[k]), int(edges[k+1])
        if b <= a:
            continue
        test = d.iloc[a:b]
        if test.empty:
            continue
        test_start = int(test["signal_ms"].min())
        train_idx = d.index[d["signal_ms"] < test_start - embargo_ms].to_numpy()
        test_idx = test.index.to_numpy()
        if len(train_idx) < 20 or len(test_idx) < 5:
            continue
        folds.append((train_idx, test_idx))
    return folds
