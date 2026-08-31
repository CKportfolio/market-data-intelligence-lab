import { createServer } from "node:http";
import path from "node:path";
import { mkdir } from "node:fs/promises";
import { CONFIG } from "./config.mjs";
import { RotatingArchiveWriter } from "./lib/rotating-archive-writer.mjs";
import { LocalOrderBook, normalizeOrderbookEvent } from "./lib/orderbook.mjs";
import { BybitPublicSocket } from "./lib/bybit-ws.mjs";
import { AdaptiveSampler } from "./lib/adaptive-sampler.mjs";

const RAW_DIR = path.join(CONFIG.DATA_DIR, "raw");
await mkdir(RAW_DIR, { recursive: true });
await mkdir(path.join(CONFIG.DATA_DIR, "complete"), { recursive: true });
await mkdir("./logs", { recursive: true });

const startedAt = Date.now();
let shuttingDown = false;
let lastSnapshotMs = null;
let samplingBusy = false;
let lastAdaptiveStateKey = null;

const stats = {
  startedAt: new Date(startedAt).toISOString(),
  records: { spotTrades: 0, spotOrderbook: 0, perpTrades: 0, perpOrderbook: 0, liquidations: 0, orderbookEvents: 0 },
  last: {},
  sockets: {},
};

const writer = await new RotatingArchiveWriter(RAW_DIR, {
  maxBytes: CONFIG.RAW_BATCH_MAX_MB * 1024 * 1024,
  liveName: CONFIG.RAW_LIVE_NAME,
  collectorId: "ml-market-collector-adaptive-v3",
  onArchive: (event) => {
    if (event.ok) {
      stats.last.archive = { at: new Date().toISOString(), file: path.basename(event.archivePath), range: event.manifest?.range };
      console.log(`[archive] OK ${path.basename(event.archivePath)}`);
    } else {
      stats.last.archiveError = { at: new Date().toISOString(), error: event.error?.message || String(event.error) };
    }
  },
}).init();

const spotBook = new LocalOrderBook({ market: "spot", symbol: CONFIG.SPOT_SYMBOL, depth: CONFIG.ORDERBOOK_DEPTH });
const perpBook = new LocalOrderBook({ market: "linear", symbol: CONFIG.PERP_SYMBOL, depth: CONFIG.ORDERBOOK_DEPTH });
const adaptive = await AdaptiveSampler.create({
  enabled: CONFIG.ADAPTIVE_ENABLED,
  primaryMarket: "spot",
  primarySymbol: CONFIG.SPOT_SYMBOL,
  topN: CONFIG.ADAPTIVE_TOP_LEVELS,
  tickMs: CONFIG.ADAPTIVE_TICK_MS,
  fastWindowMs: CONFIG.ADAPTIVE_FAST_WINDOW_MS,
  warmupMinutes: CONFIG.ADAPTIVE_WARMUP_MINUTES,
  warmupIntervalMs: CONFIG.ADAPTIVE_WARMUP_INTERVAL_MS,
  calibrationFile: CONFIG.ADAPTIVE_CALIBRATION_FILE,
  baselineQuantile: CONFIG.ADAPTIVE_BASELINE_QUANTILE,
  eventQuantile: CONFIG.ADAPTIVE_EVENT_QUANTILE,
  eventFloors: CONFIG.ADAPTIVE_EVENT_FLOORS,
  baselineFloors: CONFIG.ADAPTIVE_BASELINE_FLOORS,
  profile: CONFIG.ADAPTIVE_PROFILE,
  expectedUniverse: CONFIG.ADAPTIVE_CALIBRATION_EXPECTED_UNIVERSE,
});

function tradeRows(msg, market, recvMs) {
  return (Array.isArray(msg.data) ? msg.data : []).map((x) => ({
    schema: "trade-v1", exchange: "bybit", market,
    symbol: x.s || (market === "spot" ? CONFIG.SPOT_SYMBOL : CONFIG.PERP_SYMBOL),
    tsRecvMs: recvMs, tsExchangeMsgMs: Number(msg.ts) || null, tsTradeMs: Number(x.T) || null,
    tradeId: x.i || null, seq: Number(x.seq) || null, side: x.S || null,
    price: Number(x.p), size: Number(x.v), tickDirection: x.L || null,
    blockTrade: Boolean(x.BT), rpi: Boolean(x.RPI),
  })).filter((x) => Number.isFinite(x.tsTradeMs) && Number.isFinite(x.price) && Number.isFinite(x.size));
}

function liquidationRows(msg, recvMs) {
  return (Array.isArray(msg.data) ? msg.data : []).map((x) => ({
    schema: "liquidation-v1", exchange: "bybit", market: "linear", symbol: x.s || CONFIG.PERP_SYMBOL,
    tsRecvMs: recvMs, tsExchangeMsgMs: Number(msg.ts) || null, tsLiquidationMs: Number(x.T) || null,
    positionSide: x.S || null, size: Number(x.v), bankruptcyPrice: Number(x.p),
  })).filter((x) => Number.isFinite(x.tsLiquidationMs));
}

async function systemEvent(event) {
  stats.sockets[event.socket || "collector"] = event;
  try { await writer.write("system", { schema: "system-event-v1", ...event }, event.tsMs || Date.now()); }
  catch (err) { console.error("[system-event]", err); }
}

function feedTrades(rows) {
  for (const row of rows) adaptive.ingestTrade({ market: row.market, symbol: row.symbol, tsMs: row.tsTradeMs, price: row.price, size: row.size });
}

function feedBook(msg, market, symbol, recvMs, book) {
  book.apply(msg, recvMs);
  const d = msg?.data || {};
  const changedLevels = (Array.isArray(d.b) ? d.b.length : 0) + (Array.isArray(d.a) ? d.a.length : 0);
  const tsMs = Number(msg.cts) || Number(msg.ts) || recvMs;
  adaptive.ingestBookEvent({ market, symbol, tsMs, changedLevels });
}

async function handleSpot(msg, recvMs) {
  const topic = String(msg.topic || "");
  if (topic.startsWith("publicTrade.")) {
    const rows = tradeRows(msg, "spot", recvMs); feedTrades(rows);
    if (rows.length) { await writer.write("spot_trades", rows, rows[0].tsTradeMs); stats.records.spotTrades += rows.length; stats.last.spotTradeMs = rows.at(-1).tsTradeMs; }
    return;
  }
  if (topic.startsWith("orderbook.")) {
    feedBook(msg, "spot", CONFIG.SPOT_SYMBOL, recvMs, spotBook);
    if (CONFIG.SAVE_ORDERBOOK_DELTAS) {
      await writer.write("spot_orderbook_events", normalizeOrderbookEvent(msg, "spot", CONFIG.SPOT_SYMBOL, recvMs), Number(msg.cts) || Number(msg.ts) || recvMs);
      stats.records.orderbookEvents++;
    }
  }
}

async function handleLinear(msg, recvMs) {
  const topic = String(msg.topic || "");
  if (topic.startsWith("publicTrade.")) {
    const rows = tradeRows(msg, "linear", recvMs); feedTrades(rows);
    if (rows.length) { await writer.write("perp_trades", rows, rows[0].tsTradeMs); stats.records.perpTrades += rows.length; stats.last.perpTradeMs = rows.at(-1).tsTradeMs; }
    return;
  }
  if (topic.startsWith("orderbook.")) {
    feedBook(msg, "linear", CONFIG.PERP_SYMBOL, recvMs, perpBook);
    if (CONFIG.SAVE_ORDERBOOK_DELTAS) {
      await writer.write("perp_orderbook_events", normalizeOrderbookEvent(msg, "linear", CONFIG.PERP_SYMBOL, recvMs), Number(msg.cts) || Number(msg.ts) || recvMs);
      stats.records.orderbookEvents++;
    }
    return;
  }
  if (topic.startsWith("allLiquidation.")) {
    const rows = liquidationRows(msg, recvMs);
    if (rows.length) { await writer.write("liquidations", rows, rows[0].tsLiquidationMs); stats.records.liquidations += rows.length; stats.last.liquidationMs = rows.at(-1).tsLiquidationMs; }
  }
}

const spotTopics = [
  CONFIG.COLLECT_SPOT_TRADES ? `publicTrade.${CONFIG.SPOT_SYMBOL}` : null,
  CONFIG.COLLECT_SPOT_ORDERBOOK ? `orderbook.${CONFIG.ORDERBOOK_DEPTH}.${CONFIG.SPOT_SYMBOL}` : null,
];
const linearTopics = [
  CONFIG.COLLECT_PERP_TRADES ? `publicTrade.${CONFIG.PERP_SYMBOL}` : null,
  CONFIG.COLLECT_PERP_ORDERBOOK ? `orderbook.${CONFIG.ORDERBOOK_DEPTH}.${CONFIG.PERP_SYMBOL}` : null,
  CONFIG.COLLECT_LIQUIDATIONS ? `allLiquidation.${CONFIG.PERP_SYMBOL}` : null,
];

const sockets = [
  new BybitPublicSocket({ name: "spot", url: CONFIG.WS_SPOT_URL, topics: spotTopics,
    onMessage: (msg, recvMs) => handleSpot(msg, recvMs).catch((err) => console.error("[spot message]", err)),
    onStatus: (e) => { console.log(`[ws:${e.socket}] ${e.state}`); void systemEvent(e); }, }),
  new BybitPublicSocket({ name: "linear", url: CONFIG.WS_LINEAR_URL, topics: linearTopics,
    onMessage: (msg, recvMs) => handleLinear(msg, recvMs).catch((err) => console.error("[linear message]", err)),
    onStatus: (e) => { console.log(`[ws:${e.socket}] ${e.state}`); void systemEvent(e); }, }),
];

function samplingMeta(decision, now) {
  return {
    policy: "adaptive-ob-v0.4-winner",
    calibrationSource: adaptive.calibrationSource,
    mode: decision.mode,
    regime: decision.regime,
    intervalMs: decision.intervalMs,
    heat: Number(Number(decision.heat || 0).toFixed(6)),
    reason: decision.reason,
    calibrationId: adaptive.calibrationId,
    tsDecisionMs: now,
  };
}

const obSampler = setInterval(async () => {
  if (samplingBusy) return;
  samplingBusy = true;
  const now = Date.now();
  try {
    const d = await adaptive.evaluate(now, spotBook);
    const stateKey = `${d.mode}:${d.regime}:${d.intervalMs}`;
    if (stateKey !== lastAdaptiveStateKey) {
      console.log(`[adaptive] ${stateKey} heat=${Number(d.heat || 0).toFixed(3)} reason=${d.reason}`);
      lastAdaptiveStateKey = stateKey;
    }
    const due = lastSnapshotMs == null || (now - lastSnapshotMs) >= d.intervalMs;
    const shouldSample = due || d.speedup;
    if (!shouldSample) return;

    let wrote = false;
    const meta = samplingMeta(d, now);
    if (CONFIG.COLLECT_SPOT_ORDERBOOK && spotBook.ready && (CONFIG.ORDERBOOK_WRITE_UNCHANGED || spotBook.dirty)) {
      const snap = spotBook.snapshot(now);
      if (snap) {
        snap.sampling = meta;
        await writer.write("spot_orderbook", snap, now);
        spotBook.markSaved(snap.bookVersion);
        stats.records.spotOrderbook++;
        stats.last.spotOrderbookMs = now;
        wrote = true;
      }
    }
    if (CONFIG.COLLECT_PERP_ORDERBOOK && perpBook.ready && (CONFIG.ORDERBOOK_WRITE_UNCHANGED || perpBook.dirty)) {
      const snap = perpBook.snapshot(now);
      if (snap) {
        snap.sampling = meta;
        await writer.write("perp_orderbook", snap, now);
        perpBook.markSaved(snap.bookVersion);
        stats.records.perpOrderbook++;
        stats.last.perpOrderbookMs = now;
        wrote = true;
      }
    }
    if (wrote) lastSnapshotMs = now;
  } catch (err) { console.error("[adaptive orderbook sampler]", err); }
  finally { samplingBusy = false; }
}, CONFIG.ADAPTIVE_TICK_MS);
obSampler.unref?.();

function handoffReady() {
  const spotSocket = stats.sockets.spot?.state;
  const linearSocket = stats.sockets.linear?.state;
  return spotSocket === "subscribed" && linearSocket === "subscribed" && spotBook.ready && perpBook.ready && stats.records.spotTrades > 0 && stats.records.perpTrades > 0 && stats.records.spotOrderbook > 0 && stats.records.perpOrderbook > 0;
}

const httpServer = createServer((req, res) => {
  if (req.url === "/" || req.url === "/status" || req.url === "/health") {
    const now = Date.now();
    const body = {
      status: shuttingDown ? "stopping" : "running",
      handoffReady: handoffReady(),
      uptimeSec: Math.floor((now - startedAt) / 1000),
      config: {
        spotSymbol: CONFIG.SPOT_SYMBOL, perpSymbol: CONFIG.PERP_SYMBOL, orderbookDepth: CONFIG.ORDERBOOK_DEPTH,
        adaptiveTickMs: CONFIG.ADAPTIVE_TICK_MS, saveOrderbookDeltas: CONFIG.SAVE_ORDERBOOK_DELTAS,
        rawBatchMaxMb: CONFIG.RAW_BATCH_MAX_MB, rawLiveName: CONFIG.RAW_LIVE_NAME, dataDir: CONFIG.DATA_DIR,
        calibrationFile: CONFIG.ADAPTIVE_CALIBRATION_FILE, researchCalibrationCompatible: CONFIG.ADAPTIVE_RESEARCH_CALIBRATION_COMPATIBLE,
      },
      adaptive: adaptive.status(now),
      books: { spotReady: spotBook.ready, perpReady: perpBook.ready },
      storage: writer.status(),
      ...stats,
    };
    res.writeHead(200, { "content-type": "application/json; charset=utf-8" });
    res.end(JSON.stringify(body, null, 2));
    return;
  }
  res.writeHead(404, { "content-type": "text/plain" }); res.end("not found\n");
});

httpServer.listen(CONFIG.STATUS_PORT, "127.0.0.1", () => {
  console.log(`[collector] status: http://127.0.0.1:${CONFIG.STATUS_PORT}/status`);
  console.log(`[collector] raw data: ${RAW_DIR}`);
  console.log(`[collector] live namespace: ${CONFIG.RAW_LIVE_NAME}`);
  console.log(`[collector] batch rotation: ${CONFIG.RAW_BATCH_MAX_MB} MiB RAW -> tar.gz`);
  console.log(`[collector] spot=${CONFIG.SPOT_SYMBOL}, perp=${CONFIG.PERP_SYMBOL}, OB=L${CONFIG.ORDERBOOK_DEPTH}, adaptive=v0.4 winner`);
  console.log(`[collector] calibration=${CONFIG.ADAPTIVE_CALIBRATION_FILE}${CONFIG.ADAPTIVE_RESEARCH_CALIBRATION_COMPATIBLE ? " (validated research universe)" : " (symbol-specific/live fallback)"}`);
});
for (const s of sockets) s.start();

async function shutdown(signal, exitCode = 0) {
  if (shuttingDown) return;
  shuttingDown = true;
  console.log(`[collector] ${signal} -> shutdown`);
  clearInterval(obSampler);
  for (const s of sockets) s.stop();
  await new Promise((resolve) => httpServer.close(resolve));
  await writer.close();
  process.exit(exitCode);
}
process.on("SIGINT", () => void shutdown("SIGINT"));
process.on("SIGTERM", () => void shutdown("SIGTERM"));
process.on("uncaughtException", (err) => { console.error("[collector] uncaughtException", err); void shutdown("uncaughtException", 1); });
process.on("unhandledRejection", (err) => console.error("[collector] unhandledRejection", err));
