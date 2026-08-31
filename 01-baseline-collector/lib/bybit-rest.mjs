import { mkdir, writeFile, rm, readdir, readFile, appendFile } from "node:fs/promises";
import path from "node:path";

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

export class BybitRest {
  constructor({ baseUrl, requestDelayMs = 80, retries = 4 }) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.requestDelayMs = requestDelayMs;
    this.retries = retries;
  }

  async get(endpoint, params = {}) {
    const url = new URL(`${this.baseUrl}${endpoint}`);
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
    }

    let lastError;
    for (let attempt = 0; attempt <= this.retries; attempt++) {
      try {
        const res = await fetch(url, { headers: { "user-agent": "market-ml-collector/1.0" } });
        const text = await res.text();
        let body;
        try { body = JSON.parse(text); } catch { throw new Error(`HTTP ${res.status}: invalid JSON: ${text.slice(0, 160)}`); }
        if (!res.ok) throw new Error(`HTTP ${res.status}: ${body?.retMsg || text.slice(0, 160)}`);
        if (body?.retCode != null && Number(body.retCode) !== 0) {
          throw new Error(`Bybit retCode=${body.retCode} retMsg=${body.retMsg || ""}`);
        }
        if (this.requestDelayMs) await sleep(this.requestDelayMs);
        return body;
      } catch (err) {
        lastError = err;
        if (attempt >= this.retries) break;
        await sleep(Math.min(8000, 750 * 2 ** attempt));
      }
    }
    throw lastError;
  }
}

function n(x) {
  const v = Number(x);
  return Number.isFinite(v) ? v : null;
}

function uniqueSorted(rows, tsField) {
  const map = new Map();
  for (const row of rows) {
    const ts = Number(row?.[tsField]);
    if (Number.isFinite(ts)) map.set(ts, row);
  }
  return [...map.values()].sort((a, b) => Number(a[tsField]) - Number(b[tsField]));
}

async function writeJsonl(filePath, rows) {
  await mkdir(path.dirname(filePath), { recursive: true });
  const body = rows.map((r) => JSON.stringify(r)).join("\n");
  await writeFile(filePath, body ? body + "\n" : "", "utf8");
}

function parseKlineRow(row, meta) {
  return {
    schema: "kline-v1",
    exchange: "bybit",
    series: meta.series,
    category: meta.category,
    symbol: meta.symbol,
    interval: meta.interval,
    startMs: n(row[0]),
    open: n(row[1]),
    high: n(row[2]),
    low: n(row[3]),
    close: n(row[4]),
    ...(row.length > 5 ? { volume: n(row[5]) } : {}),
    ...(row.length > 6 ? { turnover: n(row[6]) } : {}),
  };
}

export async function backfillKlines({ client, endpoint, category, symbol, interval = "1", startMs, endMs, outputFile, series }) {
  const tmpDir = `${outputFile}.parts`;
  await rm(tmpDir, { recursive: true, force: true });
  await mkdir(tmpDir, { recursive: true });

  let cursorEnd = endMs;
  let part = 0;
  let total = 0;
  let minTs = null;
  let maxTs = null;

  while (cursorEnd >= startMs) {
    const body = await client.get(endpoint, { category, symbol, interval, start: startMs, end: cursorEnd, limit: 1000 });
    const list = body?.result?.list;
    if (!Array.isArray(list) || !list.length) break;

    const parsed = list
      .map((r) => parseKlineRow(r, { series, category, symbol, interval }))
      .filter((r) => Number.isFinite(r.startMs) && r.startMs >= startMs && r.startMs <= endMs)
      .sort((a, b) => a.startMs - b.startMs);

    if (!parsed.length) break;
    const earliest = parsed[0].startMs;
    const latest = parsed.at(-1).startMs;
    minTs = minTs == null ? earliest : Math.min(minTs, earliest);
    maxTs = maxTs == null ? latest : Math.max(maxTs, latest);
    total += parsed.length;

    const partFile = path.join(tmpDir, `${String(part).padStart(6, "0")}.jsonl`);
    await writeJsonl(partFile, parsed);
    part++;

    if (earliest <= startMs) break;
    if (earliest >= cursorEnd) throw new Error(`${series}: pagination did not move backward`);
    cursorEnd = earliest - 1;
  }

  await mkdir(path.dirname(outputFile), { recursive: true });
  await writeFile(outputFile, "", "utf8");
  const parts = (await readdir(tmpDir)).filter((x) => x.endsWith(".jsonl")).sort().reverse();
  for (const p of parts) {
    await appendFile(outputFile, await readFile(path.join(tmpDir, p)));
  }
  await rm(tmpDir, { recursive: true, force: true });

  return { rows: total, minTs, maxTs, outputFile };
}

export async function backfillOpenInterest({ client, category, symbol, startMs, endMs, outputFile }) {
  let cursor = null;
  const seenCursors = new Set();
  const rows = [];
  do {
    const body = await client.get("/v5/market/open-interest", {
      category, symbol, intervalTime: "5min", startTime: startMs, endTime: endMs, limit: 200, cursor,
    });
    for (const x of body?.result?.list || []) {
      rows.push({
        schema: "open-interest-v1", exchange: "bybit", category, symbol, interval: "5min",
        timestampMs: n(x.timestamp), openInterest: n(x.openInterest), singleOpenInterest: n(x.singleOpenInterest),
      });
    }
    const nextCursor = body?.result?.nextPageCursor || null;
    if (nextCursor && seenCursors.has(nextCursor)) throw new Error("open-interest cursor loop detected");
    if (nextCursor) seenCursors.add(nextCursor);
    cursor = nextCursor;
  } while (cursor);
  const out = uniqueSorted(rows, "timestampMs").filter((r) => r.timestampMs >= startMs && r.timestampMs <= endMs);
  await writeJsonl(outputFile, out);
  return { rows: out.length, minTs: out[0]?.timestampMs ?? null, maxTs: out.at(-1)?.timestampMs ?? null, outputFile };
}

export async function backfillLongShort({ client, category, symbol, startMs, endMs, outputFile }) {
  let cursor = null;
  const seenCursors = new Set();
  const rows = [];
  do {
    const body = await client.get("/v5/market/account-ratio", {
      category, symbol, period: "5min", startTime: startMs, endTime: endMs, limit: 500, cursor,
    });
    for (const x of body?.result?.list || []) {
      rows.push({
        schema: "long-short-ratio-v1", exchange: "bybit", category, symbol, interval: "5min",
        timestampMs: n(x.timestamp), buyRatio: n(x.buyRatio), sellRatio: n(x.sellRatio),
      });
    }
    const nextCursor = body?.result?.nextPageCursor || null;
    if (nextCursor && seenCursors.has(nextCursor)) throw new Error("long-short cursor loop detected");
    if (nextCursor) seenCursors.add(nextCursor);
    cursor = nextCursor;
  } while (cursor);
  const out = uniqueSorted(rows, "timestampMs").filter((r) => r.timestampMs >= startMs && r.timestampMs <= endMs);
  await writeJsonl(outputFile, out);
  return { rows: out.length, minTs: out[0]?.timestampMs ?? null, maxTs: out.at(-1)?.timestampMs ?? null, outputFile };
}

export async function backfillFunding({ client, category, symbol, startMs, endMs, outputFile }) {
  let cursorEnd = endMs;
  const rows = [];
  while (cursorEnd >= startMs) {
    const body = await client.get("/v5/market/funding/history", {
      category, symbol, startTime: startMs, endTime: cursorEnd, limit: 200,
    });
    const list = body?.result?.list || [];
    if (!list.length) break;
    let earliest = null;
    for (const x of list) {
      const ts = n(x.fundingRateTimestamp);
      if (!Number.isFinite(ts)) continue;
      rows.push({ schema: "funding-v1", exchange: "bybit", category, symbol, timestampMs: ts, fundingRate: n(x.fundingRate) });
      earliest = earliest == null ? ts : Math.min(earliest, ts);
    }
    if (earliest == null || earliest <= startMs) break;
    if (earliest >= cursorEnd) throw new Error("funding pagination did not move backward");
    cursorEnd = earliest - 1;
  }
  const out = uniqueSorted(rows, "timestampMs").filter((r) => r.timestampMs >= startMs && r.timestampMs <= endMs);
  await writeJsonl(outputFile, out);
  return { rows: out.length, minTs: out[0]?.timestampMs ?? null, maxTs: out.at(-1)?.timestampMs ?? null, outputFile };
}

export async function saveInstrumentInfo({ client, category, symbol, outputFile }) {
  const body = await client.get("/v5/market/instruments-info", { category, symbol, limit: 1000 });
  await mkdir(path.dirname(outputFile), { recursive: true });
  await writeFile(outputFile, JSON.stringify(body?.result?.list?.[0] || body?.result || {}, null, 2) + "\n", "utf8");
  return { outputFile };
}
