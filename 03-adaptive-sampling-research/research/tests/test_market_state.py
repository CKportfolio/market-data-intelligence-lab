import unittest, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from market_state import OrderBook
class BookTests(unittest.TestCase):
    def test_snapshot_delta(self):
        b=OrderBook(); b.apply('ob_snapshot',[['100','2']],[['101','3']],1); b.apply('ob_delta',[['100','4']],[['101','0'],['102','1']],2)
        bids,asks=b.top(5); self.assertEqual(bids[0],(100.0,4.0)); self.assertEqual(asks[0],(102.0,1.0))
if __name__=='__main__': unittest.main()
