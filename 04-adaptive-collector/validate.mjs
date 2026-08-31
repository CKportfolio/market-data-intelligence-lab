import path from "node:path";
import { createReadStream } from "node:fs";
import readline from "node:readline";
import { CONFIG } from "./config.mjs";
import { discoverCollectedSources } from "./lib/collected-merge.mjs";
import { recordTimestamp } from "./lib/data-range.mjs";
import { extractArchive } from "./lib/archive-utils.mjs";
import { mkdir, readdir, rm } from "node:fs/promises";

function arg(name) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? process.argv[i + 1] : null;
}

async function findMarketFile(root) {
  const stack = [root];
  while (stack.length) {
    const dir = stack.pop();
    for (const e of await readdir(dir, { withFileTypes: true })) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) stack.push(p);
      else if (e.isFile() && e.name === "market.jsonl") return p;
    }
  }
  return null;
}

async function validateFile(file) {
  let rows = 0, bad = 0, minTs = null, maxTs = null, backwards = 0, prevTs = null, badBook = 0;
  const rl = readline.createInterface({ input: createReadStream(file), crlfDelay: Infinity });
  for await (const line of rl) {
    if (!line.trim()) continue;
    rows++;
    try {
      const r = JSON.parse(line);
      const ts = recordTimestamp(r);
      if (ts != null) {
        minTs = minTs == null ? ts : Math.min(minTs, ts);
        maxTs = maxTs == null ? ts : Math.max(maxTs, ts);
        if (prevTs != null && ts < prevTs) backwards++;
        prevTs = ts;
      }
      if (r.schema === "orderbook-snapshot-v1") {
        if (!Array.isArray(r.bids) || !r.bids.length || !Array.isArray(r.asks) || !r.asks.length || Number(r.bestBid) >= Number(r.bestAsk)) badBook++;
      }
    } catch { bad++; }
  }
  return { rows, bad, minTs, maxTs, backwards, badBook };
}

const input = path.resolve(arg("input") || path.join(CONFIG.DATA_DIR, "raw"));
const sources = await discoverCollectedSources(input);
if (!sources.length) throw new Error(`Brak paczek danych w ${input}`);
const tempRoot = path.join(input, ".validate-tmp");
await rm(tempRoot, { recursive: true, force: true });
await mkdir(tempRoot, { recursive: true });
let globalBad = 0;
try {
  for (let i = 0; i < sources.length; i++) {
    const src = sources[i];
    let file = src.filePath;
    let tmp = null;
    if (src.type === "archive") {
      tmp = path.join(tempRoot, String(i));
      await extractArchive(src.path, tmp);
      file = await findMarketFile(tmp);
    }
    if (!file) continue;
    const v = await validateFile(file);
    globalBad += v.bad;
    console.log(`${src.type}:${path.basename(src.path)} rows=${v.rows} badJson=${v.bad} backwards=${v.backwards} badBook=${v.badBook} range=${v.minTs ? new Date(v.minTs).toISOString() : "?"} .. ${v.maxTs ? new Date(v.maxTs).toISOString() : "?"}`);
    if (tmp) await rm(tmp, { recursive: true, force: true });
  }
} finally {
  await rm(tempRoot, { recursive: true, force: true });
}
if (globalBad) process.exitCode = 2;
