import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

function envBool(name, fallback) {
  const raw = process.env[name];
  if (raw == null || raw === "") return fallback;
  return /^(1|true|yes|on)$/i.test(raw);
}
function envInt(name, fallback, min = Number.MIN_SAFE_INTEGER, max = Number.MAX_SAFE_INTEGER) {
  const n = Number(process.env[name]);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(min, Math.min(max, Math.trunc(n)));
}
function envFloat(name, fallback, min = -Infinity, max = Infinity) {
  const n = Number(process.env[name]);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(min, Math.min(max, n));
}

const DATA_DIR = path.resolve(process.env.DATA_DIR || path.join(__dirname, "data"));
const SPOT_SYMBOL = (process.env.SPOT_SYMBOL || "BTCUSDT").toUpperCase();
const PERP_SYMBOL = (process.env.PERP_SYMBOL || "BTCUSDT").toUpperCase();
const BUNDLED_RESEARCH_CALIBRATION = path.join(__dirname, "calibration", "research-v04-btcusdc-spot-btcusdt-perp.json");
const RESEARCH_CALIBRATION_COMPATIBLE = SPOT_SYMBOL === "BTCUSDC" && PERP_SYMBOL === "BTCUSDT";

export const CONFIG = {
  REST_BASE: process.env.BYBIT_REST_BASE || "https://api.bybit.com",
  WS_SPOT_URL: process.env.BYBIT_WS_SPOT_URL || "wss://stream.bybit.com/v5/public/spot",
  WS_LINEAR_URL: process.env.BYBIT_WS_LINEAR_URL || "wss://stream.bybit.com/v5/public/linear",

  SPOT_SYMBOL,
  PERP_SYMBOL,
  DATA_DIR,
  STATUS_PORT: envInt("STATUS_PORT", 3043, 1, 65535),

  ORDERBOOK_DEPTH: envInt("ORDERBOOK_DEPTH", 50, 1, 1000),
  SAVE_ORDERBOOK_DELTAS: envBool("SAVE_ORDERBOOK_DELTAS", false),
  ORDERBOOK_WRITE_UNCHANGED: envBool("ORDERBOOK_WRITE_UNCHANGED", true),

  // 900 MiB RAW ~= usually tens of MiB to around ~100 MiB after gzip, depending on market mix.
  RAW_BATCH_MAX_MB: envInt("RAW_BATCH_MAX_MB", 900, 1, 10_240),
  // Different live/staging namespace allows old and new collectors to overlap safely during PM2 handoff.
  RAW_LIVE_NAME: process.env.RAW_LIVE_NAME || "current-adaptive",

  COLLECT_SPOT_TRADES: envBool("COLLECT_SPOT_TRADES", true),
  COLLECT_SPOT_ORDERBOOK: envBool("COLLECT_SPOT_ORDERBOOK", true),
  COLLECT_PERP_TRADES: envBool("COLLECT_PERP_TRADES", true),
  COLLECT_PERP_ORDERBOOK: envBool("COLLECT_PERP_ORDERBOOK", true),
  COLLECT_LIQUIDATIONS: envBool("COLLECT_LIQUIDATIONS", true),

  ADAPTIVE_ENABLED: envBool("ADAPTIVE_ENABLED", true),
  ADAPTIVE_TICK_MS: envInt("ADAPTIVE_TICK_MS", 250, 100, 1000),
  ADAPTIVE_FAST_WINDOW_MS: envInt("ADAPTIVE_FAST_WINDOW_MS", 1000, 250, 5000),
  ADAPTIVE_TOP_LEVELS: envInt("ADAPTIVE_TOP_LEVELS", 10, 1, 50),
  ADAPTIVE_WARMUP_MINUTES: envInt("ADAPTIVE_WARMUP_MINUTES", 30, 1, 240),
  ADAPTIVE_WARMUP_INTERVAL_MS: envInt("ADAPTIVE_WARMUP_INTERVAL_MS", 1000, 250, 5000),
  // Exact research calibration is bundled and automatically selected only for the exact
  // BTCUSDC Spot + BTCUSDT Perp sensor universe used by the v0.4 research.
  // Other symbol combinations preserve continuity and safely fall back to a per-data-dir live calibration.
  ADAPTIVE_CALIBRATION_FILE: path.resolve(process.env.ADAPTIVE_CALIBRATION_FILE || (RESEARCH_CALIBRATION_COMPATIBLE && envBool("ADAPTIVE_USE_BUNDLED_RESEARCH_CALIBRATION", true) ? BUNDLED_RESEARCH_CALIBRATION : path.join(DATA_DIR, "adaptive", "calibration.json"))),
  ADAPTIVE_CALIBRATION_EXPECTED_UNIVERSE: {
    spotSymbol: SPOT_SYMBOL,
    perpSymbol: PERP_SYMBOL,
    orderbookDepth: envInt("ORDERBOOK_DEPTH", 50, 1, 1000),
    fastWindowMs: envInt("ADAPTIVE_FAST_WINDOW_MS", 1000, 250, 5000),
    tickMs: envInt("ADAPTIVE_TICK_MS", 250, 100, 1000),
    topLevels: envInt("ADAPTIVE_TOP_LEVELS", 10, 1, 50),
  },
  ADAPTIVE_BUNDLED_RESEARCH_CALIBRATION: BUNDLED_RESEARCH_CALIBRATION,
  ADAPTIVE_RESEARCH_CALIBRATION_COMPATIBLE: RESEARCH_CALIBRATION_COMPATIBLE,
  ADAPTIVE_BASELINE_QUANTILE: envFloat("ADAPTIVE_BASELINE_QUANTILE", 0.50, 0.01, 0.99),
  ADAPTIVE_EVENT_QUANTILE: envFloat("ADAPTIVE_EVENT_QUANTILE", 0.995, 0.90, 0.9999),

  // Exact v0.4 calibration floors. Live bootstrap computes medians/q99.5 and never goes below these event floors.
  ADAPTIVE_EVENT_FLOORS: {
    price_range_bp: envFloat("ADAPTIVE_FLOOR_PRICE_RANGE_BP", 0.75, 0),
    tick_travel_bp: envFloat("ADAPTIVE_FLOOR_TICK_TRAVEL_BP", 1.00, 0),
    trade_rate_s: envFloat("ADAPTIVE_FLOOR_TRADE_RATE_S", 40.0, 0),
    quote_volume_s: envFloat("ADAPTIVE_FLOOR_QUOTE_VOLUME_S", 50_000.0, 0),
    book_churn_s: envFloat("ADAPTIVE_FLOOR_BOOK_CHURN_S", 250.0, 0),
    imbalance_delta: envFloat("ADAPTIVE_FLOOR_IMBALANCE_DELTA", 0.12, 0),
  },
  // Only protects live bootstrap from pathological zero medians. These are not event gates.
  ADAPTIVE_BASELINE_FLOORS: {
    price_range: 0.02,
    tick_travel: 0.02,
    trade_rate: 0.5,
    quote_volume: 50,
    book_churn: 5,
    imbalance_delta: 0.001,
  },

  // Frozen calibration winner from the research: asym-sqrt_excess-price_heavy-clean_release-tau1000-g0.95
  ADAPTIVE_PROFILE: {
    name: "asym-sqrt_excess-price_heavy-clean_release-tau1000-g0.95",
    tauMs: 1000,
    gain: 0.95,
    transform: "sqrt_excess",
    maxHeat: 8.0,
    confluenceBonus: 0.22,
    quietTauMultiplier: 0.34,
    singleSensorMultiplier: 0.08,
    deadZone: { price: 1.20, flow: 1.45, book: 1.45 },
    shockEventFraction: { price: 0.90, flow: 0.90, book: 0.90 },
    weights: {
      price_range: 0.38,
      tick_travel: 0.22,
      trade_rate: 0.10,
      quote_volume: 0.06,
      book_churn: 0.14,
      imbalance_delta: 0.10,
    },
    intervalMap: [
      { name: "EXTREME", minHeat: 3.05, intervalMs: 800 },
      { name: "ACTIVE", minHeat: 1.75, intervalMs: 1500 },
      { name: "NORMAL", minHeat: 0.80, intervalMs: 2500 },
      { name: "QUIET", minHeat: 0.00, intervalMs: 5000 },
    ],
  },

};
