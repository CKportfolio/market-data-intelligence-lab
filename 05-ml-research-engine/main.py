from __future__ import annotations
import argparse,json,os,shutil,sys,time,hashlib
from pathlib import Path
import numpy as np
import pandas as pd

from src import __version__
from src.config import ResearchDepth,geometry_profiles,feature_lookbacks,outcome_targets,model_configs,validation_settings,stability_settings
from src.archive_stream import discover_archives,validate_archive_sequence,detect_symbols
from src.micro_aggregate import stream_archives_to_daily_micro
from src.rest_cache import RestConfig,RestDayCache,DAY_MS
from src.dataset import save_enriched_daily_partitions
from src.stream_research import build_candidate_parts,load_candidate_parts,write_price_replay,mark_independent_episodes,historical_feature_subset
from src.labels import target_name
from src.validation import split_selection_holdout,walk_forward_folds
from src.modeling import select_hyperparams,walk_forward_predict,binary_metrics,threshold_table,probability_bins,choose_champion,fit_final_bundle,save_bundle,permutation_table,fit_regressors

ROOT=Path(__file__).resolve().parent; INPUT_DIR=ROOT/'input'; OUTPUT_DIR=ROOT/'output'; WORK_DIR=ROOT/'work'; CACHE_DIR=ROOT/'cache'/'rest'

def dump_json(path:Path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    def clean(v):
        if isinstance(v,(np.integer,)):return int(v)
        if isinstance(v,(np.floating,)):return None if not np.isfinite(v) else float(v)
        if isinstance(v,Path):return str(v)
        if isinstance(v,dict):return {str(k):clean(x) for k,x in v.items()}
        if isinstance(v,(list,tuple)):return [clean(x) for x in v]
        return v
    path.write_text(json.dumps(clean(obj),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def ask_depth(label,explanation,default=20):
    print(f'\n{label}\n  {explanation}\n  10 = lekkie | 20 = srednie (polecam) | 40 = mocne')
    while True:
        r=input(f'  Glebokosc [{default}]: ').strip()
        if not r:return default
        try:
            v=int(r)
            if 1<=v<=60:return v
        except:pass
        print('  Podaj 1-60.')

def choose_input(arg:str|None)->Path:
    if arg:return Path(arg.strip('"')).expanduser().resolve()
    INPUT_DIR.mkdir(exist_ok=True)
    auto=discover_archives(INPUT_DIR)
    if auto:
        print(f'Znalazlem {len(auto)} archiwow w input/. ENTER = uzyj.')
        r=input('Folder RAW/archives [ENTER]: ').strip().strip('"')
        return INPUT_DIR if not r else Path(r).expanduser().resolve()
    r=input('Podaj folder data/raw albo data/raw/archives (mozna przeciagnac do okna): ').strip().strip('"')
    return Path(r).expanduser().resolve()

def dataset_id(infos)->str:
    raw='|'.join(f'{x.path.name}:{x.path.stat().st_size}:{x.start_ms}:{x.end_ms}' for x in infos)
    return hashlib.sha1(raw.encode()).hexdigest()[:12]

def out_dir(raw_start,raw_end):
    OUTPUT_DIR.mkdir(exist_ok=True)
    a=pd.to_datetime(raw_start,unit='ms',utc=True).strftime('%Y-%m-%d');b=pd.to_datetime(raw_end,unit='ms',utc=True).strftime('%Y-%m-%d')
    base=OUTPUT_DIR/f'{a}__{b}_streaming';p=base;i=2
    while p.exists():p=OUTPUT_DIR/f'{base.name}__{i:02d}';i+=1
    p.mkdir(parents=True);return p

def safe_metrics(df,ycol):
    if df.empty:return {}
    d=df.dropna(subset=[ycol,'prob'])
    if d.empty or d[ycol].nunique()<2:return {}
    return binary_metrics(d[ycol].astype(int).to_numpy(),d['prob'].to_numpy())

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--input');ap.add_argument('--offline',action='store_true');ap.add_argument('--rebuild',action='store_true');ap.add_argument('--defaults',action='store_true');ap.add_argument('--allow-dense',action='store_true');ap.add_argument('--quick',action='store_true')
    args=ap.parse_args()
    print('='*78);print(f'MARKET ML RESEARCH ENGINE STREAMING v{__version__}');print('RAW tar.gz -> minute partitions -> live REST cache -> candidates -> ML');print('BRAK market_merged.jsonl / BRAK rozpakowywania calego korpusu');print('='*78)
    source=choose_input(args.input);infos=discover_archives(source)
    if not infos:raise RuntimeError(f'Nie znalazlem .tar.gz w {source}')
    seq=validate_archive_sequence(infos);raw_start=int(seq['start_ms']);raw_end=int(seq['end_ms']);did=dataset_id(infos)
    print(f'\n[INVENTORY] archiwa={len(infos)}  {pd.to_datetime(raw_start,unit="ms",utc=True)} -> {pd.to_datetime(raw_end,unit="ms",utc=True)}')
    if seq['gaps']:print(f"[UWAGA] wykryto {len(seq['gaps'])} przerw >5s miedzy archiwami")
    if seq['overlaps']:print(f"[UWAGA] wykryto {len(seq['overlaps'])} nakladajacych sie zakresow")
    spot,perp=detect_symbols(infos);spot=spot or os.environ.get('SPOT_SYMBOL','BTCUSDC');perp=perp or os.environ.get('PERP_SYMBOL','BTCUSDT')
    print(f'[SYMBOLS] spot={spot}  perp={perp}')

    if args.quick: depth=ResearchDepth(10,10,10,10,10,10)
    elif args.defaults: depth=ResearchDepth()
    else:
        depth=ResearchDepth(
            geometry=ask_depth('1/6 GEOMETRIA','Skale/tolerancje double/triple test.'),features=ask_depth('2/6 FEATURES','Multi-TF + orderflow + orderbook.'),
            outcomes=ask_depth('3/6 OUTCOMES','Target/stop/horyzont.'),validation=ask_depth('4/6 WALIDACJA','Walk-forward + holdout.'),
            model_search=ask_depth('5/6 MODEL','Liczba konfiguracji HGB.'),stability=ask_depth('6/6 STABILNOSC','Kalibracja/permutation importance.'))
    profiles=geometry_profiles(depth.geometry);lookbacks=feature_lookbacks(depth.features);targets=outcome_targets(depth.outcomes)
    max_h=max(int(t['horizon_min']) for t in targets);preroll_days=max(14,int(np.ceil(max(lookbacks)/1440))+2)
    analysis_start=max(0,raw_start-preroll_days*DAY_MS)

    work=WORK_DIR/did
    if args.rebuild and work.exists():shutil.rmtree(work)
    micro_dir=work/'micro_1m';enriched_dir=work/'enriched_1m';cand_parts=work/'candidate_parts';work.mkdir(parents=True,exist_ok=True)
    rest=RestDayCache(CACHE_DIR,RestConfig(spot_symbol=spot,perp_symbol=perp),offline=args.offline)
    t0=time.time()

    marker=work/'micro_done.json'
    if not marker.exists():
        print('\n[1/7] STREAM RAW .tar.gz -> dzienne mikro-partycje 1m')
        stat=stream_archives_to_daily_micro(infos,micro_dir,allow_dense=args.allow_dense)
        dump_json(marker,{'dataset_id':did,**stat})
    else:
        stat=json.loads(marker.read_text());print(f"\n[1/7] RAW juz zagregowany: {stat.get('raw_records',0):,} records")

    print('\n[2/7] LIVE ENRICHMENT + CACHE dzien po dniu')
    save_enriched_daily_partitions(rest,micro_dir,enriched_dir,analysis_start,raw_end)

    print('\n[3/7] Kandydaci geometryczni + features + barrier labels (chunk/day)')
    if cand_parts.exists():shutil.rmtree(cand_parts)
    cstat=build_candidate_parts(enriched_dir,rest,cand_parts,raw_start,raw_end,profiles,lookbacks,targets)
    cand=load_candidate_parts(cand_parts)
    if cand.empty:raise RuntimeError('Brak kandydatow. Zbierz wiecej danych albo zwieksz glebokosc geometrii.')
    cand=mark_independent_episodes(cand,gap_min=15)
    feature_cols=[f for f in cstat['features'] if f in cand.columns]
    # geometry features are deterministic and must also be model inputs
    geom=['direction_long','formation_triple','profile_code','confirm_bars','gap_1_min','gap_2_min','level_deviation_bps','rebound_bps']
    feature_cols=list(dict.fromkeys([c for c in geom+feature_cols if c in cand.columns]))
    cand[feature_cols]=cand[feature_cols].replace([np.inf,-np.inf],np.nan)

    od=out_dir(raw_start,raw_end);(od/'reports').mkdir();(od/'preset').mkdir();(od/'candidates').mkdir()
    cand.to_csv(od/'candidates'/'candidates_with_features_outcomes.csv.gz',index=False,compression='gzip')
    dump_json(od/'preset'/'feature_schema.json',{'features':feature_cols,'signal_semantics':'signal_ms=1m candle start; signal_available_ms=close of that minute'})
    price_rows=write_price_replay(rest,od/'preset'/'price_1m.csv.gz',raw_start,raw_end)
    independent=int(cand['is_episode_first'].sum())
    print(f"  candidates={len(cand):,}, independent_episodes={independent:,}, features={len(feature_cols)}, price_minutes={price_rows:,}")

    cfg={'engine_version':__version__,'dataset_id':did,'source':str(source),'archive_sequence':seq,'symbols':{'spot':spot,'perp':perp},'depth':depth.to_dict(),
         'geometry_profiles':profiles,'feature_lookbacks':lookbacks,'outcome_targets':targets,'validation':validation_settings(depth.validation),'stability':stability_settings(depth.stability),
         'streaming':{'raw_extracted_to_disk':False,'market_merged_jsonl':False,'raw_pass':'tar.gz sequential stream','micro_store':'daily csv.gz','rest_cache':'daily jsonl.gz on demand','candidate_store':'daily csv.gz'},
         'temporal_rules':{'signal_available_ms':'minute_ms+60000','features':'past/current closed minute only','labels':'future starts next minute','same_bar_tp_sl':'SL-first conservative','final_holdout':'never used for selection'}}
    dump_json(od/'research_config.json',cfg)

    print('\n[4/7] Chronologiczny split + target/model selection')
    # Nearby same-direction candidates are not independent observations. Use the first
    # actionable signal in each episode for model selection/evaluation; keep all in output.
    model_cand=cand[cand['is_episode_first']==1].copy().reset_index(drop=True)
    vcfg=cfg['validation'];selection,holdout,holdout_start=split_selection_holdout(model_cand,vcfg['holdout_fraction'],max_h)
    target_rows=[];all_oos=[];hyper_all=[];threshold_all=[];calib_all=[];specs={target_name(t):t for t in targets};configs=model_configs(depth.model_search)
    for i,t in enumerate(targets,1):
        name=target_name(t);ycol='y_'+name;sel=selection.dropna(subset=[ycol]).copy()
        if len(sel)<35 or sel[ycol].nunique()<2:
            print(f'  [{i}/{len(targets)}] {name}: skip');continue
        print(f'  [{i}/{len(targets)}] {name}')
        best,hyper=select_hyperparams(sel,feature_cols,ycol,configs)
        if not hyper.empty:hyper['target']=name;hyper_all.append(hyper)
        folds=walk_forward_folds(sel,vcfg['walk_folds'],max_h,vcfg['min_train_fraction']);oos=walk_forward_predict(sel,feature_cols,ycol,best,folds)
        if oos.empty:continue
        m=safe_metrics(oos,ycol);target_rows.append({'target':name,**t,'oos_rows':len(oos),**{f'oos_{k}':v for k,v in m.items()},'best_params':json.dumps(best)})
        oos['target']=name;all_oos.append(oos)
        th=threshold_table(oos,ycol,t['tp_bps'],t['sl_bps']);
        if not th.empty:th['target']=name;threshold_all.append(th)
        cb=probability_bins(oos,ycol,cfg['stability']['probability_bins']);
        if not cb.empty:cb['target']=name;calib_all.append(cb)
    tr=pd.DataFrame(target_rows)
    if tr.empty:raise RuntimeError('Za malo danych/klas do ML. Pipeline danych jest gotowy; zbierz wiecej dni.')
    tr.to_csv(od/'reports'/'target_metrics.csv',index=False)
    if hyper_all:pd.concat(hyper_all,ignore_index=True).to_csv(od/'reports'/'hyperparameter_search.csv',index=False)
    if threshold_all:pd.concat(threshold_all,ignore_index=True).to_csv(od/'reports'/'thresholds.csv',index=False)
    if calib_all:pd.concat(calib_all,ignore_index=True).to_csv(od/'reports'/'calibration_bins.csv',index=False)

    champ=choose_champion(tr);name=champ['target'];spec=specs[name];ycol='y_'+name;params=json.loads(champ['best_params'])
    print(f'\n[5/7] Champion={name}; nietykalny holdout')
    eval_bundle=fit_final_bundle(selection.dropna(subset=[ycol]),feature_cols,ycol,params,spec);he=holdout.dropna(subset=[ycol]).copy();hm={}
    if len(he) and he[ycol].nunique()>=2:
        he['prob']=eval_bundle.predict_proba_success(he);hm=binary_metrics(he[ycol].astype(int).to_numpy(),he['prob'].to_numpy())
        he[['signal_ms','signal_available_ms','direction','formation','profile',ycol,'prob']].to_csv(od/'reports'/'champion_holdout_predictions.csv',index=False)
    imp=permutation_table(eval_bundle,he,ycol,cfg['stability']['permutation_repeats'])
    if not imp.empty:imp.to_csv(od/'reports'/'feature_importance.csv',index=False)

    # Ablation: cheap historical/backfillable context vs the same model with live RAW microstructure.
    ablation=[]
    ablation.append({'feature_set':'full_microstructure','feature_count':len(feature_cols),**hm})
    hist_features=historical_feature_subset(feature_cols)
    if len(hist_features)>=5 and len(he) and he[ycol].nunique()>=2:
        try:
            hb=fit_final_bundle(selection.dropna(subset=[ycol]),hist_features,ycol,params,spec)
            hp=hb.predict_proba_success(he); hmet=binary_metrics(he[ycol].astype(int).to_numpy(),hp)
            ablation.append({'feature_set':'historical_backfillable_only','feature_count':len(hist_features),**hmet})
        except Exception as e:
            ablation.append({'feature_set':'historical_backfillable_only','feature_count':len(hist_features),'error':str(e)})
    pd.DataFrame(ablation).to_csv(od/'reports'/'feature_group_ablation.csv',index=False)

    regressors,rm=fit_regressors(selection,holdout,feature_cols,f'mfe_{max_h}m_bps',f'mae_{max_h}m_bps')
    if not rm.empty:rm.to_csv(od/'reports'/'range_regression_holdout.csv',index=False)

    print('[6/7] Final preset + OOS replay')
    full=cand.dropna(subset=[ycol]).copy();final=fit_final_bundle(full,feature_cols,ycol,params,spec);save_bundle(final,od/'preset'/'champion_model.joblib')
    import joblib
    for k,m in regressors.items():joblib.dump({'model':m,'features':feature_cols,'horizon_min':max_h},od/'preset'/f'{k}_regressor.joblib')
    replay=[]
    if all_oos:
        oo=pd.concat(all_oos,ignore_index=True);oo=oo[oo.target==name].copy();oo['source']='walk_forward_oos';oo['signal_available_ms']=oo['signal_ms']+60000
        replay.append(oo.rename(columns={ycol:'actual'})[['signal_ms','signal_available_ms','direction','formation','profile','prob','actual','source']])
    if not he.empty and 'prob' in he:
        hh=he.copy();hh['source']='final_holdout';replay.append(hh.rename(columns={ycol:'actual'})[['signal_ms','signal_available_ms','direction','formation','profile','prob','actual','source']])
    rp=pd.concat(replay,ignore_index=True).sort_values('signal_ms') if replay else pd.DataFrame()
    if not rp.empty:
        rp['p_long']=np.where(rp.direction==1,rp.prob,np.nan);rp['p_short']=np.where(rp.direction==-1,rp.prob,np.nan);rp.to_csv(od/'preset'/'replay_predictions.csv.gz',index=False,compression='gzip')
        price=pd.read_csv(od/'preset'/'price_1m.csv.gz');sparse=rp.groupby(['signal_ms','direction'],as_index=False).prob.max()
        lp=sparse[sparse.direction==1][['signal_ms','prob']].rename(columns={'signal_ms':'minute_ms','prob':'p_long'});sp=sparse[sparse.direction==-1][['signal_ms','prob']].rename(columns={'signal_ms':'minute_ms','prob':'p_short'})
        tl=price.merge(lp,on='minute_ms',how='left').merge(sp,on='minute_ms',how='left');tl.to_csv(od/'preset'/'replay_timeline_1m.csv.gz',index=False,compression='gzip')
    preset={'schema':'market-ml-signal-preset-v2-streaming','created_at':pd.Timestamp.utcnow().isoformat(),'champion_target':name,'target':spec,'features':feature_cols,'model_file':'champion_model.joblib',
            'range_models':{k:f'{k}_regressor.joblib' for k in regressors},'holdout_start_ms':holdout_start,'oos_metrics':{k:v for k,v in champ.items() if str(k).startswith('oos_')},'final_holdout_metrics':hm,
            'research_only':True,'requires_forward_validation_on_new_data':True,'input_contract':'raw collector .tar.gz streamed directly; REST enrichment cached by day; no market_merged.jsonl'}
    dump_json(od/'preset'/'preset.json',preset)

    summary={'raw_start':pd.to_datetime(raw_start,unit='ms',utc=True).isoformat(),'raw_end':pd.to_datetime(raw_end,unit='ms',utc=True).isoformat(),'archives':len(infos),'raw_records':stat.get('raw_records'),
             'candidates':len(cand),'independent_episodes':int(cand['is_episode_first'].sum()),'features':len(feature_cols),'formations':cstat['formation_counts'],'champion':name,'holdout':hm,'elapsed_sec':round(time.time()-t0,2)}
    dump_json(od/'dataset_summary.json',summary);dump_json(od/'manifest.json',{'schema':'market-ml-research-output-v2-streaming','engine_version':__version__,'dataset_id':did,'output':str(od),'summary':summary})
    txt=['MARKET ML RESEARCH STREAMING - PODSUMOWANIE','='*56,f"Archiwa: {len(infos)} | RAW records: {stat.get('raw_records',0):,}",f"Zakres: {summary['raw_start']} -> {summary['raw_end']}",
         'RAW merge: NIE. Rozpakowywanie calego korpusu: NIE.',f"Kandydaci: {len(cand):,} | niezalezne epizody: {int(cand['is_episode_first'].sum()):,} | features: {len(feature_cols)}",f'Champion: {name}',f"OOS Brier: {champ.get('oos_brier')}",f'Holdout: {json.dumps(hm,ensure_ascii=False)}','',
         'Preset jest badawczy. Wymaga forward-testu na nowych danych.']
    (od/'reports'/'SUMMARY.txt').write_text('\n'.join(txt)+'\n',encoding='utf-8')
    print('\n[7/7] GOTOWE');print(f'  {od}');print('  RAW nigdy nie zostal scalony do jednego pliku.')

if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:print('\nPrzerwano. Cache/work zostaja i mozna wznowic.');raise SystemExit(130)
    except Exception as e:
        print(f'\n[BLAD] {e}');import traceback;traceback.print_exc();raise SystemExit(1)
