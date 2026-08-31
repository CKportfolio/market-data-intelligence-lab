from __future__ import annotations
import argparse,math,random
from pathlib import Path
import pandas as pd

def make(n=2400,tick=250,seed=7):
    r=random.Random(seed);rows=[];price=80000.;ts=1787860000000;burst=0
    for i in range(n):
        if i%420==200:burst=20
        hot=burst>0
        if hot:burst-=1
        move=r.gauss(0,0.35 if hot else 0.015);price+=move
        pr=abs(move)/price*10000*(2.0 if hot else 1.0)
        tr=max(0,r.gauss(70 if hot else 3,15 if hot else 2));vol=max(0,r.gauss(180000 if hot else 7000,50000 if hot else 5000));ch=max(0,r.gauss(450 if hot else 70,80 if hot else 30));imb=max(0,r.gauss(.16 if hot else .02,.04 if hot else .01))
        rows.append({'ts_ms':ts+i*tick,'price':price,'fast_price_range_bp':pr,'fast_tick_travel_bp':pr*(1.2 if hot else 1),'fast_trade_rate_s':tr,'fast_quote_volume_s':vol,'fast_book_churn_s':ch,'fast_imbalance_delta':imb,'slow_price_range_bp':pr*.7,'slow_tick_travel_bp':pr*.8,'slow_trade_rate_s':tr*.7,'slow_quote_volume_s':vol*.7,'slow_book_churn_s':ch*.7,'slow_imbalance_delta':imb*.7,'book_valid':1,'mid':price,'spread_bp':.12,'bid_depth':10+r.random()*3,'ask_depth':10+r.random()*3,'imbalance':r.uniform(-.2,.2)})
    return pd.DataFrame(rows)
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--calibration',required=True);ap.add_argument('--holdout',required=True);a=ap.parse_args();Path(a.calibration).parent.mkdir(parents=True,exist_ok=True);make(3600,seed=7).to_csv(a.calibration,index=False);make(1800,seed=19).to_csv(a.holdout,index=False);print('synthetic timelines written')
if __name__=='__main__':main()
