import path from "node:path";
import { mkdir, writeFile } from "node:fs/promises";
import { CONFIG } from "./config.mjs";
import { discoverCollectedSources, mergeCollectedSources } from "./lib/collected-merge.mjs";
import {
  BybitRest,
  backfillKlines,
  backfillOpenInterest,
  backfillLongShort,
  backfillFunding,
  saveInstrumentInfo,
} from "./lib/bybit-rest.mjs";

function arg(name) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? process.argv[i + 1] : null;
}
function parseTime(raw) {
  if (!raw) return null;
  if (/^\d+$/.test(raw)) return Number(raw);
  const ms = Date.parse(raw);
  if (!Number.isFinite(ms)) throw new Error(`Niepoprawna data: ${raw}`);
  return ms;
}
function stamp(ms) { return new Date(ms).toISOString().replace(/[:.]/g, "-"); }

const input = path.resolve(arg("input") || path.join(CONFIG.DATA_DIR, "raw"));
const baseOutput = path.resolve(arg("output") || path.join(CONFIG.DATA_DIR, "complete"));

// 1) Najpierw inwentaryzujemy wszystkie zamknięte archiwa, ewentualny staging po awarii
//    oraz aktualną, jeszcze niezarchiwizowaną paczkę.
const sources = await discoverCollectedSources(input);
if (!sources.length) throw new Error(`Brak paczek danych w ${input}`);
const inferredStart = Math.min(...sources.map((x) => x.startMs).filter(Number.isFinite));
const inferredEnd = Math.max(...sources.map((x) => x.endMs).filter(Number.isFinite));
if (!Number.isFinite(inferredStart) || !Number.isFinite(inferredEnd)) throw new Error(`Nie udało się ustalić zakresu danych w ${input}`);

const observedStart = parseTime(arg("from")) || inferredStart;
const observedEnd = parseTime(arg("to")) || inferredEnd;
if (observedEnd <= observedStart) throw new Error("--to musi być później niż --from");

const nowSafe = Date.now() - 60_000;
const restStart = Math.max(0, observedStart - CONFIG.ENRICH_PREROLL_DAYS * 86_400_000);
const restEnd = Math.min(nowSafe, observedEnd + CONFIG.ENRICH_POSTROLL_HOURS * 3_600_000);
const runDir = path.join(baseOutput, `complete_${stamp(observedStart)}__${stamp(observedEnd)}`);
const restDir = path.join(runDir, "rest");
const collectedDir = path.join(runDir, "collected");
const mergedFile = path.join(collectedDir, "market_merged.jsonl");
await mkdir(restDir, { recursive: true });
await mkdir(collectedDir, { recursive: true });

// 2) Archiwa są sortowane wg dokładnego startMs z manifestów. Każde jest kolejno
//    rozpakowywane do katalogu tymczasowego i dopisywane do jednego pliku; po dopisaniu
//    katalog tymczasowy jest usuwany. Na końcu dokładamy bieżącą paczkę.
console.log(`[enrich] składam ${sources.length} paczek -> ${mergedFile}`);
const merge = await mergeCollectedSources({
  rawDir: input,
  outputFile: mergedFile,
  tempDir: path.join(runDir, ".merge-tmp"),
});
console.log(`[enrich] merged: ${merge.sources.length} źródeł, ~${merge.totalRows} rekordów`);
console.log(`[enrich] observed: ${new Date(observedStart).toISOString()} .. ${new Date(observedEnd).toISOString()}`);
console.log(`[enrich] REST:     ${new Date(restStart).toISOString()} .. ${new Date(restEnd).toISOString()}`);

const client = new BybitRest({
  baseUrl: CONFIG.REST_BASE,
  requestDelayMs: CONFIG.REST_REQUEST_DELAY_MS,
  retries: CONFIG.REST_RETRIES,
});

const jobs = [
  ["spot_1m", () => backfillKlines({ client, endpoint: "/v5/market/kline", category: "spot", symbol: CONFIG.SPOT_SYMBOL, interval: "1", startMs: restStart, endMs: restEnd, outputFile: path.join(restDir, "spot_1m.jsonl"), series: "spot_trade_price" })],
  ["perp_1m", () => backfillKlines({ client, endpoint: "/v5/market/kline", category: "linear", symbol: CONFIG.PERP_SYMBOL, interval: "1", startMs: restStart, endMs: restEnd, outputFile: path.join(restDir, "perp_1m.jsonl"), series: "perp_trade_price" })],
  ["mark_1m", () => backfillKlines({ client, endpoint: "/v5/market/mark-price-kline", category: "linear", symbol: CONFIG.PERP_SYMBOL, interval: "1", startMs: restStart, endMs: restEnd, outputFile: path.join(restDir, "mark_1m.jsonl"), series: "mark_price" })],
  ["index_1m", () => backfillKlines({ client, endpoint: "/v5/market/index-price-kline", category: "linear", symbol: CONFIG.PERP_SYMBOL, interval: "1", startMs: restStart, endMs: restEnd, outputFile: path.join(restDir, "index_1m.jsonl"), series: "index_price" })],
  ["premium_1m", () => backfillKlines({ client, endpoint: "/v5/market/premium-index-price-kline", category: "linear", symbol: CONFIG.PERP_SYMBOL, interval: "1", startMs: restStart, endMs: restEnd, outputFile: path.join(restDir, "premium_1m.jsonl"), series: "premium_index" })],
  ["open_interest_5m", () => backfillOpenInterest({ client, category: "linear", symbol: CONFIG.PERP_SYMBOL, startMs: restStart, endMs: restEnd, outputFile: path.join(restDir, "open_interest_5m.jsonl") })],
  ["long_short_5m", () => backfillLongShort({ client, category: "linear", symbol: CONFIG.PERP_SYMBOL, startMs: restStart, endMs: restEnd, outputFile: path.join(restDir, "long_short_5m.jsonl") })],
  ["funding", () => backfillFunding({ client, category: "linear", symbol: CONFIG.PERP_SYMBOL, startMs: restStart, endMs: restEnd, outputFile: path.join(restDir, "funding.jsonl") })],
  ["spot_instrument", () => saveInstrumentInfo({ client, category: "spot", symbol: CONFIG.SPOT_SYMBOL, outputFile: path.join(restDir, "spot_instrument.json") })],
  ["perp_instrument", () => saveInstrumentInfo({ client, category: "linear", symbol: CONFIG.PERP_SYMBOL, outputFile: path.join(restDir, "perp_instrument.json") })],
];

const results = {};
let failures = 0;
for (const [name, fn] of jobs) {
  process.stdout.write(`[enrich] ${name} ... `);
  try {
    const result = await fn();
    results[name] = { ok: true, ...result };
    console.log(`OK${result?.rows != null ? ` (${result.rows} rows)` : ""}`);
  } catch (err) {
    failures++;
    results[name] = { ok: false, error: err?.message || String(err) };
    console.log(`ERROR: ${results[name].error}`);
  }
}

const manifest = {
  schema: "market-ml-complete-v2",
  createdAt: new Date().toISOString(),
  input,
  mergedCollectedFile: mergedFile,
  sourceBatches: merge.sources.map((x) => ({ type: x.type, batchId: x.batchId, path: x.path, startMs: x.startMs, endMs: x.endMs, rows: x.rows })),
  observedRange: { startMs: observedStart, endMs: observedEnd, start: new Date(observedStart).toISOString(), end: new Date(observedEnd).toISOString() },
  restRange: { startMs: restStart, endMs: restEnd, start: new Date(restStart).toISOString(), end: new Date(restEnd).toISOString() },
  config: { spotSymbol: CONFIG.SPOT_SYMBOL, perpSymbol: CONFIG.PERP_SYMBOL, restBase: CONFIG.REST_BASE, prerollDays: CONFIG.ENRICH_PREROLL_DAYS, postrollHours: CONFIG.ENRICH_POSTROLL_HOURS, rawBatchMaxMb: CONFIG.RAW_BATCH_MAX_MB },
  results,
  failures,
};
await writeFile(path.join(runDir, "manifest.json"), JSON.stringify(manifest, null, 2) + "\n", "utf8");
console.log(`[enrich] komplet: ${runDir}`);
if (failures) {
  console.error(`[enrich] ${failures} job(s) nie udało się. Dane częściowe zostały zachowane, szczegóły w manifest.json.`);
  process.exitCode = 2;
}
