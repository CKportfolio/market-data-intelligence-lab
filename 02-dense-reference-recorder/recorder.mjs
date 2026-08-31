import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';
import WebSocket from 'ws';
import YAML from 'yaml';
import * as tar from 'tar';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const cfgPath = process.env.RECORDER_CONFIG || path.join(HERE, 'recorder.config.yaml');
const cfg = YAML.parse(fs.readFileSync(cfgPath, 'utf8'));
const outDir = path.resolve(HERE, cfg.output_dir);
const stageRoot = path.join(outDir, '_staging');
fs.mkdirSync(outDir, { recursive: true });
fs.mkdirSync(stageRoot, { recursive: true });

let ingestId = 0;
let segment = null;
let stopping = false;
let sockets = [];

function isoCompact(ms) {
  return new Date(ms).toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');
}

function monoNs() { return process.hrtime.bigint().toString(); }

function newSegment(now = Date.now()) {
  const id = `seg_${isoCompact(now)}_${Math.random().toString(16).slice(2, 8)}`;
  const dir = path.join(stageRoot, id);
  fs.mkdirSync(dir, { recursive: true });
  return {
    id, dir, startMs: now, endMs: now, chunkIndex: 0, chunkBytes: 0, rows: 0,
    counts: {}, stream: null, file: null, chunkFiles: []
  };
}

function openChunk(seg) {
  if (seg.stream) seg.stream.end();
  const name = `records_${String(seg.chunkIndex++).padStart(6, '0')}.jsonl`;
  const file = path.join(seg.dir, name);
  seg.chunkFiles.push(name);
  seg.file = file;
  seg.chunkBytes = 0;
  seg.stream = fs.createWriteStream(file, { flags: 'a' });
}

async function rotateSegment(reason = 'timer') {
  const old = segment;
  if (!old || old.rows === 0) {
    if (old?.stream) old.stream.end();
    segment = newSegment();
    openChunk(segment);
    return;
  }
  if (old.stream) await new Promise(resolve => old.stream.end(resolve));
  old.endMs = Date.now();
  const manifest = {
    schema_version: 1,
    segment_id: old.id,
    reason,
    start_ms: old.startMs,
    end_ms: old.endMs,
    rows: old.rows,
    counts: old.counts,
    chunks: old.chunkFiles
  };
  fs.writeFileSync(path.join(old.dir, 'manifest.json'), JSON.stringify(manifest, null, 2));

  segment = newSegment();
  openChunk(segment);

  const archiveName = `dense_${isoCompact(old.startMs)}_${isoCompact(old.endMs)}.tar.gz`;
  const archive = path.join(outDir, archiveName);
  const members = [...old.chunkFiles, 'manifest.json'];
  await tar.c({ gzip: true, file: archive, cwd: old.dir }, members);
  await fs.promises.rm(old.dir, { recursive: true, force: true });
  console.log(`[rotate] ${archiveName} rows=${old.rows} reason=${reason}`);
}

segment = newSegment();
openChunk(segment);

function normalizeRecord(base) {
  return {
    schema_version: 1,
    ingest_id: ++ingestId,
    venue: 'bybit',
    recv_wall_ms: Date.now(),
    recv_mono_ns: monoNs(),
    ...base
  };
}

function writeRecord(rec) {
  if (!segment) return;
  const line = JSON.stringify(rec) + '\n';
  const bytes = Buffer.byteLength(line);
  const maxChunk = Number(cfg.chunk_raw_mib || 4) * 1024 * 1024;
  if (segment.chunkBytes + bytes > maxChunk && segment.chunkBytes > 0) openChunk(segment);
  segment.stream.write(line);
  segment.chunkBytes += bytes;
  segment.rows += 1;
  segment.endMs = rec.recv_wall_ms;
  segment.counts[rec.kind] = (segment.counts[rec.kind] || 0) + 1;
}

function topicCategory(topic) {
  if (topic.startsWith('publicTrade.')) return 'trade';
  if (topic.startsWith('orderbook.')) return 'orderbook';
  if (topic.startsWith('allLiquidation.')) return 'liquidation';
  return 'unknown';
}

function handleMessage(streamCfg, raw) {
  let msg;
  try { msg = JSON.parse(raw.toString()); } catch { return; }
  if (!msg.topic) return;
  const kind = topicCategory(msg.topic);
  if (kind === 'trade' && Array.isArray(msg.data)) {
    for (const t of msg.data) {
      writeRecord(normalizeRecord({
        kind: 'trade', category: streamCfg.category, symbol: streamCfg.symbol,
        exch_ts_ms: Number(t.T ?? msg.ts ?? Date.now()), sys_ts_ms: Number(msg.ts ?? 0) || null,
        seq: t.seq ?? null, trade_id: t.i ?? null, side: t.S ?? null,
        price: t.p ?? null, size: t.v ?? null,
        quote_value: (t.p != null && t.v != null) ? String(Number(t.p) * Number(t.v)) : null
      }));
    }
  } else if (kind === 'orderbook' && msg.data) {
    const d = msg.data;
    writeRecord(normalizeRecord({
      kind: msg.type === 'snapshot' ? 'ob_snapshot' : 'ob_delta',
      category: streamCfg.category, symbol: streamCfg.symbol,
      exch_ts_ms: Number(d.cts ?? msg.cts ?? msg.ts ?? Date.now()), sys_ts_ms: Number(msg.ts ?? 0) || null,
      update_id: d.u ?? null, seq: d.seq ?? null,
      bids: d.b ?? [], asks: d.a ?? []
    }));
  } else if (kind === 'liquidation') {
    const rows = Array.isArray(msg.data) ? msg.data : [msg.data];
    for (const d of rows) {
      writeRecord(normalizeRecord({
        kind: 'liquidation', category: streamCfg.category, symbol: streamCfg.symbol,
        exch_ts_ms: Number(d.T ?? msg.ts ?? Date.now()), sys_ts_ms: Number(msg.ts ?? 0) || null,
        side: d.S ?? null, price: d.p ?? null, size: d.v ?? null
      }));
    }
  }
}

function connect(streamCfg) {
  if (stopping) return;
  console.log(`[ws:${streamCfg.category}] connecting ${streamCfg.symbol}`);
  const ws = new WebSocket(streamCfg.websocket);
  sockets.push(ws);
  let heartbeat;
  ws.on('open', () => {
    ws.send(JSON.stringify({ op: 'subscribe', args: streamCfg.topics }));
    heartbeat = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ op: 'ping' }));
    }, Number(cfg.heartbeat_ms || 20000));
  });
  ws.on('message', data => handleMessage(streamCfg, data));
  ws.on('error', err => console.error(`[ws:${streamCfg.category}]`, err.message));
  ws.on('close', () => {
    clearInterval(heartbeat);
    if (!stopping) setTimeout(() => connect(streamCfg), Number(cfg.reconnect_ms || 2000));
  });
}

for (const s of cfg.streams) connect(s);

const rotateEvery = Number(cfg.segment_minutes || 15) * 60 * 1000;
const timer = setInterval(() => rotateSegment('timer').catch(console.error), rotateEvery);

const statTimer = setInterval(() => {
  console.log(`[stats] rows=${segment.rows} kinds=${JSON.stringify(segment.counts)} current=${segment.id}`);
}, 30000);

async function shutdown() {
  if (stopping) return;
  stopping = true;
  clearInterval(timer); clearInterval(statTimer);
  for (const ws of sockets) { try { ws.close(); } catch {} }
  await rotateSegment('shutdown');
  process.exit(0);
}
process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);
