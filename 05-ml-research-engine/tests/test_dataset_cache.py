import gzip,json,tempfile,unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
from src.rest_cache import RestDayCache,RestConfig
from src.dataset import build_enriched_range

class DatasetCacheTests(unittest.TestCase):
    def write(self,root,series,day,rows):
        p=root/series/f'{day}.jsonl.gz';p.parent.mkdir(parents=True,exist_ok=True)
        with gzip.open(p,'wt',encoding='utf8') as f:
            for r in rows:f.write(json.dumps(r)+'\n')
    def test_offline_daily_cache_enrich(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);cache_root=root/'cache'; ns=cache_root/'BTCUSDC__BTCUSDT'; micro=root/'micro';micro.mkdir()
            t0=1704067200000;day='2024-01-01'
            kl=[{'startMs':t0+i*60000,'open':100+i,'high':101+i,'low':99+i,'close':100+i,'volume':10,'turnover':1000} for i in range(10)]
            for s in ('spot_1m','perp_1m') : self.write(ns,s,day,kl)
            simple=[{'startMs':x['startMs'],'open':x['open'],'high':x['high'],'low':x['low'],'close':x['close']} for x in kl]
            for s in ('mark_1m','index_1m','premium_1m'):self.write(ns,s,day,simple)
            self.write(ns,'open_interest_5m',day,[{'timestampMs':t0,'openInterest':1000}])
            self.write(ns,'long_short_5m',day,[{'timestampMs':t0,'buyRatio':.52,'sellRatio':.48}])
            self.write(ns,'funding',day,[{'timestampMs':t0,'fundingRate':.0001}])
            pd.DataFrame([{'minute_ms':t0,'spot_trade_count':3,'spot_volume':1.2,'spot_turnover':120,'spot_buy_volume':.8,'spot_sell_volume':.4}]).to_csv(micro/f'{day}.csv.gz',index=False,compression='gzip')
            c=RestDayCache(cache_root,RestConfig(),offline=True);d=build_enriched_range(c,micro,t0,t0+9*60000)
            self.assertEqual(len(d),10);self.assertEqual(d.iloc[0].spot_trade_count,3);self.assertIn('perp_spot_basis_bps',d);self.assertEqual(int(d.iloc[0].available_ms),t0+60000)

if __name__=='__main__':unittest.main()
