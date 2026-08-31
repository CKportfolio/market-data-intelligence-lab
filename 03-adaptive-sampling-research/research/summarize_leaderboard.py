from __future__ import annotations
import argparse,json
from pathlib import Path
import pandas as pd

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--leaderboard',required=True);ap.add_argument('--out');a=ap.parse_args()
    d=pd.read_csv(a.leaderboard);d=d[d.variant.str.startswith('asym-')].copy()
    checks=d.checks.apply(lambda x: json.loads(x) if isinstance(x,str) else {})
    gate_names=['tier_a_recall','tier_b_recall','ab_weighted_recall','tier_a_snapshot_recall','tier_b_snapshot_recall','reaction','oversampling','false_fast','reduction']
    lines=[]
    lines.append('=== V0.4 QUALITY-FIRST GATE COUNTS ===')
    for g in gate_names: lines.append(f'{g:28s} {sum(bool(x.get(g,False)) for x in checks):4d} / {len(d)}')
    lines.append('')
    lines.append('=== RANGES ===')
    for c in ['tier_a_recall','tier_b_recall','ab_weighted_recall','tier_a_snapshot_recall','tier_b_snapshot_recall','reaction_p95_ms','oversampling_p95_ms','false_fast_time','snapshot_reduction','mid_reconstruction_mae_bp','imbalance_reconstruction_mae']:
        if c in d: lines.append(f'{c:32s} {d[c].min():.6g} .. {d[c].max():.6g}')
    lines.append('')
    lines.append('=== TOP 20 CLOSEST TO PASS (QUALITY FIRST) ===')
    cols=['variant','gates_passed','tier_a_snapshot_recall','tier_b_snapshot_recall','tier_a_recall','tier_b_recall','ab_weighted_recall','reaction_p95_ms','oversampling_p95_ms','false_fast_time','snapshot_reduction','mid_reconstruction_mae_bp','pass']
    top=d.sort_values(['gates_passed','tier_a_snapshot_recall','tier_b_snapshot_recall','ab_weighted_recall','oversampling_p95_ms','false_fast_time'],ascending=[False,False,False,False,True,True])[cols].head(20)
    lines.append(top.to_string(index=False))
    lines.append('')
    lines.append('NOTE: v0.4 does not maximize snapshot reduction. >=40% is only a minimum gate; winner ranking is quality-first.')
    text='\n'.join(lines); print(text)
    if a.out:
        Path(a.out).parent.mkdir(parents=True,exist_ok=True);Path(a.out).write_text(text,encoding='utf-8')
if __name__=='__main__':main()
