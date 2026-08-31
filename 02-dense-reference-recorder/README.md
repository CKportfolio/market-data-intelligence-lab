# Dense Reference Recorder

Reference-data recorder used to evaluate adaptive order-book sampling without guessing the sampling policy from the same sparse data it is meant to optimize.

It subscribes to public Bybit streams for:

- BTCUSDC Spot: `orderbook.50` + public trades,
- BTCUSDT Linear: `orderbook.50` + public trades + liquidations.

The recorder persists transport-level order-book snapshot/delta messages, trades and liquidations into small JSONL chunks and rotates them into 15-minute `.tar.gz` segments. Full L50 books are reconstructed during offline replay.

This component exists to answer a research question: **how much can full L50 persistence be reduced without losing important market episodes?**

## Run

```bash
npm ci
npm run check
npm start
```

Runtime archives are written under `./data/dense/live/` and are intentionally not included in this portfolio repository.
