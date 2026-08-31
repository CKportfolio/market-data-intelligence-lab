# Signal-bot research hypothesis

## Why this branch of the project exists

The starting point was an existing grid bot. Several attempts were made to make the grid strategy more context-aware by adding extra market intelligence. The practical outcome was an important negative result: the extra layers made the system more complicated, but did not deliver enough improvement to justify coupling prediction directly into the grid logic.

That led to a cleaner architecture:

- the grid remains a grid,
- predictive behaviour becomes a separate research problem,
- a future signal bot would exist only if the predictive evidence is strong enough.

## Research question

The working hypothesis is:

> **Before a short-horizon formation is fully confirmed by price, the order book and surrounding market variables may already contain a measurable state change.**

The research engine is intended to test whether that pre-confirmation state has useful out-of-sample predictive value.

Potential inputs include features derived from:

- order-book imbalance,
- book churn / liquidity movement,
- tick travel and local price range,
- trade rate and quote volume,
- liquidations,
- historical/backfillable market variables,
- combinations of microstructure and slower contextual features.

## Why own data collection was necessary

The research question depends on what happened in the seconds/minutes *before* confirmation. That data cannot always be reconstructed later at the required resolution from standard historical candles.

The fixed collector therefore existed first to create a raw corpus. The dense recorder then provided a higher-resolution reference. Adaptive-sampling research was added to answer a second question:

> **Can the long-running dataset be made much cheaper without disproportionately removing the episodes most valuable for future ML?**

This is why the adaptive collector is part of the same story as the ML engine rather than a separate optimization exercise.

## What would count as success

The signal-bot hypothesis is not considered validated merely because a classifier achieves a good score on one split. A credible result should survive:

- chronological train/selection/holdout separation,
- walk-forward validation,
- an embargo around future-label horizons,
- probability calibration checks,
- multiple market regimes/days,
- comparison against simpler historical-only features,
- explicit accounting for adaptive-sampling metadata and possible selection bias.

Only after that would model outputs be candidates for an execution strategy.

## What this repository does not claim

This repository does **not** claim:

- a profitable predictor,
- a finished signal bot,
- a universally validated top-formation detector,
- production readiness for real-money execution.

It documents and tests the research infrastructure needed to answer those questions without pretending the answer is already known.
