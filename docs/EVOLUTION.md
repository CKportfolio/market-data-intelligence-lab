# Evolution

## Stage 0 — architectural lesson from the grid bot

The project began after repeated attempts to make an existing grid strategy more "intelligent" by layering additional market signals onto it. The added infrastructure increased complexity, but the resulting grid behaviour did not improve enough to justify that complexity.

The architectural decision was therefore to separate the concerns:

- keep the grid strategy conceptually simple,
- move predictive research into a separate signal-driven system,
- build the data and validation infrastructure specifically for that predictive problem.

The intended future question became: can short-horizon market formations be recognized *before confirmation* from order-book microstructure and surrounding market variables?

## Stage 1 — fixed-cadence collector

Persist reconstructed L50 every 1 s while keeping all trades/liquidations. This established a reliable raw-data format and archive rotation/recovery path and created the first dataset for later predictive research.

## Stage 2 — dense reference recorder

A separate transport-level recorder was built so adaptive policies could be evaluated against a denser truth source instead of against already-sparsified snapshots.

## Stage 3 — adaptive-sampling PoC → v0.4 research

The early PoC established the replay/calibration/search idea. v0.4 superseded the PoC research code with asymmetric heat, quality-first gates and a 480-policy search. The old duplicate PoC research implementation is intentionally not duplicated in this consolidated repo; its dense recorder survives as Stage 2.

The goal was not merely smaller storage. It was to test whether the dataset could become cheaper while preserving the market episodes likely to matter most for later signal research.

## Stage 4 — deploy research winner

The frozen v0.4 winner and exact calibration metadata were bundled into the adaptive collector. The collector refuses to silently reuse the BTCUSDC/BTCUSDT calibration for a mismatched sensor universe.

## Stage 5 — streaming ML research

The later research engine avoids materializing giant merged raw files. It streams archives into minute partitions, builds candidate-level datasets, applies leakage-aware temporal validation and compares cheap historical features against full microstructure.

This is the first stage that is actually machine learning. The target is not to make the grid bot more complicated; it is to evaluate whether a separate short-horizon formation predictor has enough out-of-sample signal to justify a dedicated signal bot.

## Stage 6 — intended next step

If the predictive hypothesis survives broader out-of-sample validation, the next component would be an execution layer that consumes model probabilities and explicit risk rules. That signal bot is intentionally **not** represented as completed or profitable in the current repository.
