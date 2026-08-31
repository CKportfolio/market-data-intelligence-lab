from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    roc_auc_score, average_precision_score, brier_score_loss, log_loss,
    accuracy_score, precision_score, recall_score, mean_absolute_error,
)
from sklearn.utils.class_weight import compute_sample_weight

@dataclass
class CalibratedBundle:
    model: object
    calibrator: object | None
    feature_cols: list[str]
    target_name: str
    target_spec: dict

    def predict_proba_success(self, X: pd.DataFrame) -> np.ndarray:
        p = self.model.predict_proba(X[self.feature_cols])[:, 1]
        if self.calibrator is not None:
            p = self.calibrator.predict_proba(p.reshape(-1, 1))[:, 1]
        return p


def make_classifier(params: dict):
    return HistGradientBoostingClassifier(
        loss="log_loss", random_state=42, early_stopping=True, validation_fraction=0.12,
        **params,
    )


def _usable_feature_cols(frame: pd.DataFrame, features: list[str]) -> list[str]:
    """Keep only features that contain at least two distinct finite values.

    HistGradientBoosting can fail during binning when a walk-forward fold contains
    a feature that is entirely missing or constant in that fold.  This can happen
    even when the feature varies in the full dataset.  Feature eligibility is
    therefore evaluated on the actual fit partition, not globally.
    """
    usable = []
    for col in features:
        if col not in frame.columns:
            continue
        numeric = pd.to_numeric(frame[col], errors="coerce")
        finite = numeric[np.isfinite(numeric.to_numpy(dtype=float, na_value=np.nan))]
        if finite.nunique(dropna=True) >= 2:
            usable.append(col)
    if not usable:
        raise ValueError("No usable non-constant features in this training fold")
    return usable


def _fit_with_calibration(train: pd.DataFrame, features: list[str], ycol: str, params: dict) -> CalibratedBundle:
    d = train.sort_values("signal_ms").reset_index(drop=True)
    split = max(10, int(len(d) * 0.85))
    fit = d.iloc[:split]
    cal = d.iloc[split:]
    yfit = fit[ycol].astype(int)
    fit_features = _usable_feature_cols(fit, features)
    model = make_classifier(params)
    sw = compute_sample_weight("balanced", yfit) if yfit.nunique() > 1 else None
    model.fit(fit[fit_features], yfit, sample_weight=sw)
    calibrator = None
    if len(cal) >= 20 and cal[ycol].nunique() > 1:
        raw = model.predict_proba(cal[fit_features])[:, 1]
        calibrator = LogisticRegression(C=1.0, solver="lbfgs")
        calibrator.fit(raw.reshape(-1, 1), cal[ycol].astype(int))
    return CalibratedBundle(model, calibrator, fit_features, ycol, {})


def binary_metrics(y: np.ndarray, p: np.ndarray) -> dict:
    y = np.asarray(y, dtype=int); p = np.asarray(p, dtype=float)
    pred = (p >= 0.5).astype(int)
    out = {
        "n": int(len(y)), "positive_rate": float(y.mean()) if len(y) else np.nan,
        "brier": float(brier_score_loss(y, p)) if len(np.unique(y)) > 1 else np.nan,
        "log_loss": float(log_loss(y, np.c_[1-p, p], labels=[0,1])) if len(y) else np.nan,
        "accuracy_050": float(accuracy_score(y, pred)) if len(y) else np.nan,
        "precision_050": float(precision_score(y, pred, zero_division=0)) if len(y) else np.nan,
        "recall_050": float(recall_score(y, pred, zero_division=0)) if len(y) else np.nan,
    }
    out["roc_auc"] = float(roc_auc_score(y, p)) if len(np.unique(y)) > 1 else np.nan
    out["average_precision"] = float(average_precision_score(y, p)) if len(np.unique(y)) > 1 else np.nan
    return out


def select_hyperparams(selection: pd.DataFrame, features: list[str], ycol: str, configs: list[dict]) -> tuple[dict, pd.DataFrame]:
    d = selection.sort_values("signal_ms").reset_index(drop=True)
    if len(d) < 40:
        return configs[0], pd.DataFrame()
    cut = int(len(d) * 0.75)
    train, val = d.iloc[:cut], d.iloc[cut:]
    rows = []
    for i, cfg in enumerate(configs):
        try:
            bundle = _fit_with_calibration(train, features, ycol, cfg)
            p = bundle.predict_proba_success(val)
            m = binary_metrics(val[ycol].to_numpy(), p)
            rows.append({"config_id": i, **cfg, **m})
        except Exception as e:
            rows.append({"config_id": i, **cfg, "error": str(e), "brier": np.inf})
    r = pd.DataFrame(rows)
    good = r[np.isfinite(pd.to_numeric(r.get("brier"), errors="coerce"))]
    if good.empty:
        return configs[0], r
    best_id = int(good.sort_values(["brier", "log_loss"], ascending=True).iloc[0]["config_id"])
    return configs[best_id], r


def walk_forward_predict(selection: pd.DataFrame, features: list[str], ycol: str, params: dict, folds: list[tuple[np.ndarray,np.ndarray]]) -> pd.DataFrame:
    preds = []
    for fold_no, (tr_idx, te_idx) in enumerate(folds, 1):
        tr = selection.iloc[tr_idx]
        te = selection.iloc[te_idx]
        if tr[ycol].nunique() < 2:
            continue
        bundle = _fit_with_calibration(tr, features, ycol, params)
        p = bundle.predict_proba_success(te)
        part = te[["signal_ms", "direction", "formation", "profile", ycol]].copy()
        part["prob"] = p
        part["fold"] = fold_no
        preds.append(part)
    return pd.concat(preds, ignore_index=True) if preds else pd.DataFrame()


def threshold_table(preds: pd.DataFrame, ycol: str, tp_bps: float, sl_bps: float) -> pd.DataFrame:
    if preds.empty:
        return pd.DataFrame()
    rows = []
    for th in [0.50,0.55,0.60,0.65,0.70,0.75,0.80,0.85,0.90]:
        s = preds[preds["prob"] >= th]
        if s.empty:
            rows.append({"threshold": th, "signals": 0})
            continue
        wr = float(s[ycol].mean())
        exp = wr * tp_bps - (1-wr) * sl_bps
        rows.append({"threshold": th, "signals": int(len(s)), "win_rate": wr, "expectancy_bps_conservative": exp, "mean_probability": float(s["prob"].mean())})
    return pd.DataFrame(rows)


def probability_bins(preds: pd.DataFrame, ycol: str, bins: int = 10) -> pd.DataFrame:
    if preds.empty:
        return pd.DataFrame()
    d = preds.copy()
    edges = np.linspace(0, 1, bins + 1)
    d["bin"] = pd.cut(d["prob"], edges, include_lowest=True, duplicates="drop")
    g = d.groupby("bin", observed=True).agg(n=(ycol,"size"), predicted=("prob","mean"), observed=(ycol,"mean")).reset_index()
    g["bin"] = g["bin"].astype(str)
    return g


def choose_champion(target_rows: pd.DataFrame) -> dict | None:
    if target_rows.empty:
        return None
    d = target_rows.copy()
    d = d[np.isfinite(pd.to_numeric(d["oos_brier"], errors="coerce"))]
    if d.empty:
        return None
    # najpierw kalibracja/Brier, potem AP; nie wybieramy tylko po win-rate z malej probki
    d["score"] = -d["oos_brier"] + 0.15 * d["oos_average_precision"].fillna(0)
    return d.sort_values("score", ascending=False).iloc[0].to_dict()


def fit_final_bundle(all_data: pd.DataFrame, features: list[str], ycol: str, params: dict, target_spec: dict) -> CalibratedBundle:
    bundle = _fit_with_calibration(all_data, features, ycol, params)
    bundle.target_name = ycol
    bundle.target_spec = target_spec
    return bundle


def save_bundle(bundle: CalibratedBundle, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, path)


def permutation_table(bundle: CalibratedBundle, test: pd.DataFrame, ycol: str, repeats: int) -> pd.DataFrame:
    if len(test) < 20 or test[ycol].nunique() < 2:
        return pd.DataFrame()
    # permutation na bazowym modelu; ranking kierunku wplywu nie jest interpretacja kauzalna.
    try:
        r = permutation_importance(bundle.model, test[bundle.feature_cols], test[ycol].astype(int), n_repeats=repeats, random_state=42, scoring="neg_log_loss", n_jobs=-1)
        return pd.DataFrame({"feature": bundle.feature_cols, "importance_mean": r.importances_mean, "importance_std": r.importances_std}).sort_values("importance_mean", ascending=False)
    except Exception:
        return pd.DataFrame()


def fit_regressors(selection: pd.DataFrame, holdout: pd.DataFrame, features: list[str], mfe_col: str, mae_col: str) -> tuple[dict, pd.DataFrame]:
    models = {}; rows=[]
    for name, col in (("mfe", mfe_col), ("mae", mae_col)):
        tr = selection.dropna(subset=[col])
        te = holdout.dropna(subset=[col])
        if len(tr) < 30:
            continue
        model = HistGradientBoostingRegressor(loss="absolute_error", learning_rate=0.05, max_iter=250, max_leaf_nodes=31, min_samples_leaf=30, l2_regularization=1.0, random_state=42)
        model.fit(tr[features], tr[col])
        models[name] = model
        if len(te):
            p = model.predict(te[features])
            rows.append({"model": name, "n_holdout": len(te), "mae_bps": mean_absolute_error(te[col], p)})
    return models, pd.DataFrame(rows)
