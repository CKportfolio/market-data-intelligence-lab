import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd

from src.candidates import confirmed_pivots, detect_candidates
from src.labels import label_candidates, target_name
from src.validation import walk_forward_folds
from src.modeling import fit_final_bundle

class CoreTests(unittest.TestCase):
    def test_pivot_is_not_backdated(self):
        n=20
        df=pd.DataFrame({"minute_ms":np.arange(n)*60000,"low":[10,9,8,7,6,5,6,7,8,9,10,11,10,9,8,7,8,9,10,11],"high":np.arange(n)+20,"close":np.arange(n)+15})
        lows,_=confirmed_pivots(df, confirm=2)
        self.assertTrue(any(p.pivot_idx==5 and p.confirm_idx==7 for p in lows))
        self.assertTrue(all(p.confirm_idx > p.pivot_idx for p in lows))

    def test_labels_are_censored_without_full_future(self):
        n=30
        df=pd.DataFrame({"minute_ms":np.arange(n)*60000,"high":np.linspace(100,103,n),"low":np.linspace(99,102,n),"close":np.linspace(99.5,102.5,n)})
        c=pd.DataFrame([{"signal_idx":25,"signal_ms":25*60000,"direction":1}])
        t={"tp_bps":50,"sl_bps":25,"horizon_min":10}
        out=label_candidates(c,df,[t])
        self.assertTrue(pd.isna(out.iloc[0][f"y_{target_name(t)}"]))


    def test_model_drops_constant_features_per_training_fold(self):
        n = 120
        df = pd.DataFrame({
            "signal_ms": np.arange(n) * 60000,
            "varying": np.sin(np.arange(n) / 7.0),
            "constant": np.ones(n),
            "all_nan": np.full(n, np.nan),
            "y_test": np.arange(n) % 2,
        })
        params = {
            "learning_rate": 0.05,
            "max_iter": 40,
            "max_leaf_nodes": 15,
            "min_samples_leaf": 10,
            "l2_regularization": 1.0,
        }
        bundle = fit_final_bundle(
            df, ["varying", "constant", "all_nan"], "y_test", params, {}
        )
        self.assertEqual(bundle.feature_cols, ["varying"])
        p = bundle.predict_proba_success(df.iloc[-10:])
        self.assertEqual(len(p), 10)
        self.assertTrue(np.isfinite(p).all())

    def test_walk_forward_has_embargo(self):
        df=pd.DataFrame({"signal_ms":np.arange(200)*60000})
        folds=walk_forward_folds(df,4,embargo_min=10,min_train_fraction=.4)
        self.assertGreaterEqual(len(folds),2)
        for tr,te in folds:
            self.assertLess(df.iloc[tr].signal_ms.max(), df.iloc[te].signal_ms.min()-10*60000)

if __name__=='__main__': unittest.main()
