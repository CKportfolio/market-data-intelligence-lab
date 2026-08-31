import path from "node:path";
import { createReadStream, createWriteStream } from "node:fs";
import { once } from "node:events";
import readline from "node:readline";
import { mkdir, readdir, readFile, rm, stat } from "node:fs/promises";
import { extractArchive, readArchiveManifest } from "./archive-utils.mjs";
import { recordTimestamp } from "./data-range.mjs";

async function exists(p) {
  try { await stat(p); return true; }
  catch (err) { if (err?.code === "ENOENT") return false; throw err; }
}

async function inspectMarketFile(filePath) {
  if (!await exists(filePath)) return null;
  const s = await stat(filePath);
  if (!s.size) return null;
  let startMs = null, endMs = null, rows = 0;
  const rl = readline.createInterface({ input: createReadStream(filePath), crlfDelay: Infinity });
  for await (const line of rl) {
    if (!line.trim()) continue;
    try {
      const row = JSON.parse(line);
      const ts = Number(row.tsRecordMs) || recordTimestamp(row);
      if (Number.isFinite(ts)) {
        startMs = startMs == null ? ts : Math.min(startMs, ts);
        endMs = endMs == null ? ts : Math.max(endMs, ts);
      }
      rows++;
    } catch {}
  }
  return { startMs, endMs, rows, bytes: s.size };
}

async function listFiles(dir, suffix) {
  try {
    return (await readdir(dir, { withFileTypes: true }))
      .filter((e) => e.isFile() && e.name.endsWith(suffix))
      .map((e) => path.join(dir, e.name));
  } catch (err) {
    if (err?.code === "ENOENT") return [];
    throw err;
  }
}

async function listDirs(dir) {
  try {
    return (await readdir(dir, { withFileTypes: true })).filter((e) => e.isDirectory()).map((e) => path.join(dir, e.name));
  } catch (err) {
    if (err?.code === "ENOENT") return [];
    throw err;
  }
}

export async function discoverCollectedSources(rawDir) {
  const result = [];

  for (const archivePath of await listFiles(path.join(rawDir, "archives"), ".tar.gz")) {
    const manifest = await readArchiveManifest(archivePath);
    result.push({ type: "archive", path: archivePath, batchId: manifest.batchId, startMs: manifest.startMs, endMs: manifest.endMs, rows: manifest.rows, manifest });
  }

  for (const dir of await listDirs(path.join(rawDir, "staging"))) {
    const manifestPath = path.join(dir, "manifest.json");
    const filePath = path.join(dir, "market.jsonl");
    if (!await exists(manifestPath) || !await exists(filePath)) continue;
    const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
    result.push({ type: "staging", path: dir, filePath, batchId: manifest.batchId, startMs: manifest.startMs, endMs: manifest.endMs, rows: manifest.rows, manifest });
  }

  const currentFile = path.join(rawDir, "live", "current", "market.jsonl");
  const currentMeta = path.join(rawDir, "live", "current", "batch.json");
  const inspected = await inspectMarketFile(currentFile);
  if (inspected?.startMs != null) {
    let meta = {};
    try { meta = JSON.parse(await readFile(currentMeta, "utf8")); } catch {}
    result.push({ type: "current", path: currentFile, filePath: currentFile, batchId: meta.batchId || "current", ...inspected });
  }

  // Po awarii archiwum mogło już powstać, a staging nie zostać skasowany. Archiwum ma pierwszeństwo.
  const priority = { archive: 3, staging: 2, current: 1 };
  const dedup = new Map();
  for (const src of result) {
    const old = dedup.get(src.batchId);
    if (!old || priority[src.type] > priority[old.type]) dedup.set(src.batchId, src);
  }
  return [...dedup.values()].sort((a, b) => (a.startMs ?? Infinity) - (b.startMs ?? Infinity) || (a.endMs ?? Infinity) - (b.endMs ?? Infinity));
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
  throw new Error(`Po rozpakowaniu nie znaleziono market.jsonl w ${root}`);
}

async function appendFile(sourceFile, outStream) {
  const input = createReadStream(sourceFile);
  for await (const chunk of input) {
    if (!outStream.write(chunk)) await once(outStream, "drain");
  }
}

export async function mergeCollectedSources({ rawDir, outputFile, tempDir }) {
  const sources = await discoverCollectedSources(rawDir);
  if (!sources.length) throw new Error(`Brak nowych paczek danych w ${rawDir}`);
  await mkdir(path.dirname(outputFile), { recursive: true });
  await rm(tempDir, { recursive: true, force: true });
  await mkdir(tempDir, { recursive: true });

  const out = createWriteStream(outputFile, { flags: "w", highWaterMark: 1 << 20 });
  let totalRows = 0;
  try {
    for (let i = 0; i < sources.length; i++) {
      const src = sources[i];
      let sourceFile;
      if (src.type === "archive") {
        const dest = path.join(tempDir, `archive_${String(i).padStart(5, "0")}`);
        await extractArchive(src.path, dest);
        sourceFile = await findMarketFile(dest);
        await appendFile(sourceFile, out);
        await rm(dest, { recursive: true, force: true });
      } else {
        sourceFile = src.filePath;
        await appendFile(sourceFile, out);
      }
      totalRows += Number(src.rows) || 0;
    }
  } finally {
    out.end();
    await once(out, "finish");
    await rm(tempDir, { recursive: true, force: true });
  }

  const startMs = Math.min(...sources.map((x) => x.startMs).filter(Number.isFinite));
  const endMs = Math.max(...sources.map((x) => x.endMs).filter(Number.isFinite));
  return { sources, startMs, endMs, totalRows, outputFile };
}
