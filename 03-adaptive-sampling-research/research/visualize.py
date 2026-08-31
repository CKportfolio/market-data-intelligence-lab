from __future__ import annotations
import argparse,html
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt

def plot_line(x,y,title,ylabel,path,step=False):
    fig=plt.figure(figsize=(14,4));ax=fig.add_subplot(111)
    if step: ax.step(x,y,where='post')
    else: ax.plot(x,y)
    ax.set_title(title);ax.set_ylabel(ylabel);ax.set_xlabel('time');fig.tight_layout();fig.savefig(path,dpi=130);plt.close(fig)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--timeline',required=True);ap.add_argument('--decisions',required=True);ap.add_argument('--leaderboard');ap.add_argument('--episodes');ap.add_argument('--outdir',required=True);a=ap.parse_args()
    out=Path(a.outdir);out.mkdir(parents=True,exist_ok=True);t=pd.read_csv(a.timeline);d=pd.read_csv(a.decisions);x=pd.to_datetime(t.ts_ms,unit='ms',utc=True)
    plot_line(x,t.price,'BTC primary price','price',out/'price.png')
    plot_line(x,d.heat,'Market heat','heat',out/'heat_timeline.png')
    plot_line(x,d.interval_ms/1000,'Adaptive snapshot interval','seconds',out/'sampling_intervals.png',True)
    plot_line(x,t.fast_trade_rate_s,'FAST trade rate','trades/s',out/'trade_rate.png')
    plot_line(x,t.fast_book_churn_s,'FAST orderbook churn','changed levels/s',out/'book_churn.png')
    if a.leaderboard and Path(a.leaderboard).exists():
        l=pd.read_csv(a.leaderboard);l=l[l.variant.str.startswith('heat-')]
        fig=plt.figure(figsize=(8,5));ax=fig.add_subplot(111);ax.scatter(l.snapshot_reduction,l.ab_weighted_recall);ax.set_xlabel('snapshot reduction');ax.set_ylabel('Tier A+B weighted recall');ax.set_title('Storage vs important-episode recall');fig.tight_layout();fig.savefig(out/'storage_vs_recall.png',dpi=130);plt.close(fig)
    if a.episodes and Path(a.episodes).exists():
        e=pd.read_csv(a.episodes); counts=e.tier.value_counts().reindex(['A','B','C']).fillna(0)
        fig=plt.figure(figsize=(7,4));ax=fig.add_subplot(111);ax.bar(counts.index,counts.values);ax.set_xlabel('Tier');ax.set_ylabel('episodes');ax.set_title('Ground-truth episode counts');fig.tight_layout();fig.savefig(out/'episode_counts.png',dpi=130);plt.close(fig)
    imgs=['price.png','heat_timeline.png','sampling_intervals.png','trade_rate.png','book_churn.png']
    for name in ['storage_vs_recall.png','episode_counts.png']:
        if (out/name).exists():imgs.append(name)
    body='\n'.join(f'<h2>{html.escape(i)}</h2><img src="{html.escape(i)}" style="max-width:100%">' for i in imgs)
    (out/'index.html').write_text(f'<!doctype html><meta charset="utf-8"><title>Adaptive OB v0.4</title><body><h1>Adaptive OB Research v0.4</h1>{body}</body>',encoding='utf-8')
    print('open',out/'index.html')
if __name__=='__main__':main()
