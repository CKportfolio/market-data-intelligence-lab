import test from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import { mkdtemp, readFile, readdir, rm, stat } from "node:fs/promises";
import { RotatingArchiveWriter } from "../lib/rotating-archive-writer.mjs";
import { discoverCollectedSources, mergeCollectedSources } from "../lib/collected-merge.mjs";

async function exists(p) {
  try { await stat(p); return true; }
  catch (e) { if (e?.code === "ENOENT") return false; throw e; }
}

test("micro rotation -> tar.gz -> delete staging -> merge archives + current exactly once", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "market-ml-rotation-"));
  const raw = path.join(root, "raw");
  const output = path.join(root, "merged.jsonl");
  const tmp = path.join(root, "tmp");
  try {
    // ~1.8 KiB wymusza wiele rotacji na mikro-danych.
    const writer = await new RotatingArchiveWriter(raw, { maxBytes: 1800 }).init();
    const expected = [];
    const base = Date.UTC(2026, 7, 27, 12, 0, 0);
    for (let i = 0; i < 60; i++) {
      const row = {
        schema: "test-v1",
        tsTradeMs: base + i * 1000,
        id: i,
        price: 100000 + i,
        payload: "x".repeat(120),
      };
      expected.push(i);
      await writer.write(i % 2 ? "spot_trades" : "perp_trades", row, row.tsTradeMs);
    }
    await writer.close();

    const archivesDir = path.join(raw, "archives");
    const archives = (await readdir(archivesDir)).filter((x) => x.endsWith(".tar.gz")).sort();
    assert.ok(archives.length >= 3, `spodziewano się >=3 archiwów, jest ${archives.length}`);
    assert.ok(archives.every((x) => !x.includes(":")), "nazwa archiwum musi być zgodna z Windows");

    const staging = await readdir(path.join(raw, "staging"));
    assert.deepEqual(staging, [], "po poprawnym spakowaniu staging powinien być pusty");
    assert.ok(await exists(path.join(raw, "live", "current", "market.jsonl")), "bieżąca niedomknięta paczka powinna zostać");

    const sources = await discoverCollectedSources(raw);
    assert.equal(sources.length, archives.length + 1, "źródła = archiwa + current");
    for (let i = 1; i < sources.length; i++) {
      assert.ok(sources[i - 1].startMs <= sources[i].startMs, "paczki powinny być uporządkowane po startMs");
    }

    await mergeCollectedSources({ rawDir: raw, outputFile: output, tempDir: tmp });
    const lines = (await readFile(output, "utf8")).trim().split(/\r?\n/).filter(Boolean);
    const actual = lines.map((line) => JSON.parse(line).id);
    assert.deepEqual(actual, expected, "po scaleniu każdy rekord ma wystąpić dokładnie raz i w kolejności paczek");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
