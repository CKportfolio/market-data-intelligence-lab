import unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
from sampler import simulate


def cfg():
    return {'replay':{'tick_ms':250},'sampler':{'snapshot_on_speedup':True,'max_heat':8,
      'interval_map':[{'name':'EXTREME','min_heat':2.8,'interval_ms':800},{'name':'ACTIVE','min_heat':1.55,'interval_ms':1500},{'name':'NORMAL','min_heat':.7,'interval_ms':2500},{'name':'QUIET','min_heat':0,'interval_ms':5000}],
      'heat':{'tau_ms':1400,'quiet_tau_multiplier':.35,'gain':.8,'function':'sqrt_excess','confluence_bonus':.2,'single_sensor_multiplier':.1,
      'dead_zone':{'price':1.15,'flow':1.35,'book':1.35},'shock_event_fraction':{'price':.9,'flow':.9,'book':.9},
      'weights':{'price_range':.34,'tick_travel':.2,'trade_rate':.11,'quote_volume':.07,'book_churn':.16,'imbalance_delta':.12}}}}

def cal():
    return {'baselines':{'price_range':.1,'tick_travel':.1,'trade_rate':10,'quote_volume':10000,'book_churn':100,'imbalance_delta':.02},
            'event_thresholds':{'price_range_bp':1,'tick_travel_bp':1,'trade_rate_s':40,'quote_volume_s':50000,'book_churn_s':250,'imbalance_delta':.12}}

def frame(n=30):
    rows=[]
    for i in range(n):
        rows.append({'ts_ms':i*250,'fast_price_range_bp':.1,'fast_tick_travel_bp':.1,'fast_trade_rate_s':10,'fast_quote_volume_s':10000,'fast_book_churn_s':100,'fast_imbalance_delta':.02})
    return pd.DataFrame(rows)

class SamplerV04Tests(unittest.TestCase):
    def test_price_shock_attacks_immediately(self):
        d=frame(); d.loc[4,'fast_price_range_bp']=1.1
        o=simulate(d,cfg(),cal()); self.assertEqual(int(o.loc[4,'interval_ms']),800); self.assertEqual(int(o.loc[4,'snapshot']),1); self.assertEqual(int(o.loc[4,'price_shock']),1)
    def test_flow_book_confluence_attacks_to_active(self):
        d=frame(); d.loc[4,'fast_trade_rate_s']=50; d.loc[4,'fast_book_churn_s']=300
        o=simulate(d,cfg(),cal()); self.assertLessEqual(int(o.loc[4,'interval_ms']),1500); self.assertEqual(int(o.loc[4,'snapshot']),1); self.assertEqual(int(o.loc[4,'micro_shock']),1)
    def test_small_single_sensor_noise_does_not_sustain_heat(self):
        d=frame(50); d.loc[4:20,'fast_book_churn_s']=120 # above baseline, below dead-zone
        o=simulate(d,cfg(),cal()); self.assertLess(float(o.heat.max()),.05); self.assertTrue((o.interval_ms==5000).all())
    def test_release_after_short_shock(self):
        d=frame(60); d.loc[4,'fast_price_range_bp']=1.2
        o=simulate(d,cfg(),cal());
        # Immediate 800ms attack, then quiet release should not stay <=1500ms for a long tail.
        self.assertEqual(int(o.loc[4,'interval_ms']),800)
        self.assertTrue((o.loc[20:,'interval_ms']>=2500).all())

if __name__=='__main__': unittest.main()
