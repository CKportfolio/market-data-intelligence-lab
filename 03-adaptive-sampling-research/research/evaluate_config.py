from __future__ import annotations
import argparse,json
from pathlib import Path
import pandas as pd
from dataio import load_yaml
from sampler import simulate
from evaluator import evaluate, episodes_frame

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--config',required=True);ap.add_argument('--timeline',required=True);ap.add_argument('--calibration',required=True);ap.add_argument('--decisions',required=True);ap.add_argument('--out',required=True);ap.add_argument('--episodes');a=ap.parse_args()
    cfg=load_yaml(a.config);df=pd.read_csv(a.timeline);cal=json.loads(Path(a.calibration).read_text(encoding='utf-8'));dec=simulate(df,cfg,cal);m=evaluate(df,dec,cal,cfg)
    Path(a.decisions).parent.mkdir(parents=True,exist_ok=True);dec.to_csv(a.decisions,index=False);Path(a.out).write_text(json.dumps(m,indent=2),encoding='utf-8')
    if a.episodes: episodes_frame(df,cal,cfg).to_csv(a.episodes,index=False)
    print(json.dumps(m,indent=2))
if __name__=='__main__':main()
