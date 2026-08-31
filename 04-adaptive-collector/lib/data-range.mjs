import { open, readdir, stat } from "node:fs/promises";
import path from "node:path";

export function recordTimestamp(row) {
  for (const key of ["tsRecordMs", "tsTradeMs", "tsLiquidationMs", "tsMatchMs", "tsExchangeMs", "tsExchangeMsgMs", "tsSampleMs", "tsRecvMs", "timestampMs", "startMs", "tsMs"]) {
    const n = Number(row?.[key]);
    if (Number.isFinite(n) && n > 0) return n;
  }
  return null;
}

async function jsonlEdge(filePath, fromEnd) {
  const s = await stat(filePath);
  if (!s.size) return null;
  const fh = await open(filePath, "r");
  try {
    const chunk = Math.min(s.size, 128 * 1024);
    const start = fromEnd ? s.size - chunk : 0;
    const buf = Buffer.alloc(chunk);
    await fh.read(buf, 0, chunk, start);
    const lines = buf.toString("utf8").split(/\r?\n/).filter((x) => x.trim().startsWith("{"));
    const ordered = fromEnd ? lines.reverse() : lines;
    for (const line of ordered) {
      try {
        const row = JSON.parse(line);
        const ts = recordTimestamp(row);
        if (ts) return ts;
      } catch {}
    }
    return null;
  } finally {
    await fh.close();
  }
}

async function walk(dir) {
  const out = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...await walk(p));
    else if (entry.isFile() && entry.name.endsWith(".jsonl")) out.push(p);
  }
  return out;
}

export async function findCollectedRange(rawDir) {
  const files = await walk(rawDir);
  let minTs = null;
  let maxTs = null;
  const used = [];
  for (const file of files) {
    if (file.includes(`${path.sep}system${path.sep}`)) continue;
    const first = await jsonlEdge(file, false);
    const last = await jsonlEdge(file, true);
    if (first != null) minTs = minTs == null ? first : Math.min(minTs, first);
    if (last != null) maxTs = maxTs == null ? last : Math.max(maxTs, last);
    if (first != null || last != null) used.push({ file, first, last });
  }
  return { minTs, maxTs, files: used };
}
