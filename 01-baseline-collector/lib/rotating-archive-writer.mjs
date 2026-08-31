import path from "node:path";
import crypto from "node:crypto";
import { createReadStream, createWriteStream } from "node:fs";
import { once } from "node:events";
import readline from "node:readline";
import { mkdir, readFile, rename, stat, writeFile } from "node:fs/promises";
import { archiveBatchDir, childDirectories, humanRangeLabel, writeJsonAtomic } from "./archive-utils.mjs";
import { recordTimestamp } from "./data-range.mjs";

async function exists(filePath) {
  try { await stat(filePath); return true; }
  catch (err) { if (err?.code === "ENOENT") return false; throw err; }
}

function makeBatchId() {
  return `batch_${new Date().toISOString().replace(/[:.]/g, "-")}_${crypto.randomBytes(4).toString("hex")}`;
}

async function inspectJsonl(filePath) {
  if (!await exists(filePath)) return { bytes: 0, startMs: null, endMs: null, rows: 0, counts: {} };
  const s = await stat(filePath);
  if (!s.size) return { bytes: 0, startMs: null, endMs: null, rows: 0, counts: {} };
  let startMs = null, endMs = null, rows = 0;
  const counts = {};
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
      const channel = row._channel || "unknown";
      counts[channel] = (counts[channel] || 0) + 1;
      rows++;
    } catch {}
  }
  return { bytes: s.size, startMs, endMs, rows, counts };
}

export class RotatingArchiveWriter {
  constructor(baseDir, { maxBytes = 150 * 1024 * 1024, onArchive = null } = {}) {
    this.baseDir = baseDir;
    this.maxBytes = maxBytes;
    this.onArchive = onArchive;
    this.liveDir = path.join(baseDir, "live");
    this.currentDir = path.join(this.liveDir, "current");
    this.stagingDir = path.join(baseDir, "staging");
    this.archivesDir = path.join(baseDir, "archives");
    this.filePath = path.join(this.currentDir, "market.jsonl");
    this.metaPath = path.join(this.currentDir, "batch.json");
    this.stream = null;
    this.state = null;
    this.closed = false;
    this.chain = Promise.resolve();
    this.archiveChain = Promise.resolve();
  }

  async init() {
    await Promise.all([
      mkdir(this.currentDir, { recursive: true }),
      mkdir(this.stagingDir, { recursive: true }),
      mkdir(this.archivesDir, { recursive: true }),
    ]);

    // Po awarii mogła zostać gotowa, ale jeszcze niespakowana paczka.
    for (const dir of (await childDirectories(this.stagingDir)).sort()) this.#scheduleArchive(dir);

    let batchMeta = null;
    if (await exists(this.metaPath)) {
      try { batchMeta = JSON.parse(await readFile(this.metaPath, "utf8")); } catch {}
    }
    const inspected = await inspectJsonl(this.filePath);
    this.state = {
      batchId: batchMeta?.batchId || makeBatchId(),
      createdAt: batchMeta?.createdAt || new Date().toISOString(),
      ...inspected,
    };
    await this.#persistBatchMeta();
    this.#openStream();
    return this;
  }

  #openStream() {
    this.stream = createWriteStream(this.filePath, { flags: "a", encoding: "utf8", highWaterMark: 1 << 20 });
    this.stream.on("error", (err) => console.error("[writer]", err));
  }

  async #persistBatchMeta() {
    await writeJsonAtomic(this.metaPath, { batchId: this.state.batchId, createdAt: this.state.createdAt });
  }

  write(channel, records, tsMs = Date.now()) {
    if (this.closed) return Promise.reject(new Error("writer is closed"));
    const rows = (Array.isArray(records) ? records : [records]).filter(Boolean);
    if (!rows.length) return Promise.resolve();
    const task = this.chain.then(() => this.#writeNow(channel, rows, tsMs));
    this.chain = task.catch(() => {});
    return task;
  }

  async #writeNow(channel, rows, fallbackTs) {
    const normalized = rows.map((row) => {
      const ts = recordTimestamp(row) || Number(fallbackTs) || Date.now();
      return { ...row, _channel: channel, tsRecordMs: ts };
    });
    const payload = normalized.map((row) => JSON.stringify(row)).join("\n") + "\n";
    const payloadBytes = Buffer.byteLength(payload);

    // Nie dzielimy pojedynczej wiadomości WS na pół. Rotacja następuje przed zapisem,
    // jeśli ten zapis przekroczyłby limit aktualnej paczki.
    if (this.state.bytes > 0 && this.state.bytes + payloadBytes > this.maxBytes) {
      await this.#rotate();
    }

    if (!this.stream.write(payload)) await once(this.stream, "drain");
    this.state.bytes += payloadBytes;
    this.state.rows += normalized.length;
    this.state.counts[channel] = (this.state.counts[channel] || 0) + normalized.length;
    for (const row of normalized) {
      const ts = row.tsRecordMs;
      this.state.startMs = this.state.startMs == null ? ts : Math.min(this.state.startMs, ts);
      this.state.endMs = this.state.endMs == null ? ts : Math.max(this.state.endMs, ts);
    }
  }

  async #rotate() {
    if (!this.state?.bytes) return;
    this.stream.end();
    await once(this.stream, "finish");

    const manifest = {
      schema: "market-ml-raw-batch-v2",
      batchId: this.state.batchId,
      createdAt: this.state.createdAt,
      closedAt: new Date().toISOString(),
      startMs: this.state.startMs,
      endMs: this.state.endMs,
      range: humanRangeLabel(this.state.startMs, this.state.endMs),
      uncompressedBytes: this.state.bytes,
      rows: this.state.rows,
      counts: this.state.counts,
    };
    await writeFile(path.join(this.currentDir, "manifest.json"), JSON.stringify(manifest, null, 2) + "\n", "utf8");

    const staged = path.join(this.stagingDir, this.state.batchId);
    await rename(this.currentDir, staged);

    // Od tego momentu collector może natychmiast pisać nową paczkę; kompresja starej
    // odbywa się w osobnej kolejce.
    await mkdir(this.currentDir, { recursive: true });
    this.filePath = path.join(this.currentDir, "market.jsonl");
    this.metaPath = path.join(this.currentDir, "batch.json");
    this.state = {
      batchId: makeBatchId(),
      createdAt: new Date().toISOString(),
      bytes: 0,
      startMs: null,
      endMs: null,
      rows: 0,
      counts: {},
    };
    await this.#persistBatchMeta();
    this.#openStream();
    this.#scheduleArchive(staged);
  }

  #scheduleArchive(stagedDir) {
    this.archiveChain = this.archiveChain.then(async () => {
      try {
        const result = await archiveBatchDir(stagedDir, this.archivesDir);
        this.onArchive?.({ ok: true, ...result });
      } catch (error) {
        console.error(`[archive] ${stagedDir}:`, error);
        this.onArchive?.({ ok: false, stagedDir, error });
      }
    });
  }

  async flush() {
    await this.chain;
    if (this.stream && !this.stream.destroyed) {
      // fs stream nie ma jawnego flush; oczekujemy na opróżnienie bufora.
      if (this.stream.writableNeedDrain) await once(this.stream, "drain");
    }
  }

  async close() {
    if (this.closed) return;
    this.closed = true;
    await this.chain;
    if (this.stream && !this.stream.destroyed) {
      this.stream.end();
      await once(this.stream, "finish");
    }
    await this.archiveChain;
  }

  status() {
    return {
      maxBytes: this.maxBytes,
      maxMiB: +(this.maxBytes / 1024 / 1024).toFixed(2),
      current: this.state ? {
        batchId: this.state.batchId,
        bytes: this.state.bytes,
        miB: +(this.state.bytes / 1024 / 1024).toFixed(2),
        fillPct: +((this.state.bytes / this.maxBytes) * 100).toFixed(2),
        rows: this.state.rows,
        startMs: this.state.startMs,
        endMs: this.state.endMs,
        counts: this.state.counts,
      } : null,
    };
  }
}
