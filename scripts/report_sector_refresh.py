"""Render actual area QC/history and export denominators, never invented observations."""
from pathlib import Path
import csv
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap,BoundaryNorm
from agro_checker.sector_refresh import DIRECTORY,read


def main():
    directory=DIRECTORY/'versions/2026-11-09'
    summary=read(directory/'summary.json')
    fids=sorted(summary['features'])
    rows={fid:[r for r in summary['features'][fid]['history'] if r['source_status']=='ready'] for fid in fids}
    values=np.array([[r['coverage'] for r in rows[fid]] for fid in fids])
    output=ROOT/'docs/assets/sector-refresh-2026-11-09.png'
    output.parent.mkdir(parents=True,exist_ok=True)
    with (directory/'area-scene-quality.csv').open('w',encoding='utf-8-sig',newline='') as file:
        writer=csv.writer(file)
        writer.writerow(['fragment','scene','acquired_at_utc','coverage','cloud_shadow_fraction','ndvi','ndmi','usable'])
        for fid in fids:
            for r in rows[fid]:
                writer.writerow([fid,r['id'],r['acquired_at_utc'],r['coverage'],r['scl_cloud_shadow_fraction'],r['ndvi']['base'],r['ndmi']['base'],r['ndmi']['base'] is not None])
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'axes.spines.top':False,'axes.spines.right':False})
    fig=plt.figure(figsize=(15,12),facecolor='#f8f8f2')
    grid=fig.add_gridspec(2,1,height_ratios=[3.7,1.4],hspace=.38,left=.09,right=.95,top=.855,bottom=.08)
    ax=fig.add_subplot(grid[0]);ax.set_facecolor('#f8f8f2')
    cmap=ListedColormap(['#e4e6e2','#bbc9bb','#30704f'])
    ax.imshow(values,cmap=cmap,norm=BoundaryNorm([0,.5,.8,1.001],3),aspect='auto')
    ax.set_yticks(range(len(fids)),fids)
    labels=[r['acquired_at_utc'][8:10]+'.'+r['acquired_at_utc'][5:7]+'\n'+r['acquired_at_utc'][11:16] for r in rows['R11']]
    ax.set_xticks(range(len(labels)),labels)
    for i in range(len(fids)):
        for j in range(len(labels)):
            pct=values[i,j]*100
            label='<80' if 79.5<=pct<80 else f'{pct:.0f}'
            ax.text(j,i,label,ha='center',va='center',fontsize=9,
                    color='white' if values[i,j]>=.8 else '#39483f')
    ax.set_title('Пригодные пиксели площади, % · тёмно-зелёный ≥80%\nВремя съёмки — UTC; пустые даты не интерполируются',loc='left',fontsize=12,pad=14)
    ax.tick_params(length=0,pad=7)
    ax2=fig.add_subplot(grid[1]);ax2.set_facecolor('#f8f8f2')
    x=np.arange(len(labels))
    for key,color in [('ndvi','#2f6c4f'),('ndmi','#426e8b')]:
        y=np.array([r[key]['base'] if r[key]['base'] is not None else np.nan for r in rows['R11']])
        ax2.plot(x,y,'o-',color=color,label=key.upper(),linewidth=1.8,markersize=6)
    missing=np.array([r['ndmi']['base'] is None for r in rows['R11']])
    ax2.scatter(x[missing],np.full(missing.sum(),-.37),marker='x',color='#93978f',s=35,label='Пропуск',clip_on=False)
    ax2.set_xticks(x,labels);ax2.set_ylim(-.43,.5);ax2.set_xlim(-.5,len(labels)-.5)
    ax2.axhline(0,color='#d8ddd6',linewidth=1);ax2.grid(axis='y',color='#e0e3db')
    ax2.set_title('R11 · наблюдаемые индексы всей приблизительной площади',loc='left',fontsize=12,pad=13)
    ax2.legend(loc='upper left',bbox_to_anchor=(.56,1.35),ncol=3,frameon=False,fontsize=10)
    fig.text(.09,.958,'Свежая история Sentinel-2',fontsize=24,fontweight='bold',color='#244b39')
    fig.text(.09,.925,'21 октября — 8 ноября 2026 · 12 сцен / 10 дат · 27 неподтверждённых обводок',fontsize=13,color='#536457')
    fig.text(.09,.022,'Источник: Earth Search, Sentinel-2 C1 L2A. SCL + проверки спектра/краёв. Покрытие не равно точности модели.\nЭто наблюдения поверхности, не диагноз и не измерение влажности почвы.',fontsize=10,color='#59615b')
    fig.savefig(output,dpi=160,facecolor=fig.get_facecolor());plt.close(fig)
    print(output)


if __name__=='__main__':main()
