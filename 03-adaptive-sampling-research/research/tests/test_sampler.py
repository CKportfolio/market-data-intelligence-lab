import unittest, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
from sampler import simulate


def cfg():
    return {'replay':{'tick_ms':250},'sampler':{'snapshot_on_speedup':True,'max_heat':8,'interval_map':[{'name':'EXTREME','min_heat':3.2,'interval_ms':800},{'name':'ACTIVE','min_heat':2.0,'interval_ms':1500},{'name':'NORMAL','min_heat':0.9,'interval_ms':2500},{'name':'QUIET','min_heat':0.0,'interval_ms':5000}],'heat':{'tau_ms':2000,'gain':2.0,'function':'log_excess','confluence_bonus':0.35,'weights':{'price_range':.4,'tick_travel':.2,'trade_rate':.1,'quote_volume':.05,'book_churn':.15,'imbalance_delta':.1}}}}
def cal(): return {'baselines':{'price_range':.1,'tick_travel':.1,'trade_rate':10,'quote_volume':10000,'book_churn':100,'imbalance_delta':.02}}
def frame(vals):
    return pd.DataFrame([{'ts_ms':i*250,'fast_price_range_bp':v,'fast_tick_travel_bp':v,'fast_trade_rate_s':10,'fast_quote_volume_s':10000,'fast_book_churn_s':100,'fast_imbalance_delta':.02} for i,v in enumerate(vals)])
class SamplerTests(unittest.TestCase):
    def test_heat_decays(self):
        d=simulate(frame([2,0,0,0,0,0,0,0,0,0,0,0]),cfg(),cal()); self.assertGreater(d.heat.iloc[0],d.heat.iloc[-1])
    def test_speedup_snapshots_immediately(self):
        d=simulate(frame([0,0,0,0,3,0]),cfg(),cal()); row=d.iloc[4]; self.assertEqual(int(row.snapshot),1); self.assertEqual(row.reason,'speedup')
    def test_quiet_uses_five_seconds(self):
        d=simulate(frame([0]*10),cfg(),cal()); self.assertTrue((d.interval_ms==5000).all())
if __name__=='__main__': unittest.main()
