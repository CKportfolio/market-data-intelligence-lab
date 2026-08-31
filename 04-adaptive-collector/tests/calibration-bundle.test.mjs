import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import os from "node:os";
import { mkdtemp, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { AdaptiveSampler, validateCalibration } from "../lib/adaptive-sampler.mjs";
import { readFile } from "node:fs/promises";

const here = path.dirname(fileURLToPath(import.meta.url));
const bundle = path.resolve(here, "../calibration/research-v04-btcusdc-spot-btcusdt-perp.json");
const profile = {
  name: "test", tauMs: 1000, gain: 0.95, transform: "sqrt_excess", maxHeat: 8,
  confluenceBonus: 0.22, quietTauMultiplier: 0.34, singleSensorMultiplier: 0.08,
  deadZone: { price: 1.2, flow: 1.45, book: 1.45 },
  shockEventFraction: { price: .9, flow: .9, book: .9 },
  weights: { price_range:.38,tick_travel:.22,trade_rate:.10,quote_volume:.06,book_churn:.14,imbalance_delta:.10 },
  intervalMap: [{name:"EXTREME",minHeat:3.05,intervalMs:800},{name:"ACTIVE",minHeat:1.75,intervalMs:1500},{name:"NORMAL",minHeat:.8,intervalMs:2500},{name:"QUIET",minHeat:0,intervalMs:5000}],
};
const common = { enabled:true, primaryMarket:"spot", primarySymbol:"BTCUSDC", topN:10, tickMs:250, fastWindowMs:1000, warmupMinutes:30, warmupIntervalMs:1000, baselineQuantile:.5, eventQuantile:.995, eventFloors:{price_range_bp:.75,tick_travel_bp:1,trade_rate_s:40,quote_volume_s:50000,book_churn_s:250,imbalance_delta:.12}, baselineFloors:{price_range:.02,tick_travel:.02,trade_rate:.5,quote_volume:50,book_churn:5,imbalance_delta:.001}, profile };

test("bundled calibration contains exact supplied research values", async () => {
  const raw = JSON.parse(await readFile(bundle,"utf8"));
  assert.ok(validateCalibration(raw));
  assert.equal(raw.primarySymbol,"BTCUSDC");
  assert.equal(raw.sensorUniverse.perpSymbol,"BTCUSDT");
  assert.equal(raw.baselines.trade_rate,6);
  assert.equal(raw.event_thresholds.book_churn_s,1752.9100000000035);
  assert.equal(raw.rows,140419);
});

test("bundled calibration loads for exact research sensor universe", async () => {
  const s = await AdaptiveSampler.create({...common, calibrationFile:bundle, expectedUniverse:{spotSymbol:"BTCUSDC",perpSymbol:"BTCUSDT",orderbookDepth:50,fastWindowMs:1000,tickMs:250,topLevels:10}});
  assert.equal(s.status().calibrated,true);
  assert.equal(s.status().calibrationSource,"research_v04_bundled");
});

test("research calibration is rejected for mismatched spot symbol", async () => {
  const s = await AdaptiveSampler.create({...common, primarySymbol:"BTCUSDT", calibrationFile:bundle, expectedUniverse:{spotSymbol:"BTCUSDT",perpSymbol:"BTCUSDT",orderbookDepth:50,fastWindowMs:1000,tickMs:250,topLevels:10}});
  assert.equal(s.status().calibrated,false);
  assert.equal(s.status().mode,"warmup");
});
