# Adaptive-sampling experiment design

## Hypothesis

A fixed 1-second L50 persistence schedule wastes storage during quiet periods. A policy driven by current microstructure activity can reduce persistence volume while retaining important episodes.

## Frozen chronological study

The published run froze 56 complete 15-minute dense-reference archives and split them chronologically:

- 39 calibration segments (~70%),
- 3 gap segments (~5%),
- 14 holdout segments (~25%).

The holdout was separated by a chronological gap and evaluated only after a calibration winner passed all required gates.

## Search space

480 adaptive candidates:

```text
5 tau values
x 4 gain values
x 2 excess transforms
x 3 sensor-weight families
x 4 response profiles
= 480
```

Two fixed baselines are also present in the published leaderboard, therefore `leaderboard_v04.csv` contains 482 rows.

## Sensors

- fast price range
- fast tick travel
- trade rate
- quote volume
- order-book churn
- imbalance delta

## Acceptance gates

- Tier A event recall >= 98%
- Tier B event recall >= 90%
- A+B weighted event recall >= 95%
- Tier A snapshot recall >= 98%
- Tier B snapshot recall >= 90%
- reaction p95 <= 750 ms
- post-episode oversampling p95 <= 6 s
- false-fast time <= 20%
- snapshot reduction >= 40%

The optimizer is explicitly **quality-first**. Reduction above the minimum gate is not maximized at the expense of fidelity.
