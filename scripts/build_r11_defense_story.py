"""Descriptive presentation figures from the existing R11 pair. No model fitting."""
import csv
import hashlib
import io
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.cm import ScalarMappable
import numpy as np
from PIL import Image
from rasterio.transform import from_origin

from agro_checker import farm_scouting as scout
from agro_checker.farm_context_analysis import masks, scenarios, spectral_stats

ROOT=Path(__file__).resolve().parents[1]
INK='#252A27';MUTED='#626A64';GREEN='#35634B';RULE='#DDE0DD'
LABELS={'base':'Исходная обводка','N':'Север','NE':'Северо-восток','E':'Восток',
        'SE':'Юго-восток','S':'Юг','SW':'Юго-запад','W':'Запад','NW':'Северо-запад',
        'core':'Внутреннее ядро','outer':'Расширенная обводка'}


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def number(value,digits=3):return f'{value:.{digits}f}'.replace('.',',').replace('-','−')


def story_sources():
    pair=scout.detail('R11','recent2026')['pair']
    return [scout.R/'approximate-areas.geojson',scout.D/'protocol.json',scout.D/'summary.json',scout.D/'satellite.json',
            *[scout.D/'sentinel'/pair[k]['id']/'indices.npz' for k in ('before','after')],
            ROOT/'agro_checker/farm_scouting.py',ROOT/'agro_checker/farm_context_analysis.py']


def evidence():
    d=scout.detail('R11','recent2026');pair=d['pair'];p=scout.archive()[0]
    assert pair['before']['at'].startswith('2026-09-21') and pair['after']['at'].startswith('2026-10-14')
    assert pair['same_baseline'] and pair['before']['baseline']=='05.13'
    poly=scout.fragment('R11')[3];variants=scenarios(poly,d['working_band_m'])
    aa,bb=[scout.raster(pair[k]['id']) for k in ('before','after')]
    valid=aa['valid']&bb['valid'];transform=from_origin(p['bounds_utm'][0],p['bounds_utm'][3],20,20)
    indices=masks(variants,transform,valid.shape);rows=[]
    for key in ('ndvi','ndmi'):
        for name,_ in variants:
            stats=spectral_stats(bb[key]-aa[key],valid,indices[name])
            assert stats['usable'],(key,name)
            rows.append({'index':key,'scenario':name,'label':LABELS[name],**stats})
        selected=[v['value'] for v in rows if v['index']==key]
        assert np.isclose(min(selected),pair['metrics'][key]['all_scenario_min'])
        assert np.isclose(max(selected),pair['metrics'][key]['all_scenario_max'])
        assert np.isclose(selected[0],pair['metrics'][key]['base'])
    assert all(v['value']<0 for v in rows)
    assert d['working_band_m']==50
    result={'fragment':'R11','period':'recent2026','pair':pair,'working_band_m':d['working_band_m'],
            'scenarios':rows,'statistic':'median of per-pixel after-minus-before index on common valid cells',
            'scenario_definition':'base; eight translations of Euclidean length 50 m; inward and outward 50 m buffers',
            'minimum_valid_cells':10,'minimum_common_coverage':.8,'grid_m':20,
            'limitations':['Approximate screenshot reconstruction, ownership and boundaries unconfirmed',
                'Eleven deterministic geometry scenarios, not a confidence interval or all possible boundaries',
                'Observed spectral change, not a forecast, disease label or measured soil moisture',
                'NDMI decline remains negative but is less than 0.10 in the outer scenario',
                'Difference of medians differs from median of pixel differences',
                'Selected descriptive example, not evaluation across the farm or proof of useful visits'],
            'source_hashes':{path.relative_to(ROOT).as_posix():digest(path) for path in story_sources()}}
    return result,variants


def header(fig,label,title,subtitle):
    fig.text(.06,.945,'onlyfarms',fontsize=14,fontweight='bold',color=INK)
    fig.text(.94,.945,label,ha='right',fontsize=12,color=MUTED)
    fig.add_artist(plt.Line2D([.06,.94],[.918,.918],transform=fig.transFigure,color=RULE,lw=.7))
    fig.text(.06,.852,title,fontsize=27,fontweight='bold',color=INK)
    fig.text(.06,.798,subtitle,fontsize=15,color=MUTED)


def save(fig,folder,name):
    for ext in ('png','svg'):fig.savefig(folder/f'{name}.{ext}',dpi=200,facecolor='white')
    plt.close(fig)


def make_story(out):
    folder=out/'main';folder.mkdir(parents=True,exist_ok=True)
    data,variants=evidence();pair=data['pair'];metrics=pair['metrics'];band=data['working_band_m']
    (folder/'R11-evidence.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    with (folder/'R11-boundary-scenarios.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['index','scenario','label','value','valid_cells','total_cells','valid_fraction','usable'])
        writer.writeheader();writer.writerows(data['scenarios'])

    fig=plt.figure(figsize=(16,9),facecolor='white')
    header(fig,'01 / НАБЛЮДЕНИЕ','На R11 изменились спутниковые индексы',
           'Sentinel-2, 21 сентября и 14 октября 2026. Между съёмками 23 дня.')
    for x,side,title in [(.06,'before','21.09.2026'),(.36,'after','14.10.2026'),(.66,'delta','Изменение NDMI')]:
        ax=fig.add_axes([x,.27,.28,.45])
        raw=scout.map_png('R11','recent2026',pair['before']['id'],pair['after']['id'],'ndmi',side)
        ax.imshow(Image.open(io.BytesIO(raw)),interpolation='nearest');ax.set_axis_off();ax.set_title(title,fontsize=17,pad=12,loc='left')
        colors=['#B69167','#DFE0C6','#2A798B'] if side!='delta' else ['#A94A3A','#F2ECD0','#327D5A']
        cmap=LinearSegmentedColormap.from_list(side,colors)
        cb=fig.add_axes([x+.025,.23,.23,.012]);limit=1 if side!='delta' else .3
        bar=fig.colorbar(ScalarMappable(norm=Normalize(-limit,limit),cmap=cmap),cax=cb,orientation='horizontal',ticks=[-limit,0,limit])
        bar.outline.set_visible(False);bar.ax.tick_params(length=0,labelsize=11,pad=5)
        bar.ax.set_xticklabels([number(-limit,1),'0',number(limit,1)])
    fig.text(.06,.16,f'ΔNDMI {number(metrics["ndmi"]["base"])}     ΔNDVI {number(metrics["ndvi"]["base"])}',fontsize=20,fontweight='bold')
    fig.text(.66,.16,f'Общее покрытие {number(pair["coverage"]*100,1)}%',fontsize=19)
    fig.text(.06,.105,'Медиана разности на одних и тех же пригодных пикселях. Шахматная заливка обозначает пропуски.',fontsize=13,color=MUTED)
    fig.text(.06,.067,'Сетка 20 м, версия обработки 05.13. Контур приблизительный. Остаточные облака возможны.',fontsize=13,color=MUTED)
    fig.text(.06,.030,'Архивное наблюдение. Причину изменения и необходимость действий ещё предстоит проверить на месте.',fontsize=13,color=MUTED)
    save(fig,folder,'01-r11-observed-change')

    fig=plt.figure(figsize=(16,9),facecolor='white')
    header(fig,'02 / ГРАНИЦЫ','Снижение NDMI сохраняется при изменении контура',
           'R11, та же пара съёмок. Восемь сдвигов на 50 м, внутреннее ядро и расширение на 50 м.')
    ax=fig.add_axes([.07,.27,.34,.43]);center=variants[0][1].centroid
    for name,poly in variants:
        xx,yy=np.array(poly.exterior.coords).T
        color=INK if name=='base' else GREEN if name=='core' else '#AD7864' if name=='outer' else '#C9CEC9'
        ax.plot(xx-center.x,yy-center.y,color=color,lw=1.8 if name in ('base','core','outer') else .6,
                ls='-' if name in ('base','core','outer') else '--',zorder=3 if name=='base' else 2)
    ax.set_aspect('equal');ax.set_xlabel('Восток, м',fontsize=12);ax.set_ylabel('Север, м',fontsize=12)
    ax.tick_params(labelsize=11,length=0);ax.spines[['top','right']].set_visible(False)
    for x,color,label in [(.06,INK,'Исходная'),(.175,GREEN,'Ядро'),(.27,'#AD7864','Расширение')]:
        fig.add_artist(plt.Line2D([x,x+.018],[.20,.20],transform=fig.transFigure,color=color,lw=2))
        fig.text(x+.025,.194,label,fontsize=11,color=MUTED)
    rows=[r for r in data['scenarios'] if r['index']=='ndmi']
    ax=fig.add_axes([.54,.25,.29,.46]);yy=np.arange(11)
    ax.axvline(0,color='#9FA79F',lw=1)
    ax.set_axisbelow(True);ax.grid(axis='x',color='#ECEEEC',lw=.7)
    for i,row in enumerate(rows):
        color=INK if row['scenario']=='base' else GREEN if row['scenario']=='core' else '#AD7864' if row['scenario']=='outer' else '#7E8A80'
        ax.scatter(row['value'],i,c=color,s=35,marker='D' if row['scenario']=='base' else 'o',zorder=3)
        ax.text(.006,i,number(row['value']),ha='left',va='center',fontsize=12,clip_on=False)
        ax.text(.042,i,number(row['valid_fraction']*100,1)+'%',ha='left',va='center',fontsize=12,color=MUTED,clip_on=False)
    ax.set_yticks(yy,[r['label'] for r in rows],fontsize=12);ax.set_ylim(10.7,-.7);ax.set_xlim(-.15,.002)
    ax.set_xticks([-.15,-.10,-.05,0],['−0,15','−0,10','−0,05','0']);ax.tick_params(length=0,pad=9,labelsize=12)
    ax.spines[['top','left','right','bottom']].set_visible(False);ax.set_xlabel('Изменение NDMI',fontsize=12,labelpad=10)
    fig.text(.845,.737,'ΔNDMI',fontsize=12,color=MUTED);fig.text(.912,.737,'Покрытие',fontsize=12,color=MUTED)
    lo,hi=[metrics['ndmi'][k] for k in ('all_scenario_min','all_scenario_max')]
    fig.text(.06,.135,f'Все 11 вариантов: от {number(lo)} до {number(hi)}. Знак сохраняется, величина различается.',fontsize=17)
    fig.text(.06,.082,'В расширенной обводке снижение меньше 0,10. Направление устойчиво в проверенных сценариях.',fontsize=13,color=MUTED)
    fig.text(.06,.041,'Это анализ чувствительности одного примера, без вероятностной гарантии границ и без проверки агрономической причины.',fontsize=12.5,color=MUTED)
    save(fig,folder,'02-r11-boundary-sensitivity')

    fig=plt.figure(figsize=(16,9),facecolor='white')
    header(fig,'03 / СЦЕНАРИЙ','От снимка к проверке на месте',
           'Рабочий прототип сохраняет основание задачи и связывает будущий результат с выбранным местом.')
    stages=[('Карта','Площадь R11\nПриблизительная\nобводка','Доступна'),
            ('Снимки','Даты и изменение\nПокрытие и пропуски\nВыбор места','Реальный архив'),
            ('Задача','Место и вопрос\nОтветственный\nПлановая дата','Пустая форма'),
            ('Результат','Дата и заметки\nФотографии\nСледующий шаг','Просмотр формы'),
            ('История','Повторные посещения\nИзменения индексов\nпо датам','Записей пока нет')]
    fig.add_artist(plt.Line2D([.06,.94],[.695,.695],transform=fig.transFigure,color=RULE,lw=1))
    for i,(title,body,status) in enumerate(stages):
        x=.06+i*.182
        fig.text(x,.72,f'{i+1:02}',fontsize=13,color=MUTED)
        fig.text(x,.625,title,fontsize=20,fontweight='bold')
        fig.text(x,.465,body,fontsize=14,linespacing=1.8,color=MUTED)
        fig.text(x,.32,status,fontsize=13,color=GREEN if i<2 else MUTED)
    fig.add_artist(plt.Line2D([.06,.94],[.23,.23],transform=fig.transFigure,color=RULE,lw=.7))
    fig.text(.06,.166,'На экранах 22 октября 2026: архивные данные, пустые формы и история без посещений.',fontsize=16)
    fig.text(.06,.105,'Следующий этап с агрономом: провести осмотры, внести результаты и проверить полезность выбранных мест.',fontsize=14,color=MUTED)
    fig.text(.06,.052,'Сотрудничество с агрономом подтверждает команда. Польза осмотров, экономия и точность прогноза пока не подтверждены.',fontsize=12.5,color=MUTED)
    save(fig,folder,'03-scouting-workflow')
    return data
