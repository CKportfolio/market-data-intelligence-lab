import { createServer } from "node:http";
import path from "node:path";
import { mkdir } from "node:fs/promises";
import { CONFIG } from "./config.mjs";
import { RotatingArchiveWriter } from "./lib/rotating-archive-writer.mjs";
import { LocalOrderBook, normalizeOrderbookEvent } from "./lib/orderbook.mjs";
import { BybitPublicSocket } from "./lib/bybit-ws.mjs";

const RAW_DIR = path.join(CONFIG.DATA_DIR, "raw");
await mkdir(RAW_DIR, { recursive: true });
await mkdir(path.join(CONFIG.DATA_DIR, "complete"), { recursive: true });
await mkdir("./logs", { recursive: true });

const startedAt = Date.now();
let shuttingDown = false;

const stats = {
  startedAt: new Date(startedAt).toISOString(),
  records: {
    spotTrades: 0,
    spotOrderbook: 0,
    perpTrades: 0,
    perpOrderbook: 0,
    liquidations: 0,
    orderbookEvents: 0,
  },
  last: {},
  sockets: {},
};

const writer = await new RotatingArchiveWriter(RAW_DIR, {
  maxBytes: CONFIG.RAW_BATCH_MAX_MB * 1024 * 1024,
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

function tradeRows(msg, market, recvMs) {
  return (Array.isArray(msg.data) ? msg.data : []).map((x) => ({
    schema: "trade-v1",
    exchange: "bybit",
    market,
    symbol: x.s || (market === "spot" ? CONFIG.SPOT_SYMBOL : CONFIG.PERP_SYMBOL),
    tsRecvMs: recvMs,
    tsExchangeMsgMs: Number(msg.ts) || null,
    tsTradeMs: Number(x.T) || null,
    tradeId: x.i || null,
    seq: Number(x.seq) || null,
    side: x.S || null,
    price: Number(x.p),
    size: Number(x.v),
    tickDirection: x.L || null,
    blockTrade: Boolean(x.BT),
    rpi: Boolean(x.RPI),
  })).filter((x) => Number.isFinite(x.tsTradeMs) && Number.isFinite(x.price) && Number.isFinite(x.size));
}

function liquidationRows(msg, recvMs) {
  return (Array.isArray(msg.data) ? msg.data : []).map((x) => ({
    schema: "liquidation-v1",
    exchange: "bybit",
    market: "linear",
    symbol: x.s || CONFIG.PERP_SYMBOL,
    tsRecvMs: recvMs,
    tsExchangeMsgMs: Number(msg.ts) || null,
    tsLiquidationMs: Number(x.T) || null,
    positionSide: x.S || null,
    size: Number(x.v),
    bankruptcyPrice: Number(x.p),
  })).filter((x) => Number.isFinite(x.tsLiquidationMs));
}


async function systemEvent(event) {
  stats.sockets[event.socket || "collector"] = event;
  try { await writer.write("system", { schema: "system-event-v1", ...event }, event.tsMs || Date.now()); }
  catch (err) { console.error("[system-event]", err); }
}

async function handleSpot(msg, recvMs) {
  const topic = String(msg.topic || "");
  if (topic.startsWith("publicTrade.")) {
    const rows = tradeRows(msg, "spot", recvMs);
    if (rows.length) {
      await writer.write("spot_trades", rows, rows[0].tsTradeMs);
      stats.records.spotTrades += rows.length;
      stats.last.spotTradeMs = rows.at(-1).tsTradeMs;
    }
    return;
  }
  if (topic.startsWith("orderbook.")) {
    spotBook.apply(msg, recvMs);
    if (CONFIG.SAVE_ORDERBOOK_DELTAS) {
      await writer.write("spot_orderbook_events", normalizeOrderbookEvent(msg, "spot", CONFIG.SPOT_SYMBOL, recvMs), recvMs);
      stats.records.orderbookEvents++;
    }
  }
}

async function handleLinear(msg, recvMs) {
  const topic = String(msg.topic || "");
  if (topic.startsWith("publicTrade.")) {
    const rows = tradeRows(msg, "linear", recvMs);
    if (rows.length) {
      await writer.write("perp_trades", rows, rows[0].tsTradeMs);
      stats.records.perpTrades += rows.length;
      stats.last.perpTradeMs = rows.at(-1).tsTradeMs;
    }
    return;
  }
  if (topic.startsWith("orderbook.")) {
    perpBook.apply(msg, recvMs);
    if (CONFIG.SAVE_ORDERBOOK_DELTAS) {
      await writer.write("perp_orderbook_events", normalizeOrderbookEvent(msg, "linear", CONFIG.PERP_SYMBOL, recvMs), recvMs);
      stats.records.orderbookEvents++;
    }
    return;
  }
  if (topic.startsWith("allLiquidation.")) {
    const rows = liquidationRows(msg, recvMs);
    if (rows.length) {
      await writer.write("liquidations", rows, rows[0].tsLiquidationMs);
      stats.records.liquidations += rows.length;
      stats.last.liquidationMs = rows.at(-1).tsLiquidationMs;
    }
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
  new BybitPublicSocket({
    name: "spot",
    url: CONFIG.WS_SPOT_URL,
    topics: spotTopics,
    onMessage: (msg, recvMs) => handleSpot(msg, recvMs).catch((err) => console.error("[spot message]", err)),
    onStatus: (e) => { console.log(`[ws:${e.socket}] ${e.state}`); void systemEvent(e); },
  }),
  new BybitPublicSocket({
    name: "linear",
    url: CONFIG.WS_LINEAR_URL,
    topics: linearTopics,
    onMessage: (msg, recvMs) => handleLinear(msg, recvMs).catch((err) => console.error("[linear message]", err)),
    onStatus: (e) => { console.log(`[ws:${e.socket}] ${e.state}`); void systemEvent(e); },
  }),
];

const obSampler = setInterval(async () => {
  const now = Date.now();
  try {
    if (CONFIG.COLLECT_SPOT_ORDERBOOK && spotBook.dirty) {
      const snap = spotBook.snapshot(now);
      if (snap) {
        await writer.write("spot_orderbook", snap, now);
        spotBook.markSaved(snap.bookVersion);
        stats.records.spotOrderbook++;
        stats.last.spotOrderbookMs = now;
      }
    }
    if (CONFIG.COLLECT_PERP_ORDERBOOK && perpBook.dirty) {
      const snap = perpBook.snapshot(now);
      if (snap) {
        await writer.write("perp_orderbook", snap, now);
        perpBook.markSaved(snap.bookVersion);
        stats.records.perpOrderbook++;
        stats.last.perpOrderbookMs = now;
      }
    }
  } catch (err) {
    console.error("[orderbook sampler]", err);
  }
}, CONFIG.ORDERBOOK_SAMPLE_MS);

const httpServer = createServer((req, res) => {
  if (req.url === "/" || req.url === "/status" || req.url === "/health") {
    const now = Date.now();
    const body = {
      status: shuttingDown ? "stopping" : "running",
      uptimeSec: Math.floor((now - startedAt) / 1000),
      config: {
        spotSymbol: CONFIG.SPOT_SYMBOL,
        perpSymbol: CONFIG.PERP_SYMBOL,
        orderbookDepth: CONFIG.ORDERBOOK_DEPTH,
        orderbookSampleMs: CONFIG.ORDERBOOK_SAMPLE_MS,
        saveOrderbookDeltas: CONFIG.SAVE_ORDERBOOK_DELTAS,
        rawBatchMaxMb: CONFIG.RAW_BATCH_MAX_MB,
      },
      storage: writer.status(),
      ...stats,
    };
    res.writeHead(200, { "content-type": "application/json; charset=utf-8" });
    res.end(JSON.stringify(body, null, 2));
    return;
  }
  res.writeHead(404, { "content-type": "text/plain" });
  res.end("not found\n");
});

httpServer.listen(CONFIG.STATUS_PORT, "127.0.0.1", () => {
  console.log(`[collector] status: http://127.0.0.1:${CONFIG.STATUS_PORT}/status`);
  console.log(`[collector] raw data: ${RAW_DIR}`);
  console.log(`[collector] batch rotation: ${CONFIG.RAW_BATCH_MAX_MB} MiB -> tar.gz`);
  console.log(`[collector] spot=${CONFIG.SPOT_SYMBOL}, perp=${CONFIG.PERP_SYMBOL}, OB L${CONFIG.ORDERBOOK_DEPTH}/${CONFIG.ORDERBOOK_SAMPLE_MS}ms`);
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
process.on("uncaughtException", (err) => {
  console.error("[collector] uncaughtException", err);
  void shutdown("uncaughtException", 1);
});
process.on("unhandledRejection", (err) => {
  console.error("[collector] unhandledRejection", err);
});
