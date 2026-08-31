import test from "node:test";
import assert from "node:assert/strict";
import { LocalOrderBook } from "../lib/orderbook.mjs";

test("snapshot + delta reconstructs a sorted book", () => {
  const ob = new LocalOrderBook({ market: "spot", symbol: "BTCUSDT", depth: 3 });
  ob.apply({
    type: "snapshot", ts: 1000, cts: 999,
    data: { u: 10, seq: 100, b: [["100", "2"], ["99", "3"]], a: [["101", "4"], ["102", "5"]] },
  }, 1001);
  ob.apply({
    type: "delta", ts: 1010, cts: 1009,
    data: { u: 11, seq: 101, b: [["100", "0"], ["100.5", "1"]], a: [["101", "6"], ["103", "7"]] },
  }, 1011);
  const s = ob.snapshot(1020);
  assert.equal(s.bestBid, 100.5);
  assert.equal(s.bestAsk, 101);
  assert.deepEqual(s.bids, [[100.5, 1], [99, 3]]);
  assert.deepEqual(s.asks, [[101, 6], [102, 5], [103, 7]]);
  assert.equal(s.updateId, 11);
  assert.equal(s.seq, 101);
});

test("u=1 resets the book", () => {
  const ob = new LocalOrderBook({ market: "linear", symbol: "BTCUSDT", depth: 50 });
  ob.apply({ type: "snapshot", data: { u: 5, seq: 5, b: [["100", "1"]], a: [["101", "1"]] } }, 1);
  ob.apply({ type: "delta", data: { u: 1, seq: 6, b: [["90", "2"]], a: [["91", "2"]] } }, 2);
  const s = ob.snapshot(3);
  assert.equal(s.bestBid, 90);
  assert.equal(s.bestAsk, 91);
});

test("markSaved does not clear a newer unsaved update", () => {
  const ob = new LocalOrderBook({ market: "spot", symbol: "BTCUSDT", depth: 3 });
  ob.apply({ type: "snapshot", data: { u: 1, seq: 1, b: [["100", "1"]], a: [["101", "1"]] } }, 1);
  const old = ob.snapshot(2);
  ob.apply({ type: "delta", data: { u: 2, seq: 2, b: [["100", "2"]], a: [] } }, 3);
  ob.markSaved(old.bookVersion);
  assert.equal(ob.dirty, true);
});
