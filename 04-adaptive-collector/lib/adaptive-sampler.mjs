import path from "node:path";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";

const SENSOR_KEYS = ["price_range", "tick_travel", "trade_rate", "quote_volume", "book_churn", "imbalance_delta"];
const PRICE = new Set(["price_range", "tick_travel"]);
const FLOW = new Set(["trade_rate", "quote_volume"]);
const BOOK = new Set(["book_churn", "imbalance_delta"]);

const EVENT_KEY = {
  price_range: "price_range_bp",
  tick_travel: "tick_travel_bp",
  trade_rate: "trade_rate_s",
  quote_volume: "quote_volume_s",
  book_churn: "book_churn_s",
  imbalance_delta: "imbalance_delta",
};

function finite(n, fallback = 0) {
  const x = Number(n);
  return Number.isFinite(x) ? x : fallback;
}

function quantile(values, q) {
  const a = values.filter(Number.isFinite).sort((x, y) => x - y);
  if (!a.length) return null;
  if (a.length === 1) return a[0];
  const p = Math.max(0, Math.min(1, q)) * (a.length - 1);
  const lo = Math.floor(p), hi = Math.ceil(p);
  if (lo === hi) return a[lo];
  const f = p - lo;
  return a[lo] * (1 - f) + a[hi] * f;
}

function transformRatio(ratio, dead, kind) {
  if (ratio <= dead) return 0;
  const e = Math.max(0, ratio / dead - 1);
  if (kind === "sqrt_excess") return Math.sqrt(e);
  if (kind === "linear_excess") return e;
  return Math.log1p(e);
}

function intervalFromHeat(heat, intervalMap) {
  const sorted = [...intervalMap].sort((a, b) => b.minHeat - a.minHeat);
  for (const x of sorted) if (heat >= x.minHeat) return { regime: x.name, intervalMs: x.intervalMs };
  const x = sorted.at(-1);
  return { regime: x.name, intervalMs: x.intervalMs };
}

async function writeJsonAtomic(filePath, value) {
  await mkdir(path.dirname(filePath), { recursive: true });
  const tmp = `${filePath}.tmp`;
  await writeFile(tmp, JSON.stringify(value, null, 2) + "\n", "utf8");
  await rename(tmp, filePath);
}

function calibrationCore(value) {
  if (!value || typeof value !== "object") return null;
  const baselines = value.baselines;
  const eventThresholds = value.event_thresholds || value.eventThresholds;
  if (!baselines || !eventThresholds) return null;
  for (const key of SENSOR_KEYS) {
    if (!(finite(baselines[key], NaN) > 0)) return null;
    if (!(finite(eventThresholds[EVENT_KEY[key]], NaN) > 0)) return null;
  }
  return { ...value, baselines, event_thresholds: eventThresholds };
}

export class AdaptiveSampler {
  static async create(options) {
    const x = new AdaptiveSampler(options);
    await x.#loadCalibration();
    return x;
  }

  constructor(options) {
    this.enabled = options.enabled !== false;
    this.primaryMarket = options.primaryMarket || "spot";
    this.primarySymbol = options.primarySymbol;
    this.topN = options.topN || 10;
    this.tickMs = options.tickMs || 250;
    this.fastWindowMs = options.fastWindowMs || 1000;
    this.warmupMs = (options.warmupMinutes || 30) * 60_000;
    this.calibrationFile = options.calibrationFile;
    this.baselineQuantile = options.baselineQuantile ?? 0.50;
    this.eventQuantile = options.eventQuantile ?? 0.995;
    this.eventFloors = options.eventFloors;
    this.baselineFloors = options.baselineFloors;
    this.profile = options.profile;
    this.expectedUniverse = options.expectedUniverse || null;
    this.trades = [];
    this.churn = [];
    this.heat = 0;
    this.prevIntervalMs = options.warmupIntervalMs || 1000;
    this.prevImbalance = 0;
    this.lastTickMs = null;
    this.lastPrimaryPrice = null;
    this.samples = [];
    this.warmupStartedMs = Date.now();
    this.calibration = null;
    this.calibrationSource = "warmup";
    this.calibrationId = null;
    this.lastDecision = {
      mode: this.enabled ? "warmup" : "fixed",
      regime: this.enabled ? "WARMUP" : "FIXED",
      intervalMs: options.warmupIntervalMs || 1000,
      heat: 0,
      reason: "startup",
      speedup: false,
      sensors: null,
    };
  }

  async #loadCalibration() {
    if (!this.enabled || !this.calibrationFile) return;
    try {
      const raw = JSON.parse(await readFile(this.calibrationFile, "utf8"));
      const cal = calibrationCore(raw);
      if (!cal) throw new Error("invalid calibration schema");
      const sym = cal.primarySymbol || cal.primary_symbol || null;
      const market = cal.primaryMarket || cal.primary_market || null;
      if (sym && String(sym).toUpperCase() !== String(this.primarySymbol).toUpperCase()) {
        throw new Error(`calibration symbol ${sym} != ${this.primarySymbol}`);
      }
      if (market && String(market) !== String(this.primaryMarket)) {
        throw new Error(`calibration market ${market} != ${this.primaryMarket}`);
      }
      const u = cal.sensorUniverse || cal.sensor_universe || null;
      if (u && this.expectedUniverse) {
        const checks = [
          ["spotSymbol", (a,b) => String(a).toUpperCase() === String(b).toUpperCase()],
          ["perpSymbol", (a,b) => String(a).toUpperCase() === String(b).toUpperCase()],
          ["orderbookDepth", (a,b) => Number(a) === Number(b)],
          ["fastWindowMs", (a,b) => Number(a) === Number(b)],
          ["tickMs", (a,b) => Number(a) === Number(b)],
          ["topLevels", (a,b) => Number(a) === Number(b)],
        ];
        for (const [key, eq] of checks) {
          if (u[key] != null && this.expectedUniverse[key] != null && !eq(u[key], this.expectedUniverse[key])) {
            throw new Error(`calibration sensorUniverse.${key} ${u[key]} != ${this.expectedUniverse[key]}`);
          }
        }
      }
      this.calibration = cal;
      this.calibrationSource = cal.source || "file";
      this.calibrationId = cal.calibrationId || cal.calibration_id || `file-${Date.now()}`;
      this.lastDecision.mode = "adaptive";
      this.lastDecision.regime = "QUIET";
      this.lastDecision.intervalMs = 5000;
      this.prevIntervalMs = 5000;
      console.log(`[adaptive] loaded calibration: ${this.calibrationFile}`);
    } catch (err) {
      console.warn(`[adaptive] calibration not loaded (${err.message}); starting ${Math.round(this.warmupMs / 60000)}m safe 1s warmup`);
    }
  }

  ingestTrade({ market, symbol, tsMs, price, size }) {
    const ts = finite(tsMs, 0), p = finite(price, NaN), q = finite(size, NaN);
    if (!(ts > 0) || !Number.isFinite(p) || !Number.isFinite(q)) return;
    this.trades.push({ ts, price: p, quote: p * q, market, symbol });
    if (market === this.primaryMarket && symbol === this.primarySymbol) this.lastPrimaryPrice = p;
  }

  ingestBookEvent({ tsMs, changedLevels }) {
    const ts = finite(tsMs, 0), changed = finite(changedLevels, 0);
    if (ts > 0 && changed > 0) this.churn.push({ ts, changed });
  }

  #purge(now) {
    const cutoff = now - Math.max(this.fastWindowMs + 5000, 6000);
    if (this.trades.length && this.trades[0].ts < cutoff) this.trades = this.trades.filter((x) => x.ts >= cutoff);
    if (this.churn.length && this.churn[0].ts < cutoff) this.churn = this.churn.filter((x) => x.ts >= cutoff);
  }

  sensors(now, primaryBook) {
    this.#purge(now);
    const lo = now - this.fastWindowMs;
    const allTrades = this.trades.filter((x) => x.ts > lo && x.ts <= now);
    const primaryTrades = allTrades.filter((x) => x.market === this.primaryMarket && x.symbol === this.primarySymbol);
    const prices = primaryTrades.map((x) => x.price);
    let priceRangeBp = 0, tickTravelBp = 0;
    if (prices.length) {
      const ref = prices[0] || 1;
      priceRangeBp = (Math.max(...prices) - Math.min(...prices)) / ref * 10_000;
      for (let i = 1; i < prices.length; i++) tickTravelBp += Math.abs(prices[i] - prices[i - 1]) / ref * 10_000;
      this.lastPrimaryPrice = prices.at(-1);
    }
    const sec = Math.max(this.fastWindowMs / 1000, 1e-9);
    const tradeRate = allTrades.length / sec;
    const quoteVolume = allTrades.reduce((s, x) => s + x.quote, 0) / sec;
    const bookChurn = this.churn.filter((x) => x.ts > lo && x.ts <= now).reduce((s, x) => s + x.changed, 0) / sec;
    const metrics = primaryBook?.metrics?.(this.topN) || null;
    const imbalance = Number.isFinite(metrics?.imbalance) ? metrics.imbalance : this.prevImbalance;
    const imbalanceDelta = Math.abs(imbalance - this.prevImbalance);
    return {
      price_range: priceRangeBp,
      tick_travel: tickTravelBp,
      trade_rate: tradeRate,
      quote_volume: quoteVolume,
      book_churn: bookChurn,
      imbalance_delta: imbalanceDelta,
      imbalance,
      priceLast: this.lastPrimaryPrice,
      bookValid: metrics?.bookValid ? 1 : 0,
    };
  }

  async evaluate(now, primaryBook) {
    const sensors = this.sensors(now, primaryBook);
    const dt = this.lastTickMs == null ? this.tickMs : Math.max(1, now - this.lastTickMs);
    this.lastTickMs = now;
    this.prevImbalance = sensors.imbalance;

    if (!this.enabled) {
      this.lastDecision = { mode: "fixed", regime: "FIXED", intervalMs: 1000, heat: 0, reason: "adaptive_disabled", speedup: false, sensors };
      return this.lastDecision;
    }

    if (!this.calibration) {
      this.samples.push({ tsMs: now, ...Object.fromEntries(SENSOR_KEYS.map((k) => [k, sensors[k]])) });
      if (now - this.warmupStartedMs >= this.warmupMs && this.samples.length >= 100) await this.#finishWarmup(now);
      if (!this.calibration) {
        const d = { mode: "warmup", regime: "WARMUP", intervalMs: 1000, heat: 0, reason: "calibrating", speedup: this.prevIntervalMs > 1000, sensors };
        this.prevIntervalMs = 1000;
        this.lastDecision = d;
        return d;
      }
    }

    const cal = this.calibration;
    const p = this.profile;
    const ratios = {};
    const eventRatios = {};
    for (const k of SENSOR_KEYS) {
      ratios[k] = sensors[k] / Math.max(finite(cal.baselines[k], 1e-9), 1e-9);
      eventRatios[k] = sensors[k] / Math.max(finite(cal.event_thresholds[EVENT_KEY[k]], 1e-9), 1e-9);
    }

    const pricePresent = [...PRICE].some((k) => ratios[k] > p.deadZone.price);
    const flowPresent = [...FLOW].some((k) => ratios[k] > p.deadZone.flow);
    const bookPresent = [...BOOK].some((k) => ratios[k] > p.deadZone.book);
    const confluence = flowPresent && bookPresent;
    const effectiveTau = (pricePresent || confluence) ? p.tauMs : Math.max(100, p.tauMs * p.quietTauMultiplier);
    this.heat *= Math.exp(-dt / effectiveTau);

    let energy = 0;
    const contrib = {};
    for (const k of SENSOR_KEYS) {
      const family = PRICE.has(k) ? "price" : FLOW.has(k) ? "flow" : "book";
      const z = transformRatio(ratios[k], p.deadZone[family], p.transform);
      const mult = (family !== "price" && !confluence) ? p.singleSensorMultiplier : 1;
      const v = p.weights[k] * z * mult;
      contrib[k] = v;
      energy += v;
    }
    if (confluence) energy += p.confluenceBonus;
    this.heat = Math.min(p.maxHeat, Math.max(0, this.heat + p.gain * energy * (dt / 1000)));

    let { regime, intervalMs } = intervalFromHeat(this.heat, p.intervalMap);
    const priceShock = eventRatios.price_range >= p.shockEventFraction.price || eventRatios.tick_travel >= p.shockEventFraction.price;
    const flowShock = eventRatios.trade_rate >= p.shockEventFraction.flow || eventRatios.quote_volume >= p.shockEventFraction.flow;
    const bookShock = eventRatios.book_churn >= p.shockEventFraction.book || eventRatios.imbalance_delta >= p.shockEventFraction.book;
    const microShock = flowShock && bookShock;
    let forced = "";
    if (priceShock && intervalMs > 800) {
      intervalMs = 800; regime = "EXTREME"; forced = "price_shock";
    } else if (microShock && intervalMs > 1500) {
      intervalMs = 1500; regime = "ACTIVE"; forced = "micro_shock";
    }
    const speedup = intervalMs < this.prevIntervalMs;
    const reason = forced || (speedup ? "speedup" : "heat");
    this.prevIntervalMs = intervalMs;
    this.lastDecision = {
      mode: "adaptive", regime, intervalMs, heat: this.heat, reason, speedup, sensors,
      energy, confluence, priceShock, microShock, effectiveTauMs: effectiveTau, contrib,
    };
    return this.lastDecision;
  }

  async #finishWarmup(now) {
    const baselines = {};
    const eventThresholds = {};
    for (const k of SENSOR_KEYS) {
      let values = this.samples.map((x) => finite(x[k], 0));
      if (k === "trade_rate" || k === "quote_volume") values = values.filter((x) => x > 0);
      const b = quantile(values, this.baselineQuantile);
      baselines[k] = Math.max(finite(b, 0), finite(this.baselineFloors[k], 1e-9), 1e-9);
      const e = quantile(this.samples.map((x) => finite(x[k], 0)), this.eventQuantile);
      eventThresholds[EVENT_KEY[k]] = Math.max(finite(e, 0), finite(this.eventFloors[EVENT_KEY[k]], 1e-9));
    }
    this.calibrationId = `live-${new Date(now).toISOString().replace(/[:.]/g, "-")}`;
    this.calibrationSource = "live_bootstrap";
    this.calibration = {
      schema: "adaptive-ob-calibration-v1",
      algorithm: "adaptive-ob-v0.4-winner",
      calibrationId: this.calibrationId,
      source: this.calibrationSource,
      createdAt: new Date(now).toISOString(),
      primaryMarket: this.primaryMarket,
      primarySymbol: this.primarySymbol,
      rows: this.samples.length,
      startMs: this.samples[0]?.tsMs || this.warmupStartedMs,
      endMs: now,
      baselines,
      event_thresholds: eventThresholds,
    };
    if (this.calibrationFile) {
      await writeJsonAtomic(this.calibrationFile, this.calibration);
      console.log(`[adaptive] warmup complete; calibration saved: ${this.calibrationFile}`);
    }
    this.samples = [];
  }

  status(now = Date.now()) {
    const warmupElapsed = now - this.warmupStartedMs;
    return {
      algorithm: "adaptive-ob-v0.4-winner",
      enabled: this.enabled,
      mode: this.lastDecision.mode,
      regime: this.lastDecision.regime,
      intervalMs: this.lastDecision.intervalMs,
      heat: +finite(this.lastDecision.heat, 0).toFixed(4),
      reason: this.lastDecision.reason,
      calibrationSource: this.calibrationSource,
      calibrationId: this.calibrationId,
      calibrationFile: this.calibrationFile,
      calibrated: Boolean(this.calibration),
      warmupSamples: this.samples.length,
      warmupRemainingSec: this.calibration ? 0 : Math.max(0, Math.ceil((this.warmupMs - warmupElapsed) / 1000)),
      lastSensors: this.lastDecision.sensors,
      profile: {
        tauMs: this.profile.tauMs,
        gain: this.profile.gain,
        transform: this.profile.transform,
        weights: this.profile.weights,
        deadZone: this.profile.deadZone,
        quietTauMultiplier: this.profile.quietTauMultiplier,
        singleSensorMultiplier: this.profile.singleSensorMultiplier,
        intervalMap: this.profile.intervalMap,
      },
    };
  }
}

export function validateCalibration(value) {
  return calibrationCore(value);
}
