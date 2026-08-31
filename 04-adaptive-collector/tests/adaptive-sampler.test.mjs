import test from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { AdaptiveSampler } from "../lib/adaptive-sampler.mjs";

const profile = {
  name: "winner", tauMs: 1000, gain: 0.95, transform: "sqrt_excess", maxHeat: 8,
  confluenceBonus: 0.22, quietTauMultiplier: 0.34, singleSensorMultiplier: 0.08,
  deadZone: { price: 1.2, flow: 1.45, book: 1.45 },
  shockEventFraction: { price: 0.9, flow: 0.9, book: 0.9 },
  weights: { price_range: .38, tick_travel: .22, trade_rate: .10, quote_volume: .06, book_churn: .14, imbalance_delta: .10 },
  intervalMap: [
    {name:"EXTREME",minHeat:3.05,intervalMs:800},{name:"ACTIVE",minHeat:1.75,intervalMs:1500},
    {name:"NORMAL",minHeat:.8,intervalMs:2500},{name:"QUIET",minHeat:0,intervalMs:5000}
  ]
};
const eventFloors={price_range_bp:.75,tick_travel_bp:1,trade_rate_s:40,quote_volume_s:50000,book_churn_s:250,imbalance_delta:.12};
const baselineFloors={price_range:.02,tick_travel:.02,trade_rate:.5,quote_volume:50,book_churn:5,imbalance_delta:.001};
const book={metrics:()=>({bookValid:1,imbalance:0})};

async function makeLoaded() {
  const root=await mkdtemp(path.join(os.tmpdir(),"adaptive-sampler-"));
  const file=path.join(root,"cal.json");
  await writeFile(file,JSON.stringify({primaryMarket:"spot",primarySymbol:"BTCUSDT",baselines:{price_range:.1,tick_travel:.1,trade_rate:10,quote_volume:10000,book_churn:100,imbalance_delta:.02},event_thresholds:eventFloors}));
  const s=await AdaptiveSampler.create({enabled:true,primaryMarket:"spot",primarySymbol:"BTCUSDT",topN:10,tickMs:250,fastWindowMs:1000,warmupMinutes:30,warmupIntervalMs:1000,calibrationFile:file,baselineQuantile:.5,eventQuantile:.995,eventFloors,baselineFloors,profile});
  return {s,root};
}

test("frozen winner profile loads calibration and is adaptive", async()=>{
  const {s,root}=await makeLoaded();
  try { assert.equal(s.status().calibrated,true); assert.equal(s.status().profile.tauMs,1000); }
  finally { await rm(root,{recursive:true,force:true}); }
});

test("price shock attacks immediately to 800ms", async()=>{
  const {s,root}=await makeLoaded();
  try {
    const now=Date.now();
    s.ingestTrade({market:"spot",symbol:"BTCUSDT",tsMs:now-900,price:100,size:1});
    s.ingestTrade({market:"spot",symbol:"BTCUSDT",tsMs:now-100,price:100.02,size:1}); // 2bp range > .9*.75
    const d=await s.evaluate(now,book);
    assert.equal(d.intervalMs,800); assert.equal(d.priceShock,true);
  } finally { await rm(root,{recursive:true,force:true}); }
});

test("quiet state releases heat rather than staying fast", async()=>{
  const {s,root}=await makeLoaded();
  try {
    const now=Date.now();
    s.heat=4;
    let d;
    for(let i=1;i<=20;i++) d=await s.evaluate(now+i*250,book);
    assert.ok(d.intervalMs>=2500,`expected release, got ${d.intervalMs}`);
  } finally { await rm(root,{recursive:true,force:true}); }
});
