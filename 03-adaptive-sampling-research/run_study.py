from __future__ import annotations
import argparse, json, os, shutil, subprocess, sys, tarfile, time
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent
RESEARCH=ROOT/'research'
VENV=ROOT/'.venv'

def local_venv_python():
    return VENV/('Scripts/python.exe' if os.name=='nt' else 'bin/python')

def _python_has_deps(py:Path|str):
    try:
        subprocess.check_call([str(py),'-c','import pandas,numpy,yaml,matplotlib'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False

def ensure_python_and_relaunch(args, source:Path):
    if args.inside_venv:
        return
    candidates=[local_venv_python(),Path(sys.executable)]
    chosen=None
    for py in candidates:
        if Path(py).exists() and _python_has_deps(py):
            chosen=Path(py); break
    if chosen is None:
        vp=local_venv_python()
        if not vp.exists():
            print('[setup] creating local .venv ...',flush=True)
            subprocess.check_call([sys.executable,'-m','venv',str(VENV)])
        print('[setup] installing Python requirements ...',flush=True)
        subprocess.check_call([str(vp),'-m','pip','install','-r',str(RESEARCH/'requirements.txt')])
        chosen=vp
    print(f'[setup] using Python: {chosen}',flush=True)
    cmd=[str(chosen),str(Path(__file__).resolve()),'--inside-venv','--max-variants',str(args.max_variants)]
    if args.source: cmd += ['--source',args.source]
    raise SystemExit(subprocess.call(cmd,cwd=str(ROOT)))

def run(*parts,cwd=RESEARCH):
    cmd=[sys.executable,*map(str,parts)]
    print('\n>>>',' '.join(cmd),flush=True)
    subprocess.check_call(cmd,cwd=str(cwd))

def read_tar_manifest(p:Path):
    try:
        with tarfile.open(p,'r:gz') as tf:
            members=tf.getmembers(); names={m.name for m in members}
            mans=[m for m in members if m.isfile() and m.name.endswith('manifest.json')]
            if not mans or not any(n.endswith('.jsonl') for n in names): return None
            fh=tf.extractfile(mans[0]); return json.loads(fh.read().decode('utf-8')) if fh else None
    except Exception:
        return None

def tar_is_complete(p:Path):
    return read_tar_manifest(p) is not None

def freeze_archives(source_live:Path):
    candidates=sorted(source_live.glob('*.tar.gz'))
    if len(candidates)<4: raise RuntimeError(f'Only {len(candidates)} tar.gz found in {source_live}')
    sizes={p:p.stat().st_size for p in candidates}; time.sleep(1.0)
    stable=[]; skipped=[]
    for p in candidates:
        try:
            if p.stat().st_size!=sizes[p]: skipped.append((p.name,'size-changing')); continue
            if not tar_is_complete(p): skipped.append((p.name,'not-yet-complete-or-invalid')); continue
            stable.append(p)
        except FileNotFoundError: skipped.append((p.name,'disappeared'))
    if len(stable)<4: raise RuntimeError(f'Only {len(stable)} complete/stable tar.gz files are safe for study')
    snap=ROOT/'data'/'study_snapshot'; splits=ROOT/'data'/'splits'
    shutil.rmtree(snap,ignore_errors=True); snap.mkdir(parents=True,exist_ok=True); splits.mkdir(parents=True,exist_ok=True)
    local=[]; methods={'hardlink':0,'copy':0}
    for src in stable:
        dst=snap/src.name
        try:
            os.link(src,dst); methods['hardlink']+=1
        except Exception:
            shutil.copy2(src,dst); methods['copy']+=1
        local.append(dst.resolve())
    n=len(local); cal_n=int(n*0.70); hold_n=int(__import__('math').ceil(n*0.25)); gap_n=n-cal_n-hold_n
    if gap_n<1:
        gap_n=1; cal_n=max(1,n-hold_n-gap_n)
    cal=local[:cal_n]; gap=local[cal_n:cal_n+gap_n]; hold=local[-hold_n:]
    def write(name,items):
        p=splits/f'{name}.txt'; p.write_text('\n'.join(str(x) for x in items)+'\n',encoding='utf-8'); return p
    calp=write('calibration',cal); gapp=write('gap',gap); holdp=write('holdout',hold)
    # To reconstruct a mid-session orderbook correctly, holdout replay later warms through all preceding frozen archives,
    # but emits no holdout rows until the first holdout segment begins. This does NOT use gap/calibration for scoring.
    holdreplayp=write('holdout_replay_with_warmup',local)
    hold_first_manifest=read_tar_manifest(hold[0]) or {}
    holdout_start_ms=int(hold_first_manifest.get('start_ms') or 0)
    if holdout_start_ms<=0: raise RuntimeError('Could not read holdout start_ms from first holdout archive manifest')
    manifest={'created_utc':datetime.now(timezone.utc).isoformat(),'source_live':str(source_live),'source_tar_count_at_scan':len(candidates),'stable_frozen_count':n,'skipped':skipped,'snapshot_method_counts':methods,'calibration_count':len(cal),'gap_count':len(gap),'holdout_count':len(hold),'holdout_start_ms':holdout_start_ms,'calibration_files':[x.name for x in cal],'gap_files':[x.name for x in gap],'holdout_files':[x.name for x in hold]}
    (ROOT/'data'/'study_manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(f"[snapshot] source={len(candidates)} stable={n} | CAL={len(cal)} GAP={len(gap)} HOLDOUT={len(hold)} | hardlinks={methods['hardlink']} copies={methods['copy']}",flush=True)
    if skipped: print('[snapshot] skipped:',skipped,flush=True)
    return calp,gapp,holdp,holdreplayp,holdout_start_ms,manifest

def main():
    ap=argparse.ArgumentParser(description='Adaptive OB Research v0.4 quality-first one-command study')
    ap.add_argument('--source',help='Path to dense reference recorder root; default: ../02-dense-reference-recorder')
    ap.add_argument('--max-variants',type=int,default=480)
    ap.add_argument('--inside-venv',action='store_true',help=argparse.SUPPRESS)
    args=ap.parse_args()
    source=Path(args.source).resolve() if args.source else (ROOT.parent/'02-dense-reference-recorder').resolve()
    ensure_python_and_relaunch(args,source)
    source_live=source/'data'/'dense'/'live'
    if not source_live.exists(): raise SystemExit(f'Source live folder not found: {source_live}')
    print('Adaptive OB Research v0.4')
    print('Recorder may keep running. This program NEVER writes to:',source_live)
    cal_list,gap_list,hold_list,hold_replay_list,holdout_start_ms,manifest=freeze_archives(source_live)
    work=RESEARCH/'work'/'real'; shutil.rmtree(work,ignore_errors=True); work.mkdir(parents=True,exist_ok=True)
    run('-m','unittest','discover','-s','tests','-v')
    run('replay.py','--config','config.yaml','--list',str(cal_list),'--out','work/real/calibration_timeline.csv')
    run('calibrate.py','--config','config.yaml','--timeline','work/real/calibration_timeline.csv','--out','work/real/calibration.json')
    run('optimize.py','--config','config.yaml','--timeline','work/real/calibration_timeline.csv','--calibration','work/real/calibration.json','--out','work/real/leaderboard_v04.csv','--best-config','work/real/best_config_v04.yaml','--best-metrics','work/real/best_calibration_metrics.json','--max-variants',str(args.max_variants))
    run('summarize_leaderboard.py','--leaderboard','work/real/leaderboard_v04.csv','--out','work/real/CALIBRATION_SUMMARY.txt')
    import yaml
    best=yaml.safe_load((work/'best_config_v04.yaml').read_text(encoding='utf-8'))
    status=best.get('research_status','')
    summary={'snapshot_manifest':manifest,'research_status':status,'max_variants':args.max_variants,'holdout_touched':False}
    if status=='calibration_winner_pending_holdout':
        print('\n[study] Calibration produced a PASS winner. Opening frozen HOLDOUT exactly once.',flush=True)
        run('replay.py','--config','config.yaml','--list',str(hold_replay_list),'--emit-from-ms',str(holdout_start_ms),'--out','work/real/holdout_timeline.csv')
        run('evaluate_config.py','--config','work/real/best_config_v04.yaml','--timeline','work/real/holdout_timeline.csv','--calibration','work/real/calibration.json','--decisions','work/real/holdout_decisions.csv','--out','work/real/holdout_metrics.json','--episodes','work/real/holdout_episodes.csv')
        run('visualize.py','--timeline','work/real/holdout_timeline.csv','--decisions','work/real/holdout_decisions.csv','--leaderboard','work/real/leaderboard_v04.csv','--episodes','work/real/holdout_episodes.csv','--outdir','work/real/plots')
        summary['holdout_touched']=True
        summary['holdout_metrics']=json.loads((work/'holdout_metrics.json').read_text(encoding='utf-8'))
    else:
        print('\n[study] No calibration PASS. HOLDOUT remains untouched.',flush=True)
        print('[study] Send me research\\work\\real\\CALIBRATION_SUMMARY.txt and leaderboard_v04.csv.',flush=True)
    (work/'STUDY_RESULT.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print('\n=== DONE ===')
    print('Results:',work)
    if summary['holdout_touched']:
        print('Open visualizer:',work/'plots'/'index.html')
        print('Send me: holdout_metrics.json, best_config_v04.yaml, leaderboard_v04.csv')
    else:
        print('Send me: CALIBRATION_SUMMARY.txt, best_calibration_metrics.json, leaderboard_v04.csv')

if __name__=='__main__':
    try: main()
    except subprocess.CalledProcessError as e:
        print(f'\nERROR: command failed with exit code {e.returncode}',file=sys.stderr)
        raise SystemExit(e.returncode)
