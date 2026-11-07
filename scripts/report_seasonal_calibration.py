"""Publish calibration versus detection separately, without post-test tuning."""
from pathlib import Path
from collections import Counter
import sys
import zipfile
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tmp/georef-deps')]
from agro_checker.farm_context_data import read,dump,digest
from agro_checker.seasonal_calibration import D,NAMES


def percent(x):return '—' if x is None else f'{100*x:.1f}%'


if __name__=='__main__':
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    p=read(D/'protocol.json');f=read(D/'frozen-selection.json');r=read(D/'report.json');v=read(D/'verification.json')
    meta=read(D/'meta-oof.json')
    selection_table=read(D/'selection-table.json')
    lossless_minimum={name:min(a['metrics']['fp'] for a in selection_table if a['index']==name and a['lost_tp']==0) for name in NAMES}
    selection=[];main=[];spatial=[]
    for c in f['selected']:
        raw=c['raw_quality'];new=c.get('selection_metrics',{}).get('probability',raw)
        selection.append(f"| {c['index'].upper()} | {c['family']} | {c['threshold']:.1f} | {raw['brier']:.4f} → {new['brier']:.4f} | {raw['logloss']:.4f} → {new['logloss']:.4f} |")
    for name in NAMES:
        x=r['results']['all2026'][name];a,b=x['raw'],x['new'];aa,bb=a['metrics'],b['metrics']
        for label,m in [('Исходный детектор',a),('Выбранная калибровка',b)]:
            mm=m['metrics'];q=m['probability']
            main.append(f"| {name.upper()} | {label} | {mm['tp']} из {mm['tp']+mm['fn']} | {mm['fp']} | {mm['fp_dates']} | {q['brier']:.4f} | {q['logloss']:.4f} | {q['ece']:.4f} |")
        for split,title in [('spatial2024','Резервные площади 2024'),('spatial2026','Резервные площади 2026'),('temporal2026','Остальные площади 2026')]:
            z=r['results'][split][name];a,b=z['raw'],z['new']
            spatial.append(f"| {title} | {name.upper()} | {b['metrics']['pairs']} | {a['probability']['brier']:.4f} → {b['probability']['brier']:.4f} | {b['metrics']['tp']} / {b['metrics']['fp']} | {b['lost_tp']} |")
    asset=ROOT/'docs/assets/farm-reference/seasonal-calibration.png'
    fig,axes=plt.subplots(1,2,figsize=(10.5,4.5),layout='constrained')
    for j,name in enumerate(NAMES):
        ax=axes[j];values=[r['results']['all2026'][name][version]['probability']['brier'] for version in ['raw','new']]
        bars=ax.bar([0,1],values,color=['#a8bbae','#b56a4d']);ax.bar_label(bars,fmt='%.4f',padding=4)
        ax.set_xticks([0,1],['Исходные оценки','Калибровка по 2024']);ax.set_title(name.upper()+' · Brier: меньше — лучше')
        ax.set_ylim(0,.115);ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True);ax.spines[['top','right']].set_visible(False)
    fig.suptitle('Калибровка вероятностей: NDMI улучшился, NDVI ухудшился',fontsize=14,fontweight='bold')
    fig.supxlabel('2026: те же 499 зависимых пар / 27 дат. Все сигналы прежние: NDVI 9 попаданий / 10 ложных, NDMI 3 / 3.',fontsize=9)
    fig.savefig(asset,dpi=160,facecolor='white');plt.close(fig)
    reliability_asset=ROOT/'docs/assets/farm-reference/seasonal-calibration-reliability.png'
    fig,axes=plt.subplots(1,2,figsize=(10.5,4.9),layout='constrained')
    for j,name in enumerate(NAMES):
        ax=axes[j];ax.plot([0,1],[0,1],linestyle='--',color='#aab1b4',label='Совпадение оценки и частоты')
        for version,label,color in [('raw','Исходные оценки','#779488'),('new','Калибровка','#b56a4d')]:
            bins=r['results']['all2026'][name][version]['probability']['reliability']
            ax.plot([b['mean_probability'] for b in bins],[b['event_frequency'] for b in bins],marker='o',color=color,label=label)
        ax.set(xlim=(0,1),ylim=(0,1),title=name.upper(),xlabel='Средняя оценка',ylabel='Наблюдаемая частота снижения')
        ax.grid(alpha=.2);ax.spines[['top','right']].set_visible(False);ax.legend(fontsize=8)
    fig.suptitle('Надёжность оценок: фиксированные интервалы вероятности',fontweight='bold',fontsize=14)
    fig.supxlabel('Равный вес дат; размеры групп и число дат сохранены в report.json. Некоторые группы малы; условная гарантия не установлена.',fontsize=9)
    fig.savefig(reliability_asset,dpi=160,facecolor='white');plt.close(fig)
    fallback=Counter((a['index'],a['start']) for a in meta['audits'] if a['fallback'])
    ndvi=r['results']['all2026']['ndvi'];ndmi=r['results']['all2026']['ndmi']
    relative_ndmi=100*(1-ndmi['new']['probability']['brier']/ndmi['raw']['probability']['brier'])
    calibration_half=float(1/(1+np.exp(-f['calibrators'][0].get('coef',[0,0])[1]))) if f['calibrators'][0]['family']=='platt' else None
    text=f'''# Калибровка сезонного детектора и сохранение найденных снижений

Дата: 21 октября 2026. **Снизить ложные тревоги без потери найденных снижений не удалось.** Все прежние сигналы сохранены. Для NDMI улучшилась общая вероятностная ошибка, для NDVI она ухудшилась. Калибровки остаются исследовательскими; в продукт не подключены.

## Какие детекторы проверяли

- NDVI: сохранённый сезонный `season25` из предыдущего эксперимента, порог 0,5 и прежнее условие отрицательного прошлого темпа. На 2026 до калибровки — 9 попаданий из 50, 10 ложных.
- NDMI: сохранённый временной классификатор `2024_only`, порог 0,85 и прежнее условие. До калибровки — 3 попадания из 42, 3 ложные. Принятого сезонного NDMI-детектора нет; его существование не предполагается.

Их финальные веса, признаки, условие и пороги неизменны. Дополнительный слой может только отфильтровать исходную тревогу: `старая тревога AND новая оценка >= порог`. Он не может создать новую тревогу, но может потерять верную — это проверяется по конкретным случаям, а не только сумме попаданий.

## Почему одного пересчёта вероятности недостаточно

Обычные монотонные методы меняют численные оценки, сохраняя порядок; изотоническое преобразование может объединять оценки в равные группы. Они не добавляют информации для различения событий. Поэтому изменение вероятностной ошибки и изменение качества тревог рассматриваются отдельно. Brier и logloss оценивают не только калибровку, но и различимость случаев; снижение Brier не доказывает надёжность каждой выданной вероятности. Методологический источник — [официальная документация scikit-learn](https://scikit-learn.org/stable/modules/calibration.html).

## Обучение без утечки

Собраны причинные прогнозы: {f['calibration_pairs']} пар «площадь × дата» на {f['calibration_dates']} датах 2024. Для каждой даты базовая модель обучалась только на примерах, чей целевой снимок был строго раньше этой даты. Первоначально требуется минимум 5 дат / 20 текущих пар. Ранние годы добавлялись только в прежний NDVI-алгоритм. Собственные обучающие предсказания не использовались для калибровки.

Затем пять внешних проверок июнь–октябрь: калибратор видит только завершённые целевые даты до начала месяца; проверка — выдачи в месяце с целью до следующего месяца. Получились те же 274 пары / 25 дат. Пересекающие границы окна исключены; R23–R27 не обучают базовую модель, калибратор, нормировки и не выбирают пороги.

Это последовательные обновления базовой модели внутри месяца. Поэтому их исходные результаты на отборе отличаются от прежнего ежемесячного OOF: NDVI 4 попадания / 0 ложных, NDMI 14 / 2. Разные результаты отбора не подменяют контроль 2026: его финальные базовые бинарные модели сохранены точно прежними.

При недостатке классов/дат применяется исходная оценка без калибровки и остаётся в знаменателе. В мета-проверке fallback встречался для {len(fallback)} сочетаний «индекс × месяц»; эти периоды не удалялись. Финальная калибровка обучена на всех причинных прогнозах 2024 до {f['calibration_max_target']}.

Ограничение: калибратор обучается на оценках последовательно растущих моделей, а затем применяется к сохранённой финальной модели. Их распределения могут отличаться; это не доказанная гарантия переноса. Настройки базовых алгоритмов и предыдущие условия ранее выбирались по 2024; повторное использование 2024 делает оценки отбора оптимистичными.

## Что попробовали и как выбирали

Семь вариантов: исходные оценки; temperature scaling; регуляризованный Platt с неотрицательным наклоном; монотонная beta-калибровка; изотоническая с 50% исходной оценки; условная логит-поправка по четырём прошлым сезонным признакам; её 50% смесь с исходной оценкой.

Условные признаки — положение в наблюдавшемся диапазоне, прошлый недельный темп относительно амплитуды, интервал до предыдущего снимка и флаг поддержки истории. Это не культура/BBCH и не будущая сезонная информация. Варианты и регуляризация зафиксированы до обучения; изотоническая калибровка на малых данных особенно ограничена по переносу.

Порог фильтра выбирается только по мета-OOF 2024. Условия: сохранить каждый прежний TP, не добавить тревог, не увеличить ложные пары и даты, не ухудшить Brier и logloss. Затем минимизировать ложные, Brier и logloss. При одинаковом результате выбирается **меньший порог фильтра**, чтобы не вводить лишнее отсечение без подтверждения. Порог 0 означает отсутствие дополнительной фильтрации.

| Индекс | Выбранная калибровка | Порог фильтра | Brier на отборе | Logloss на отборе |
|---|---|---:|---:|---:|
{chr(10).join(selection)}

Оба порога — 0. По 2024 не подтверждено безопасное отсечение тревог, которое одновременно сохраняет все найденные события и улучшает вероятностные оценки. Тот факт, что наблюдаемые ложные тревоги малы или отсутствуют, не позволяет обещать их отсутствие в другом году.

Даже без условий по Brier/logloss среди проверенных вариантов с сохранением каждого прежнего TP минимум ложных на отборе: NDVI {lossless_minimum['ndvi']}, NDMI {lossless_minimum['ndmi']}. Для NDMI уменьшить две исходные ложные без потери одного из 14 верных в этой сетке не получилось. Для NDVI исходных ложных на отборе нет, поэтому дополнительное отсечение не имеет подтверждённой пользы.

## Проверка замороженного выбора на 2026

Те же 499 пар / 27 дат, выдачи с 28 июля, факты до 19 октября. 2026 уже изучался ранее, поэтому это проверка без нового подбора, а не свежий слепой тест. 2025 отсутствует в текущем архиве. Спектральные снижения не являются подтверждёнными повреждениями или полезностью осмотра.

| Индекс | Вариант | Найдено / событий | Ложные пары | Дат с ложными | Brier ↓ | Logloss ↓ | ECE ↓ |
|---|---|---:|---:|---:|---:|---:|---:|
{chr(10).join(main)}

Для NDMI Brier снизился на {relative_ndmi:.1f}%, logloss также уменьшился. Но сигналов осталось столько же: это улучшение численных оценок в этом архиве, а не улучшение обнаружения. Для NDVI Platt поднял оценку исходного 0,5 примерно до {calibration_half:.2f}; эта поправка с 2024 не перенеслась и усилила завышение оценок на 2026. Это механизм преобразования; причины межгодового сдвига по культурам/операциям/версии не установлены.

![Вероятностная ошибка](assets/farm-reference/seasonal-calibration.png)

![Надёжность оценок](assets/farm-reference/seasonal-calibration-reliability.png)

Кривые построены с заранее фиксированными границами групп и равным суммарным весом дат; размеры групп и число дат записаны в JSON. Например, после калибровки NDMI в группе 0,8–1,0 всего 3 пары на 3 датах, без подтверждённых снижений. Поэтому нельзя объявлять высокие оценки надёжными вероятностями для агронома по улучшению одного среднего Brier. ECE зависит от разбиения и также не является гарантией.

## Пространственный перенос и сохранение случаев

| Поднабор | Индекс | Пар | Brier: исходный → новый | TP / FP после | Потеряно прежних TP |
|---|---|---:|---:|---:|---:|
{chr(10).join(spatial)}

На пяти резервных площадях 2024 обе выбранные калибровки ухудшили Brier. На резерве 2026 Brier обеих немного улучшился. Это неоднородный перенос, а не подтверждённая универсальная калибровка. Резервные фрагменты близки к обучающим и не являются независимым хозяйством.

По конкретным случаям сохранены все 9 прежних попаданий NDVI и все 3 NDMI; все ложные также сохранены. Новый слой не добавил ни одной тревоги. Порог после просмотра 2026 не повышался ради красивого числа ложных.

## Решение

**Цель не достигнута, рабочие модели и интерфейс сохранены.** NDMI получил ограниченное улучшение вероятностной ошибки, NDVI — ухудшение. Принятой модели вероятности повреждения или полезности осмотра из этого не следует.

Для следующей проверки нужны независимые даты/сезоны с достаточным числом реальных ложных тревог исходного детектора, заранее выделенная проверка и сведения о культурах/операциях. Уменьшать ложные, сохраняя все найденные события, по текущему отбору надёжно не научились. Повторного подбора по 2026 здесь нет.

## Воспроизведение

`scripts/train_seasonal_calibration.py protocol|fit|evaluate`, `scripts/verify_seasonal_calibration.py`. Проверены причинные сроки базового OOF, отделение калибровки от собственных обучающих предсказаний, месячные разрывы, резерв, воспроизведение выбора и параметров, совпадение всех 499 исходных оценок и базовых сигналов с прошлым экспериментом, идентичность найденных случаев и неизменность {v['protected_files']} прежних файлов. Заморозка: `{r['frozen_sha256']}`. После неё повторное обучение и оценка запрещены.
'''
    doc=ROOT/'docs/SEASONAL_CALIBRATION_REPORT.md';doc.write_text(text,encoding='utf-8')
    files=[doc,asset,reliability_asset,ROOT/'agro_checker/seasonal_calibration.py',ROOT/'tests/test_seasonal_calibration.py',
        ROOT/'scripts/train_seasonal_calibration.py',ROOT/'scripts/verify_seasonal_calibration.py',Path(__file__),*[q for q in D.iterdir() if q.is_file()]]
    bundle=ROOT/'outputs/seasonal-calibration-2026-10-21.zip'
    with zipfile.ZipFile(bundle,'w',zipfile.ZIP_DEFLATED) as z:
        for q in files:z.write(q,q.relative_to(ROOT).as_posix())
    dump(ROOT/'outputs/seasonal-calibration-2026-10-21/manifest.json',dict(archive=bundle.relative_to(ROOT).as_posix(),sha256=digest(bundle),
        files=[dict(path=q.relative_to(ROOT).as_posix(),sha256=digest(q)) for q in files],production_promoted=False))
    print(doc);print(bundle)
