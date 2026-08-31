from __future__ import annotations
import argparse, itertools, json, random, math
from copy import deepcopy
from pathlib import Path
import pandas as pd
from dataio import load_yaml, save_yaml
from sampler import simulate, simulate_fixed
from evaluator import evaluate_prepared, prepare_ground_truth


def variants(cfg):
    s=cfg['search']; out=[]
    for tau,gain,fun,(wname,w),(pname,p) in itertools.product(s['tau_ms'],s['gain'],s['transforms'],s['weight_families'].items(),s['response_profiles'].items()):
        c=deepcopy(cfg)
        h=c['sampler']['heat']; h['tau_ms']=tau; h['gain']=gain; h['function']=fun; h['weights']=w
        h['dead_zone']=deepcopy(p['dead_zone']); h['quiet_tau_multiplier']=float(p['quiet_tau_multiplier']); h['single_sensor_multiplier']=float(p['single_sensor_multiplier']); h['shock_event_fraction']=deepcopy(p['shock_event_fraction'])
        q,n,a=p['thresholds']
        c['sampler']['interval_map']=[{'name':'EXTREME','min_heat':a,'interval_ms':800},{'name':'ACTIVE','min_heat':n,'interval_ms':1500},{'name':'NORMAL','min_heat':q,'interval_ms':2500},{'name':'QUIET','min_heat':0.0,'interval_ms':5000}]
        out.append((f'asym-{fun}-{wname}-{pname}-tau{tau}-g{gain}',c))
    return out


def _row(name,m):
    return {'variant':name,**{k:v for k,v in m.items() if k!='checks'},'checks':json.dumps(m['checks'],sort_keys=True)}


def _winner_key(m:dict,cfg:dict):
    """Quality-first ranking. Reduction is a gate, not a contest.

    Once a candidate has already achieved a meaningful reduction, prefer fidelity,
    clean release and lower reconstruction error. Only then prefer a reduction near
    the soft target rather than the largest possible reduction.
    """
    target=float(cfg.get('acceptance',{}).get('preferred_snapshot_reduction',0.50))
    mid=float(m.get('mid_reconstruction_mae_bp',999))
    imb=float(m.get('imbalance_reconstruction_mae',999))
    return (
        float(m.get('tier_a_snapshot_recall',0)),
        float(m.get('tier_b_snapshot_recall',0)),
        float(m.get('ab_weighted_recall',0)),
        float(m.get('tier_a_recall',0)),
        float(m.get('tier_b_recall',0)),
        -float(m.get('oversampling_p95_ms',1e18)),
        -float(m.get('false_fast_time',1)),
        -float(m.get('reaction_p95_ms',1e18)),
        -mid,
        -imb,
        -abs(float(m.get('snapshot_reduction',0))-target),
    )


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--config',required=True);ap.add_argument('--timeline',required=True);ap.add_argument('--calibration',required=True);ap.add_argument('--out',required=True);ap.add_argument('--best-config',required=True);ap.add_argument('--best-metrics');ap.add_argument('--max-variants',type=int,default=480);a=ap.parse_args()
    cfg=load_yaml(a.config); df=pd.read_csv(a.timeline); cal=json.loads(Path(a.calibration).read_text(encoding='utf-8')); gt=prepare_ground_truth(df,cal,cfg)
    rows=[]; candidates=variants(cfg); total=len(candidates); rnd=random.Random(int(cfg.get('seed',0))); rnd.shuffle(candidates); candidates=candidates[:min(a.max_variants,total)]
    print(f'candidate space: {total}; evaluating: {len(candidates)}')
    for name,ival in [('fixed-1s',1000),('fixed-2.5s',2500)]:
        m=evaluate_prepared(df,simulate_fixed(df,ival),cal,cfg,gt); rows.append(_row(name,m))
    best=None; bestkey=None; by_name={}
    for idx,(name,c) in enumerate(candidates,1):
        d=simulate(df,c,cal); m=evaluate_prepared(df,d,cal,c,gt); rows.append(_row(name,m)); by_name[name]=c
        if m['pass']:
            key=_winner_key(m,cfg)
            if best is None or key>bestkey: best=(name,c,m); bestkey=key
        if idx%40==0 or idx==len(candidates): print(f'  evaluated {idx}/{len(candidates)}')
    outdf=pd.DataFrame(rows); Path(a.out).parent.mkdir(parents=True,exist_ok=True); outdf.to_csv(a.out,index=False)
    if best is None:
        usable=[r for r in rows if r['variant'].startswith('asym-')]
        usable.sort(key=lambda r:(r.get('gates_passed',0),r.get('tier_a_snapshot_recall',0),r.get('tier_b_snapshot_recall',0),r.get('ab_weighted_recall',0),-r.get('oversampling_p95_ms',1e18),-r.get('false_fast_time',1)),reverse=True)
        fallback=usable[0]; fallback_name=fallback['variant']; c=by_name[fallback_name]; c['research_status']='diagnostic_fallback_no_candidate_passed'; c['diagnostic_name']=fallback_name; save_yaml(c,a.best_config)
        if a.best_metrics: Path(a.best_metrics).write_text(json.dumps(fallback,indent=2),encoding='utf-8')
        print('WARNING: no calibration candidate passed all v0.4 quality-first acceptance gates; HOLDOUT MUST REMAIN UNTOUCHED.')
        print('diagnostic fallback:',fallback_name)
    else:
        name,c,m=best; c['research_status']='calibration_winner_pending_holdout'; c['winner_name']=name; c['selection_policy']='quality_first_reduction_is_minimum_not_maximum'; save_yaml(c,a.best_config)
        if a.best_metrics: Path(a.best_metrics).write_text(json.dumps(m,indent=2),encoding='utf-8')
        print('CALIBRATION WINNER:',name)
        print('Selection policy: quality first; reduction only needs to clear the meaningful minimum.')
        print(json.dumps(m,indent=2))
    print('leaderboard:',a.out)
if __name__=='__main__':main()
