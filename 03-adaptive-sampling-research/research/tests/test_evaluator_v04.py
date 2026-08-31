import unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
from evaluator import build_ground_truth,evaluate


def base_rows(n=40):
    rows=[]
    for i in range(n):
        rows.append({'ts_ms':i*250,'price':100.0,'fast_price_range_bp':0.0,'fast_tick_travel_bp':0.0,'fast_trade_rate_s':0.0,'fast_quote_volume_s':0.0,'fast_book_churn_s':0.0,'fast_imbalance_delta':0.0,'slow_price_range_bp':0,'slow_tick_travel_bp':0,'slow_trade_rate_s':0,'slow_quote_volume_s':0,'slow_book_churn_s':0,'slow_imbalance_delta':0,'book_valid':1,'mid':100.0,'spread_bp':1,'bid_depth':1,'ask_depth':1,'imbalance':0})
    return rows

def cal():
    return {'event_thresholds':{'price_range_bp':1,'tick_travel_bp':1,'trade_rate_s':10,'quote_volume_s':100,'book_churn_s':10,'imbalance_delta':.1}}
def cfg():
    return {'replay':{'tick_ms':250},'evaluation':{'episode_gap_ms':1500,'event_context_ms':1000,'tier_a_required_interval_ms':800,'tier_b_required_interval_ms':1500,'tier_c_required_interval_ms':2500,'snapshot_grace_ms':750,'oversampling_slow_interval_ms':2500,'oversampling_stable_ms':500,'oversampling_horizon_ms':30000,'tier_weights':{'A':2,'B':1}},'acceptance':{'tier_a_recall_min':.98,'tier_b_recall_min':.9,'ab_weighted_recall_min':.95,'reaction_p95_ms_max':750,'oversampling_p95_ms_max':6000,'false_fast_time_max':.2,'snapshot_reduction_min':.6}}

class EvalTests(unittest.TestCase):
    def test_tier_assignment_and_episode_clustering(self):
        r=base_rows(); r[4]['fast_price_range_bp']=2; r[7]['fast_trade_rate_s']=20; r[7]['fast_book_churn_s']=20; r[20]['fast_book_churn_s']=20
        df=pd.DataFrame(r); gt=build_ground_truth(df,cal(),cfg())
        # A at 1s and B at 1.75s merge into one A episode because gap <=1.5s. C stays diagnostic.
        self.assertEqual(len(gt['episodes']),1); self.assertEqual(gt['episodes'][0].tier,'A'); self.assertEqual(len(gt['tier_c_episodes']),1)
    def test_next_event_ends_oversampling_clock(self):
        r=base_rows(60); r[4]['fast_price_range_bp']=2; r[16]['fast_price_range_bp']=2
        df=pd.DataFrame(r)
        dec=pd.DataFrame({'ts_ms':df.ts_ms,'interval_ms':[800]*len(df),'snapshot':[1 if i%4==0 else 0 for i in range(len(df))]})
        m=evaluate(df,dec,cal(),cfg())
        # First episode remains fast until the next important event ~3s later, not a fabricated 30s oversampling tail.
        self.assertLessEqual(m['oversampling_p95_ms'],3000)
    def test_tier_c_is_not_acceptance_gate(self):
        r=base_rows(80); r[4]['fast_price_range_bp']=2; r[30]['fast_trade_rate_s']=20; r[30]['fast_book_churn_s']=20; r[60]['fast_book_churn_s']=20
        df=pd.DataFrame(r)
        # Perfect A/B state coverage, deliberately never speeds for C.
        ints=[]
        for i in range(len(df)):
            if i==4: ints.append(800)
            elif i==30: ints.append(1500)
            else: ints.append(5000)
        dec=pd.DataFrame({'ts_ms':df.ts_ms,'interval_ms':ints,'snapshot':[1 if v<=1500 else 0 for v in ints]})
        m=evaluate(df,dec,cal(),cfg())
        self.assertEqual(m['tier_c_recall'],0.0)
        self.assertNotIn('tier_c_recall',m['checks'])
if __name__=='__main__':unittest.main()
