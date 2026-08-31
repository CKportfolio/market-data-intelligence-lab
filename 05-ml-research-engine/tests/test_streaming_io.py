import gzip,json,tarfile,tempfile,unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
from src.archive_stream import discover_archives,iter_archive_rows,validate_archive_sequence
from src.micro_aggregate import stream_archives_to_daily_micro
from src.labels import label_candidates_by_time,target_name
from src.stream_research import mark_independent_episodes

class StreamingIOTests(unittest.TestCase):
    def _archive(self,root,name,start,rows):
        b=root/'batch';b.mkdir(exist_ok=True)
        market=b/'market.jsonl'
        with market.open('w',encoding='utf8') as f:
            for r in rows:f.write(json.dumps(r)+'\n')
        end=max(r['tsRecordMs'] for r in rows)
        (b/'manifest.json').write_text(json.dumps({'schema':'market-ml-raw-batch-v2','batchId':name,'startMs':start,'endMs':end,'rows':len(rows)}))
        out=root/f'{name}.tar.gz'
        with tarfile.open(out,'w:gz') as tf:tf.add(b,arcname=name)
        return out

    def test_tar_is_streamed_and_aggregated_without_extract(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);t0=1_700_000_000_000
            rows=[]
            for i in range(3):
                ts=t0+i*1000
                rows.append({'_channel':'spot_trades','tsRecordMs':ts,'tsTradeMs':ts,'side':'Buy','price':100+i,'size':1,'symbol':'BTCUSDC'})
            self._archive(root,'a',t0,rows)
            infos=discover_archives(root);self.assertEqual(len(infos),1)
            got=list(iter_archive_rows(infos[0].path));self.assertEqual(len(got),3)
            out=root/'micro';st=stream_archives_to_daily_micro(infos,out,progress_every=0)
            self.assertEqual(st['raw_records'],3)
            files=list(out.glob('*.csv.gz'));self.assertEqual(len(files),1)
            d=pd.read_csv(files[0]);self.assertEqual(int(d.spot_trade_count.sum()),3)
            self.assertFalse(any(root.rglob('market_merged.jsonl')))

    def test_timestamp_labeler_censors_missing_future(self):
        t0=1_700_000_000_000
        p=pd.DataFrame({'minute_ms':[t0+i*60000 for i in range(5)],'high':[100,101,102,103,104],'low':[99,99,99,99,99],'close':[100]*5})
        c=pd.DataFrame([{'signal_ms':t0,'direction':1}])
        target={'tp_bps':100,'sl_bps':100,'horizon_min':10}
        r=label_candidates_by_time(c,p,[target]);self.assertTrue(pd.isna(r.iloc[0]['y_'+target_name(target)]))


    def test_episode_dedup_uses_first_signal_without_lookahead(self):
        d=pd.DataFrame([
            {'signal_ms':0,'direction':1,'quality_geom':1.0},
            {'signal_ms':5*60000,'direction':1,'quality_geom':99.0},
            {'signal_ms':21*60000,'direction':1,'quality_geom':2.0},
            {'signal_ms':4*60000,'direction':-1,'quality_geom':1.0},
        ])
        r=mark_independent_episodes(d,gap_min=15)
        long=r[r.direction==1].sort_values('signal_ms')
        self.assertEqual(long.is_episode_first.tolist(),[1,0,1])

if __name__=='__main__':unittest.main()
