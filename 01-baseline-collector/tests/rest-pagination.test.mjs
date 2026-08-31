import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { backfillKlines } from "../lib/bybit-rest.mjs";

test("kline backfill pages backward and writes ascending JSONL", async () => {
  const calls = [];
  const fake = {
    async get(endpoint, params) {
      calls.push({ endpoint, ...params });
      const all = [1000, 2000, 3000, 4000, 5000].filter((t) => t >= params.start && t <= params.end);
      const picked = all.sort((a, b) => b - a).slice(0, 2);
      return { result: { list: picked.map((t) => [String(t), "1", "2", "0.5", "1.5", "10", "15"]) } };
    },
  };
  const dir = await mkdtemp(path.join(os.tmpdir(), "ml-rest-"));
  const out = path.join(dir, "x.jsonl");
  try {
    const r = await backfillKlines({ client: fake, endpoint: "/v5/market/kline", category: "spot", symbol: "BTCUSDT", interval: "1", startMs: 1000, endMs: 5000, outputFile: out, series: "spot" });
    assert.equal(r.rows, 5);
    const rows = (await readFile(out, "utf8")).trim().split("\n").map(JSON.parse);
    assert.deepEqual(rows.map((x) => x.startMs), [1000, 2000, 3000, 4000, 5000]);
    assert.ok(calls.length >= 3);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});
