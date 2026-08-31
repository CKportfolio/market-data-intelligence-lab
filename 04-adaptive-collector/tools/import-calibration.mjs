import path from "node:path";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { CONFIG } from "../config.mjs";
import { validateCalibration } from "../lib/adaptive-sampler.mjs";

const source = process.argv[2];
if (!source) throw new Error("Usage: node tools/import-calibration.mjs /path/to/research/calibration.json");
const raw = JSON.parse(await readFile(path.resolve(source), "utf8"));
const cal = validateCalibration(raw);
if (!cal) throw new Error("Plik nie ma poprawnych baselines/event_thresholds");
const wrapped = {
  ...cal,
  schema: "adaptive-ob-calibration-v1",
  algorithm: "adaptive-ob-v0.4-winner",
  calibrationId: cal.calibrationId || `research-${new Date().toISOString().replace(/[:.]/g, "-")}`,
  source: "research_import",
  primaryMarket: "spot",
  primarySymbol: CONFIG.SPOT_SYMBOL,
  sensorUniverse: {
    spotSymbol: CONFIG.SPOT_SYMBOL,
    perpSymbol: CONFIG.PERP_SYMBOL,
    orderbookDepth: CONFIG.ORDERBOOK_DEPTH,
    fastWindowMs: CONFIG.ADAPTIVE_FAST_WINDOW_MS,
    tickMs: CONFIG.ADAPTIVE_TICK_MS,
    topLevels: CONFIG.ADAPTIVE_TOP_LEVELS,
  },
  importedAt: new Date().toISOString(),
};
const dest = CONFIG.ADAPTIVE_CALIBRATION_FILE;
await mkdir(path.dirname(dest), { recursive: true });
const tmp = `${dest}.tmp`;
await writeFile(tmp, JSON.stringify(wrapped, null, 2) + "\n", "utf8");
await rename(tmp, dest);
console.log(`OK: ${dest}`);
console.log(`primary: spot ${CONFIG.SPOT_SYMBOL}`);
