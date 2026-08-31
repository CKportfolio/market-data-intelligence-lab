from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any

LEVELS = {
    10: "LEKKIE / szybki rekonesans",
    20: "SREDNIE / rekomendowane na pierwsze pelne badanie",
    40: "BARDZO MOCNE / szerokie badanie",
}

GEOMETRY_PROFILES = [
    {"name": "micro", "confirm": 1, "min_gap": 3, "max_gap": 30, "tol_bps": 15, "min_rebound_bps": 20},
    {"name": "short", "confirm": 2, "min_gap": 5, "max_gap": 60, "tol_bps": 20, "min_rebound_bps": 30},
    {"name": "intraday", "confirm": 3, "min_gap": 8, "max_gap": 120, "tol_bps": 25, "min_rebound_bps": 40},
    {"name": "swing", "confirm": 5, "min_gap": 15, "max_gap": 240, "tol_bps": 35, "min_rebound_bps": 60},
    {"name": "macro_intraday", "confirm": 8, "min_gap": 30, "max_gap": 480, "tol_bps": 50, "min_rebound_bps": 100},
    {"name": "macro", "confirm": 13, "min_gap": 60, "max_gap": 960, "tol_bps": 70, "min_rebound_bps": 150},
]

OUTCOME_TARGETS = [
    {"tp_bps": 25, "sl_bps": 20, "horizon_min": 30},
    {"tp_bps": 50, "sl_bps": 25, "horizon_min": 60},
    {"tp_bps": 75, "sl_bps": 25, "horizon_min": 120},
    {"tp_bps": 100, "sl_bps": 30, "horizon_min": 240},
    {"tp_bps": 125, "sl_bps": 35, "horizon_min": 240},
    {"tp_bps": 150, "sl_bps": 40, "horizon_min": 360},
    {"tp_bps": 200, "sl_bps": 50, "horizon_min": 480},
    {"tp_bps": 250, "sl_bps": 60, "horizon_min": 720},
    {"tp_bps": 300, "sl_bps": 75, "horizon_min": 720},
    {"tp_bps": 400, "sl_bps": 100, "horizon_min": 1440},
    {"tp_bps": 500, "sl_bps": 125, "horizon_min": 1440},
    {"tp_bps": 750, "sl_bps": 175, "horizon_min": 2880},
    {"tp_bps": 1000, "sl_bps": 250, "horizon_min": 4320},
    {"tp_bps": 1250, "sl_bps": 300, "horizon_min": 4320},
    {"tp_bps": 1500, "sl_bps": 350, "horizon_min": 5760},
    {"tp_bps": 2000, "sl_bps": 500, "horizon_min": 10080},
]

MODEL_CONFIGS = [
    {"learning_rate": 0.08, "max_iter": 120, "max_leaf_nodes": 15, "min_samples_leaf": 20, "l2_regularization": 0.2},
    {"learning_rate": 0.06, "max_iter": 180, "max_leaf_nodes": 15, "min_samples_leaf": 30, "l2_regularization": 0.5},
    {"learning_rate": 0.05, "max_iter": 220, "max_leaf_nodes": 31, "min_samples_leaf": 30, "l2_regularization": 0.5},
    {"learning_rate": 0.04, "max_iter": 300, "max_leaf_nodes": 31, "min_samples_leaf": 40, "l2_regularization": 1.0},
    {"learning_rate": 0.08, "max_iter": 180, "max_leaf_nodes": 31, "min_samples_leaf": 20, "l2_regularization": 1.0},
    {"learning_rate": 0.03, "max_iter": 360, "max_leaf_nodes": 31, "min_samples_leaf": 50, "l2_regularization": 1.5},
    {"learning_rate": 0.05, "max_iter": 260, "max_leaf_nodes": 63, "min_samples_leaf": 40, "l2_regularization": 1.5},
    {"learning_rate": 0.035, "max_iter": 420, "max_leaf_nodes": 63, "min_samples_leaf": 50, "l2_regularization": 2.0},
    {"learning_rate": 0.07, "max_iter": 220, "max_leaf_nodes": 63, "min_samples_leaf": 25, "l2_regularization": 2.0},
    {"learning_rate": 0.025, "max_iter": 500, "max_leaf_nodes": 63, "min_samples_leaf": 60, "l2_regularization": 3.0},
    {"learning_rate": 0.05, "max_iter": 320, "max_leaf_nodes": 127, "min_samples_leaf": 50, "l2_regularization": 2.0},
    {"learning_rate": 0.03, "max_iter": 520, "max_leaf_nodes": 127, "min_samples_leaf": 70, "l2_regularization": 3.0},
    {"learning_rate": 0.02, "max_iter": 650, "max_leaf_nodes": 63, "min_samples_leaf": 80, "l2_regularization": 4.0},
    {"learning_rate": 0.04, "max_iter": 420, "max_leaf_nodes": 127, "min_samples_leaf": 35, "l2_regularization": 4.0},
    {"learning_rate": 0.025, "max_iter": 700, "max_leaf_nodes": 127, "min_samples_leaf": 80, "l2_regularization": 5.0},
    {"learning_rate": 0.02, "max_iter": 850, "max_leaf_nodes": 127, "min_samples_leaf": 100, "l2_regularization": 6.0},
]

@dataclass
class ResearchDepth:
    geometry: int = 20
    features: int = 20
    outcomes: int = 20
    validation: int = 20
    model_search: int = 20
    stability: int = 20

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _tier(v: int) -> int:
    if v <= 14:
        return 10
    if v <= 29:
        return 20
    return 40


def geometry_profiles(depth: int):
    n = {10: 2, 20: 4, 40: 6}[_tier(depth)]
    return GEOMETRY_PROFILES[:n]


def feature_lookbacks(depth: int):
    return {
        10: [1, 5, 15, 60],
        20: [1, 3, 5, 15, 30, 60, 240],
        40: [1, 3, 5, 10, 15, 30, 60, 120, 240, 480, 1440],
    }[_tier(depth)]


def outcome_targets(depth: int):
    n = {10: 4, 20: 8, 40: 16}[_tier(depth)]
    return OUTCOME_TARGETS[:n]


def model_configs(depth: int):
    n = {10: 3, 20: 8, 40: 16}[_tier(depth)]
    return MODEL_CONFIGS[:n]


def validation_settings(depth: int):
    return {
        10: {"walk_folds": 3, "holdout_fraction": 0.15, "min_train_fraction": 0.45},
        20: {"walk_folds": 5, "holdout_fraction": 0.20, "min_train_fraction": 0.45},
        40: {"walk_folds": 8, "holdout_fraction": 0.20, "min_train_fraction": 0.40},
    }[_tier(depth)]


def stability_settings(depth: int):
    return {
        10: {"permutation_repeats": 3, "probability_bins": 5},
        20: {"permutation_repeats": 8, "probability_bins": 10},
        40: {"permutation_repeats": 15, "probability_bins": 15},
    }[_tier(depth)]
