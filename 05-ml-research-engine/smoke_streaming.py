from __future__ import annotations
import gzip,json,math,shutil,tarfile,tempfile
from pathlib import Path
import numpy as np,pandas as pd
from src.archive_stream import discover_archives
from src.micro_aggregate import stream_archives_to_daily_micro
from src.rest_cache import RestDayCache,RestConfig,days_between
from src.dataset import save_enriched_daily_partitions
from src.stream_research import build_candidate_parts,load_candidate_parts
from src.modeling import select_hyperparams,walk_forward_predict
from src.validation import walk_forward_folds
from src.labels import target_name
from src.config import MODEL_CONFIGS


def write_cache(root,series,day,rows):
    p=root/series/f'{day}.jsonl.gz';p.parent.mkdir(parents=True,exist_ok=True)
    with gzip.open(p,'wt',encoding='utf8') as f:
        for r in rows:f.write(json.dumps(r)+'\n')

def main():
    rng=np.random.default_rng(42);n=5000;t0=1704067200000;t=np.arange(n)
    close=10000+80*np.sin(2*np.pi*t/180)+25*np.sin(2*np.pi*t/47)+0.03*t+rng.normal(0,2,n)
    with tempfile.TemporaryDirectory() as td:
        root=Path(td);arch=root/'archives';arch.mkdir();cache_root=root/'cache';ns=cache_root/'BTCUSDC__BTCUSDT';micro=root/'micro';enriched=root/'enriched';cparts=root/'candidates'
        # six collector-like tar.gz packages
        for part,start_i in enumerate(range(0,n,900)):
            end_i=min(n,start_i+900);b=root/f'b{part}';b.mkdir();rows=[]
            with (b/'market.jsonl').open('w',encoding='utf8') as f:
                for i in range(start_i,end_i):
                    ts=t0+i*60000
                    for market,ch in [('spot','spot_trades'),('linear','perp_trades')]:
                        r={'_channel':ch,'tsRecordMs':ts+100,'tsTradeMs':ts+100,'market':market,'symbol':'BTCUSDC' if market=='spot' else 'BTCUSDT','side':'Buy' if i%2 else 'Sell','price':float(close[i]),'size':float(0.01+rng.random()*.02)}
                        f.write(json.dumps(r)+'\n');rows.append(r)
            manifest={'schema':'market-ml-raw-batch-v2','batchId':f'b{part}','startMs':t0+start_i*60000,'endMs':t0+(end_i-1)*60000+100,'rows':len(rows)}
            (b/'manifest.json').write_text(json.dumps(manifest))
            with tarfile.open(arch/f'p{part}.tar.gz','w:gz') as tf:tf.add(b,arcname=f'b{part}')
        # REST cache, including enough future for 60m labels
        end=t0+(n+120)*60000
        for day in days_between(t0,end):
            ds=int(pd.Timestamp(day,tz='UTC').timestamp()*1000);inds=[i for i in range(n+120) if ds<=t0+i*60000<ds+86400000]
            kl=[]
            for i in inds:
                c=float(close[min(i,n-1)] if i<n else close[-1]+rng.normal(0,3));ts=t0+i*60000
                kl.append({'startMs':ts,'open':c,'high':c+5+rng.random()*3,'low':c-5-rng.random()*3,'close':c,'volume':20+rng.random()*10,'turnover':c*(20+rng.random()*10)})
            for s in ('spot_1m','perp_1m'):write_cache(ns,s,day,kl)
            simp=[{k:r[k] for k in ('startMs','open','high','low','close')} for r in kl]
            for s in ('mark_1m','index_1m','premium_1m'):write_cache(ns,s,day,simp)
            if kl:
                write_cache(ns,'open_interest_5m',day,[{'timestampMs':r['startMs'],'openInterest':1000+i} for i,r in enumerate(kl[::5])])
                write_cache(ns,'long_short_5m',day,[{'timestampMs':r['startMs'],'buyRatio':.51,'sellRatio':.49} for r in kl[::5]])
                write_cache(ns,'funding',day,[{'timestampMs':kl[0]['startMs'],'fundingRate':.0001}])
        infos=discover_archives(arch);st=stream_archives_to_daily_micro(infos,micro,progress_every=0)
        cache=RestDayCache(cache_root,RestConfig(),offline=True);save_enriched_daily_partitions(cache,micro,enriched,t0,t0+(n-1)*60000)
        profiles=[{'name':'test','confirm':2,'min_gap':5,'max_gap':240,'tol_bps':80,'min_rebound_bps':15}];targets=[{'tp_bps':25,'sl_bps':20,'horizon_min':60}]
        cs=build_candidate_parts(enriched,cache,cparts,t0,t0+(n-1)*60000,profiles,[1,5,15,60],targets);cand=load_candidate_parts(cparts)
        y='y_'+target_name(targets[0]);features=[x for x in cs['features'] if x in cand]+[x for x in ['direction_long','formation_triple','profile_code','confirm_bars','gap_1_min','gap_2_min','level_deviation_bps','rebound_bps'] if x in cand]
        features=list(dict.fromkeys(features));d=cand.dropna(subset=[y]);folds=walk_forward_folds(d,3,60,.4);best,_=select_hyperparams(d,features,y,MODEL_CONFIGS[:2]);pred=walk_forward_predict(d,features,y,best,folds)
        assert st['raw_records']>0 and len(cand)>30 and len(pred)>5
        print(f'SMOKE PASS | raw={st["raw_records"]:,} candidates={len(cand):,} oos={len(pred):,}')
        print('No market_merged.jsonl was created.')
if __name__=='__main__':main()
