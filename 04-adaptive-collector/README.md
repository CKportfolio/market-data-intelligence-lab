# Market ML Collector Adaptive v3.0

Production-candidate collector based on the validated **Adaptive OB v0.4 winner**, now with the exact research calibration bundled:

`asym-sqrt_excess-price_heavy-clean_release-tau1000-g0.95`

It keeps every public trade and liquidation, reconstructs Spot + Perp L50 continuously in RAM, and changes only how often full L50 snapshots are persisted.

## Adaptive L50 schedule

The engine is evaluated every 250 ms and chooses one of:

- QUIET: 5000 ms
- NORMAL: 2500 ms
- ACTIVE: 1500 ms
- EXTREME: 800 ms

Price shocks attack immediately; flow+book confluence attacks to <=1500 ms. Small isolated flow/book deviations are heavily discounted and quiet release is intentionally faster. Every saved orderbook snapshot contains a `sampling` object with policy/mode/regime/interval/heat/reason/calibrationId so later ML can model the non-uniform sampling policy instead of accidentally learning a hidden selection artifact.

**Trades and liquidations are never adaptively dropped.** Only full L50 snapshot frequency changes.

## Calibration: exact research file is now bundled

The exact `calibration.json` supplied after the v0.4 study is included as:

`calibration/research-v04-btcusdc-spot-btcusdt-perp.json`

It contains the original 140,419 calibration rows metadata and the exact baselines/event thresholds from the study. It is tagged with the **sensor universe used in the research**:

- Spot primary/sensor: `BTCUSDC`
- Linear/perp sensor: `BTCUSDT`
- L50
- fast window: 1000 ms
- adaptive tick: 250 ms
- imbalance top levels: 10

When the collector runs with exactly `SPOT_SYMBOL=BTCUSDC` and `PERP_SYMBOL=BTCUSDT`, this bundled research calibration is selected automatically. There is **no 30-minute warmup**; adaptive v0.4 starts immediately.

The collector deliberately refuses to apply this calibration to another symbol universe. In particular, if an old collector is currently recording `BTCUSDT` Spot + `BTCUSDT` Perp, the zero-gap handoff preserves those symbols and the new collector falls back to the per-data-dir calibration (`data/adaptive/calibration.json`) or a safe 1-second warmup if none exists. This prevents a scientifically invalid BTCUSDC calibration from being silently used on BTCUSDT Spot.

This distinction matters for continuity: changing BTCUSDT Spot to BTCUSDC Spot during handoff would be a **dataset migration**, not a zero-gap continuation. Do that only intentionally.

An explicit calibration can still be supplied with `ADAPTIVE_CALIBRATION_FILE=/path/to/file.json`. Calibration metadata is checked against the configured sensor universe when present.

## 900 MiB rotation

`RAW_BATCH_MAX_MB=900`

The rotation limit is RAW/uncompressed bytes. A 900 MiB batch will often produce a compressed tar in the broad neighborhood of tens of MiB to ~100 MiB, but gzip size cannot be guaranteed because market activity and the mix of trades/orderbook snapshots change.

## Zero-gap PM2 handoff from the old collector

The new collector defaults to a separate live namespace:

`data/raw/live/current-adaptive`

and separate staging:

`data/raw/staging-adaptive`

but uses the same shared `data/raw/archives` directory. Therefore old and new collectors may safely overlap for a few seconds while the new WebSockets subscribe and both books become ready.

This is preferable to `pm2 stop old && pm2 start new`, which can leave a WebSocket handshake/subscription gap.

Deploy this folder separately, e.g.:

```bash
/srv/market-ml-collector-adaptive
```

Install dependencies:

```bash
cd /srv/market-ml-collector-adaptive
npm install --omit=dev
```

Then one handoff command:

```bash
bash pm2-handoff.sh ml-market-collector /srv/market-ml-collector/data
```

The script:

1. starts `ml-market-collector-adaptive`, pointed at the existing data directory;
2. waits until Spot + Linear sockets are subscribed, both L50 books are ready and trades are arriving;
3. only then stops `ml-market-collector`;
4. runs `pm2 save`.

If the new collector does not become ready within 90 seconds, the script exits and **does not stop the old collector**.

The few seconds of overlap may produce a tiny duplicate window. This is intentional: for continuity, overlap is safer than a gap. Downstream research should deduplicate by trade IDs / timestamps when combining the handoff boundary.

### Can PM2 do stop-old/start-new atomically?

Not for this kind of singleton WebSocket data recorder. `pm2 reload` zero-downtime semantics are aimed at application workers and do not solve a shared-file writer handoff. Starting the new namespaced writer first, waiting for readiness, and then stopping the old one is the safer pattern.

## Status

```bash
curl http://127.0.0.1:3043/status
```

Important fields:

- `handoffReady`
- `adaptive.mode` (`warmup` or `adaptive`)
- `adaptive.intervalMs`
- `adaptive.regime`
- `adaptive.heat`
- `adaptive.calibrated`
- `adaptive.warmupRemainingSec`
- `storage.current.miB`
- `storage.current.fillPct`

## Tests

```bash
npm install
npm test
npm run check
```

## Defaults

- Spot: BTCUSDT
- Perp: BTCUSDT
- L50
- adaptive tick: 250 ms
- full snapshot interval: 5.0 / 2.5 / 1.5 / 0.8 s
- warmup if no calibration: fixed 1 s for 30 min
- `SAVE_ORDERBOOK_DELTAS=false`
- `ORDERBOOK_WRITE_UNCHANGED=true` (keeps Spot/Perp snapshots synchronized with the tested sampler schedule)
- RAW rotation: 900 MiB
- PM2 process: `ml-market-collector-adaptive`
- status: 127.0.0.1:3043
