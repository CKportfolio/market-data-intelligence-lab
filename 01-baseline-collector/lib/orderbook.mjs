function asNum(x) {
  const n = Number(x);
  return Number.isFinite(n) ? n : 0;
}

export class LocalOrderBook {
  constructor({ market, symbol, depth = 50 }) {
    this.market = market;
    this.symbol = symbol;
    this.depth = depth;
    this.bids = new Map();
    this.asks = new Map();
    this.ready = false;
    this.dirty = false;
    this.last = null;
    this.version = 0;
  }

  apply(message, recvMs = Date.now()) {
    const data = message?.data;
    if (!data || !Array.isArray(data.b) || !Array.isArray(data.a)) return false;

    const isSnapshot = message.type === "snapshot" || Number(data.u) === 1 || !this.ready;
    if (isSnapshot) {
      this.bids.clear();
      this.asks.clear();
    }

    this.#applySide(this.bids, data.b);
    this.#applySide(this.asks, data.a);

    this.ready = true;
    this.dirty = true;
    this.version += 1;
    this.last = {
      recvMs,
      exchangeMs: Number(message.ts) || null,
      matchMs: Number(message.cts) || null,
      updateId: Number(data.u) || null,
      seq: Number(data.seq) || null,
      sourceType: message.type || null,
    };
    return true;
  }

  #applySide(map, rows) {
    for (const row of rows) {
      if (!Array.isArray(row) || row.length < 2) continue;
      const price = String(row[0]);
      const size = String(row[1]);
      if (asNum(size) === 0) map.delete(price);
      else map.set(price, size);
    }
  }

  snapshot(sampleTsMs = Date.now()) {
    if (!this.ready || !this.last) return null;

    const bids = [...this.bids.entries()]
      .sort((a, b) => asNum(b[0]) - asNum(a[0]))
      .slice(0, this.depth)
      .map(([p, q]) => [asNum(p), asNum(q)]);
    const asks = [...this.asks.entries()]
      .sort((a, b) => asNum(a[0]) - asNum(b[0]))
      .slice(0, this.depth)
      .map(([p, q]) => [asNum(p), asNum(q)]);

    if (!bids.length || !asks.length) return null;
    const bestBid = bids[0][0];
    const bestAsk = asks[0][0];
    const mid = (bestBid + bestAsk) / 2;

    return {
      schema: "orderbook-snapshot-v1",
      exchange: "bybit",
      market: this.market,
      symbol: this.symbol,
      depth: this.depth,
      tsSampleMs: sampleTsMs,
      tsRecvMs: this.last.recvMs,
      tsExchangeMs: this.last.exchangeMs,
      tsMatchMs: this.last.matchMs,
      updateId: this.last.updateId,
      seq: this.last.seq,
      bookVersion: this.version,
      bestBid,
      bestAsk,
      mid,
      spread: bestAsk - bestBid,
      spreadBps: mid > 0 ? ((bestAsk - bestBid) / mid) * 10_000 : null,
      bids,
      asks,
    };
  }

  markSaved(version = this.version) {
    if (this.version === version) this.dirty = false;
  }
}

export function normalizeOrderbookEvent(message, market, symbol, recvMs = Date.now()) {
  const d = message?.data || {};
  return {
    schema: "orderbook-event-v1",
    exchange: "bybit",
    market,
    symbol,
    type: message?.type || null,
    tsRecvMs: recvMs,
    tsExchangeMs: Number(message?.ts) || null,
    tsMatchMs: Number(message?.cts) || null,
    updateId: Number(d.u) || null,
    seq: Number(d.seq) || null,
    bids: Array.isArray(d.b) ? d.b.map(([p, q]) => [Number(p), Number(q)]) : [],
    asks: Array.isArray(d.a) ? d.a.map(([p, q]) => [Number(p), Number(q)]) : [],
  };
}
