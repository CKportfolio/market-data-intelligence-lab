import unittest, json, tempfile
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from src.features import add_time_series_features
from src.candidates import detect_candidates, attach_features
from src.labels import label_candidates, target_name
from src.validation import walk_forward_folds
from src.modeling import select_hyperparams, walk_forward_predict
from src.config import MODEL_CONFIGS

class SmokeTest(unittest.TestCase):
    def test_mini_research_pipeline(self):
        n=6000
        t=np.arange(n)
        # cykliczny rynek z trendem i szumem -> wiele 2x testow
        rng=np.random.default_rng(42)
        close=10000 + 80*np.sin(2*np.pi*t/180) + 25*np.sin(2*np.pi*t/47) + 0.03*t + rng.normal(0,2,n)
        df=pd.DataFrame({
            "minute_ms":1_700_000_000_000 + t*60000,
            "open":close+rng.normal(0,1,n), "high":close+5+rng.random(n)*3,
            "low":close-5-rng.random(n)*3, "close":close,
            "spot_delta_ratio":np.tanh(np.gradient(close)/5),
            "perp_delta_ratio":np.tanh(np.gradient(close)/4),
            "spot_trade_count":rng.integers(20,100,n), "perp_trade_count":rng.integers(30,150,n),
            "spot_volume":rng.random(n)*20+5, "perp_volume":rng.random(n)*40+10,
            "spot_ob_imbalance_l50_mean":np.tanh(np.gradient(close)/8),
            "perp_ob_imbalance_l50_mean":np.tanh(np.gradient(close)/7),
        })
        feat_df, feats=add_time_series_features(df,[1,5,15,60])
        profiles=[{"name":"test","confirm":2,"min_gap":5,"max_gap":240,"tol_bps":80,"min_rebound_bps":15}]
        c=detect_candidates(feat_df,profiles)
        self.assertGreater(len(c),40)
        c,features=attach_features(c,feat_df,feats)
        target={"tp_bps":25,"sl_bps":20,"horizon_min":60}
        c=label_candidates(c,feat_df,[target]).dropna(subset=[f"y_{target_name(target)}"])
        ycol=f"y_{target_name(target)}"
        self.assertGreater(c[ycol].nunique(),1)
        folds=walk_forward_folds(c,3,embargo_min=60,min_train_fraction=.4)
        self.assertGreaterEqual(len(folds),2)
        best,_=select_hyperparams(c,features,ycol,MODEL_CONFIGS[:2])
        p=walk_forward_predict(c,features,ycol,best,folds)
        self.assertGreater(len(p),5)
        self.assertTrue(((p.prob>=0)&(p.prob<=1)).all())

if __name__=='__main__': unittest.main()
