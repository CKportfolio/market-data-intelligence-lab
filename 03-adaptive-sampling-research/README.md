# Adaptive Sampling Research v0.4

Offline replay study for selecting an adaptive L50 persistence policy against a dense reference stream.

## Study protocol

1. Freeze all complete reference archives visible at study start.
2. Split them chronologically into approximately 70% calibration / 5% gap / 25% holdout.
3. Calibrate sensor baselines only on calibration data.
4. Evaluate exactly 480 adaptive policies plus two fixed baselines.
5. Rank candidates quality-first; storage reduction is a minimum gate, not the main objective.
6. Freeze the winner.
7. Touch the holdout once and evaluate all acceptance gates.

The published run used 56 frozen 15-minute segments: 39 calibration, 3 gap and 14 holdout segments (~3.5 h holdout).

## Winner

`asym-sqrt_excess-price_heavy-clean_release-tau1000-g0.95`

Intervals:

- EXTREME: 800 ms
- ACTIVE: 1500 ms
- NORMAL: 2500 ms
- QUIET: 5000 ms

See [`published-results/`](published-results/) and [`docs/RESULTS.md`](../docs/RESULTS.md).

## Run a new study

With dense archives available under `../02-dense-reference-recorder/data/dense/live/`:

```bash
python run_study.py
```

Or pass another recorder root with `--source`.

## Tests

```bash
python -m pip install -r research/requirements.txt
cd research
python -m unittest discover -s tests -v
```

The raw reference archives are not committed. Unit tests and published result artifacts are included; reproducing the exact real-market study requires the original frozen archives.
