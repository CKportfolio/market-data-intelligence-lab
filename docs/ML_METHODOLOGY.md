# ML methodology

## What is ML here — and what is not

The adaptive sampler is a deterministic control policy selected through offline experimentation. It is **not** described as an ML model.

The `05-ml-research-engine` component is the machine-learning layer.

## Models

- `HistGradientBoostingClassifier` for event/outcome probabilities
- logistic-regression probability calibration
- `HistGradientBoostingRegressor` with absolute-error loss for MFE/MAE estimates

## Temporal validation

The engine uses:

1. chronological selection / final holdout split,
2. an embargo equal to the maximum future-label horizon,
3. walk-forward folds inside the selection period,
4. out-of-sample predictions for model selection,
5. final untouched holdout evaluation.

This avoids random train/test shuffling across time and reduces future-information leakage around label horizons.

## Metrics

Classification reports include Brier score, log loss, ROC AUC and Average Precision. Hyperparameter ranking puts calibration/Brier quality ahead of raw win-rate-style metrics.

## Feature-value ablation

A core research question is whether expensive tick/order-book data adds predictive value over data that can be backfilled cheaply later. The engine therefore compares:

- `historical_backfillable_only`
- `full_microstructure`

This turns data collection cost into an explicit empirical question instead of an assumption.

## Published evidence boundary

The supplied project bundle contains the ML source, unit tests and a synthetic end-to-end smoke test. It does not contain a real-market trained champion model/report. Therefore this repository makes no claim about predictive profitability or live-trading performance.
