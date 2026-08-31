from __future__ import annotations
from collections import deque
from dataclasses import dataclass, field
import math

class OrderBook:
    def __init__(self):
        self.bids: dict[float,float] = {}
        self.asks: dict[float,float] = {}
        self.valid = False
        self.last_ts = None

    def apply(self, kind: str, bids, asks, ts: int):
        if kind == 'ob_snapshot':
            self.bids.clear(); self.asks.clear(); self.valid = True
        elif not self.valid:
            return 0
        changed = 0
        for side, updates in ((self.bids, bids or []), (self.asks, asks or [])):
            for p, q in updates:
                p=float(p); q=float(q); changed += 1
                if q == 0: side.pop(p, None)
                else: side[p]=q
        self.last_ts = ts
        return changed

    def top(self, n=10):
        bids = sorted(self.bids.items(), key=lambda x:x[0], reverse=True)[:n]
        asks = sorted(self.asks.items(), key=lambda x:x[0])[:n]
        return bids, asks

    def metrics(self, n=10):
        if not self.valid or not self.bids or not self.asks:
            return {'book_valid':0,'mid':math.nan,'spread_bp':math.nan,'bid_depth':math.nan,'ask_depth':math.nan,'imbalance':math.nan}
        bids, asks = self.top(n)
        bb, ba = bids[0][0], asks[0][0]
        mid=(bb+ba)/2
        spread_bp=(ba-bb)/mid*10000 if mid else math.nan
        bd=sum(q for _,q in bids); ad=sum(q for _,q in asks)
        imb=(bd-ad)/(bd+ad) if (bd+ad)>0 else 0.0
        return {'book_valid':1,'mid':mid,'spread_bp':spread_bp,'bid_depth':bd,'ask_depth':ad,'imbalance':imb}

class MarketState:
    def __init__(self, primary_category='spot', primary_symbol='BTCUSDC', topn=10):
        self.primary=(primary_category,primary_symbol)
        self.topn=topn
        self.books={}
        self.trades=deque()  # (ts, price, quote, key)
        self.churn=deque()   # (ts, changed)
        self.last_primary_price=math.nan
        self.prev_imbalance=0.0

    def book(self,key):
        if key not in self.books: self.books[key]=OrderBook()
        return self.books[key]

    def apply(self,r:dict):
        ts=int(r.get('exch_ts_ms') or r.get('recv_wall_ms') or 0)
        key=(r.get('category'),r.get('symbol'))
        k=r.get('kind')
        if k in ('ob_snapshot','ob_delta'):
            c=self.book(key).apply(k,r.get('bids'),r.get('asks'),ts)
            if c: self.churn.append((ts,c))
        elif k=='trade':
            try:
                p=float(r.get('price')); q=float(r.get('quote_value') or float(r.get('price'))*float(r.get('size')))
            except Exception: return
            self.trades.append((ts,p,q,key))
            if key==self.primary: self.last_primary_price=p

    def _purge(self, now, ms):
        cutoff=now-ms
        while self.trades and self.trades[0][0] < cutoff-5000: self.trades.popleft()
        while self.churn and self.churn[0][0] < cutoff-5000: self.churn.popleft()

    def sensors(self, now:int, window_ms:int):
        self._purge(now,window_ms)
        lo=now-window_ms
        primary=[x for x in self.trades if lo < x[0] <= now and x[3]==self.primary]
        alltr=[x for x in self.trades if lo < x[0] <= now]
        prices=[x[1] for x in primary]
        if prices:
            hi=max(prices); low=min(prices); ref=prices[0] or 1.0
            range_bp=(hi-low)/ref*10000
            travel=0.0
            for a,b in zip(prices,prices[1:]): travel += abs(b-a)
            travel_bp=travel/ref*10000
            price_last=prices[-1]
        else:
            range_bp=travel_bp=0.0; price_last=self.last_primary_price
        sec=max(window_ms/1000.0,1e-9)
        tr_rate=len(alltr)/sec
        quote=sum(x[2] for x in alltr)/sec
        churn=sum(c for ts,c in self.churn if lo < ts <= now)/sec
        bm=self.book(self.primary).metrics(self.topn)
        imb=bm['imbalance']
        if math.isnan(imb): imb_delta=0.0
        else: imb_delta=abs(imb-self.prev_imbalance)
        return {
            'price_last':price_last,
            'price_range_bp':range_bp,
            'tick_travel_bp':travel_bp,
            'trade_rate_s':tr_rate,
            'quote_volume_s':quote,
            'book_churn_s':churn,
            'book_valid':bm['book_valid'],
            'mid':bm['mid'],'spread_bp':bm['spread_bp'],'bid_depth':bm['bid_depth'],'ask_depth':bm['ask_depth'],
            'imbalance':imb if not math.isnan(imb) else 0.0,
            'imbalance_delta':imb_delta,
        }

    def commit_tick(self):
        bm=self.book(self.primary).metrics(self.topn)
        if not math.isnan(bm['imbalance']): self.prev_imbalance=bm['imbalance']
