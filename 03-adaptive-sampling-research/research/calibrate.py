from __future__ import annotations
import argparse,json
from pathlib import Path
import numpy as np, pandas as pd
from dataio import load_yaml

SENSORS={
 'price_range':'fast_price_range_bp','tick_travel':'fast_tick_travel_bp','trade_rate':'fast_trade_rate_s',
 'quote_volume':'fast_quote_volume_s','book_churn':'fast_book_churn_s','imbalance_delta':'fast_imbalance_delta'}

def qpos(s,q,positive=False):
    a=pd.to_numeric(s,errors='coerce').replace([np.inf,-np.inf],np.nan).dropna().values
    if positive: a=a[a>0]
    if len(a)==0: return 1e-9
    return float(np.quantile(a,q))

def calibrate(cfg,df):
    cc=cfg['calibration']; q=float(cc.get('baseline_quantile',0.5)); eq=float(cc.get('event_quantile',0.995))
    pos=bool(cc.get('positive_only_for_flow',True)); floors=cc['floors']
    baselines={}
    for k,c in SENSORS.items(): baselines[k]=max(qpos(df[c],q,positive=(pos and k in ('trade_rate','quote_volume'))),1e-9)
    events={
      'price_range_bp':max(qpos(df['fast_price_range_bp'],eq),float(floors['price_range_bp'])),
      'tick_travel_bp':max(qpos(df['fast_tick_travel_bp'],eq),float(floors['tick_travel_bp'])),
      'trade_rate_s':max(qpos(df['fast_trade_rate_s'],eq),float(floors['trade_rate_s'])),
      'quote_volume_s':max(qpos(df['fast_quote_volume_s'],eq),float(floors['quote_volume_s'])),
      'book_churn_s':max(qpos(df['fast_book_churn_s'],eq),float(floors['book_churn_s'])),
      'imbalance_delta':max(qpos(df['fast_imbalance_delta'],eq),float(floors['imbalance_delta']))
    }
    return {'baselines':baselines,'event_thresholds':events,'rows':int(len(df)),'start_ms':int(df.ts_ms.min()),'end_ms':int(df.ts_ms.max())}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--config',required=True); ap.add_argument('--timeline',required=True); ap.add_argument('--out',required=True); a=ap.parse_args()
    cfg=load_yaml(a.config); df=pd.read_csv(a.timeline); out=calibrate(cfg,df)
    Path(a.out).parent.mkdir(parents=True,exist_ok=True); Path(a.out).write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps(out,indent=2))
if __name__=='__main__': main()
