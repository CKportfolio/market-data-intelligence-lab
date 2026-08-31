# Reproducibility

## What can be reproduced from the repository

- all Node unit tests and syntax checks,
- adaptive sampler research unit tests,
- ML unit tests,
- synthetic end-to-end streaming ML smoke test,
- validation of published holdout arithmetic and acceptance gates,
- research winner → adaptive collector calibration lineage.

## What requires omitted data

The exact real-market adaptive replay requires the original frozen 15-minute dense-reference archives. They are intentionally omitted because raw market corpora are large runtime data, not source code.

The exact real-market ML experiment likewise requires the collected market archive corpus and REST cache.

## Pinned CI environments

CI uses Node 22 and Python 3.12. Python requirements include project-level ranges; `requirements.lock.txt` files record the versions used to validate this portfolio package.
