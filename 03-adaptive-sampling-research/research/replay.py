from __future__ import annotations
import argparse, heapq
from dataio import load_yaml, archive_paths, archive_paths_from_list, stream_tar_jsonl, write_csv
from market_state import MarketState

FIELDS=['ts_ms','price','fast_price_range_bp','fast_tick_travel_bp','fast_trade_rate_s','fast_quote_volume_s','fast_book_churn_s','fast_imbalance_delta','slow_price_range_bp','slow_tick_travel_bp','slow_trade_rate_s','slow_quote_volume_s','slow_book_churn_s','slow_imbalance_delta','book_valid','mid','spread_bp','bid_depth','ask_depth','imbalance']

def build_timeline(cfg, paths, emit_from_ms=None):
    rcfg=cfg['replay']; tick=int(rcfg['tick_ms']); reorder=int(rcfg.get('reorder_ms',250))
    m=MarketState(cfg['market']['primary_category'],cfg['market']['primary_symbol'],int(rcfg.get('top_levels_imbalance',10)))
    heap=[]; seq=0; next_tick=None; max_ts=-1
    def emit_until(limit):
        nonlocal next_tick
        while next_tick is not None and next_tick <= limit:
            f=m.sensors(next_tick,int(rcfg['fast_window_ms']))
            s=m.sensors(next_tick,int(rcfg['slow_window_ms']))
            row={
                'ts_ms':next_tick,'price':f['price_last'],
                'fast_price_range_bp':f['price_range_bp'],'fast_tick_travel_bp':f['tick_travel_bp'],'fast_trade_rate_s':f['trade_rate_s'],'fast_quote_volume_s':f['quote_volume_s'],'fast_book_churn_s':f['book_churn_s'],'fast_imbalance_delta':f['imbalance_delta'],
                'slow_price_range_bp':s['price_range_bp'],'slow_tick_travel_bp':s['tick_travel_bp'],'slow_trade_rate_s':s['trade_rate_s'],'slow_quote_volume_s':s['quote_volume_s'],'slow_book_churn_s':s['book_churn_s'],'slow_imbalance_delta':s['imbalance_delta'],
                'book_valid':f['book_valid'],'mid':f['mid'],'spread_bp':f['spread_bp'],'bid_depth':f['bid_depth'],'ask_depth':f['ask_depth'],'imbalance':f['imbalance']
            }
            # Even during warm-up we must commit every 250 ms tick so imbalance-delta state is realistic.
            m.commit_tick()
            if emit_from_ms is None or next_tick >= int(emit_from_ms):
                yield row
            next_tick += tick
    for rec in stream_tar_jsonl(paths):
        ts=int(rec.get('exch_ts_ms') or rec.get('recv_wall_ms') or 0); seq+=1; max_ts=max(max_ts,ts)
        heapq.heappush(heap,(ts,seq,rec))
        watermark=max_ts-reorder
        while heap and heap[0][0] <= watermark:
            ets,_,ev=heapq.heappop(heap)
            if next_tick is None: next_tick=(ets//tick)*tick
            yield from emit_until(ets)
            m.apply(ev)
    while heap:
        ets,_,ev=heapq.heappop(heap)
        if next_tick is None: next_tick=(ets//tick)*tick
        yield from emit_until(ets)
        m.apply(ev)
    if next_tick is not None:
        yield from emit_until(max_ts+int(rcfg['slow_window_ms']))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--config',required=True)
    src=ap.add_mutually_exclusive_group(required=True); src.add_argument('--glob'); src.add_argument('--list')
    ap.add_argument('--out',required=True); ap.add_argument('--emit-from-ms',type=int); a=ap.parse_args()
    cfg=load_yaml(a.config); paths=archive_paths(a.glob) if a.glob else archive_paths_from_list(a.list)
    paths=[p for p in paths if __import__('pathlib').Path(p).exists()]
    if not paths: raise SystemExit('No input archives found')
    write_csv(build_timeline(cfg,paths,a.emit_from_ms),a.out,FIELDS)
    print(f'wrote {a.out} from {len(paths)} archives')
if __name__=='__main__': main()
