"""Report only frozen primary/diagnostic results; never retune from test outcomes."""
from pathlib import Path
import sys
import zipfile
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tmp/georef-deps')]
from agro_checker.farm_context_data import read,dump,digest
from agro_checker.season_version_decline import D,NAMES,key


LABELS={
    '2024_only':'Только 2024', 'raw_pool':'Простое объединение лет',
    'pool_fixed_mass':'Пул с фиксированным весом обучения', 'balanced25':'Ранние годы: вес 25%',
    'season25':'Относительная сезонная динамика + вес 25%', 'version25':'Версия обработки + вес 25%',
    'season_version25':'Сезон + версия + вес 25%',
    'overlap_season_version25':'Сезон + версия + сходство входов',
    'consensus_season_version25':'Согласие контроля и адаптированной модели'}


def percent(v):return '—' if v is None else f'{v*100:.1f}%'


if __name__=='__main__':
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    p=read(D/'protocol.json');f=read(D/'frozen-selection.json');r=read(D/'report.json')
    diag=read(D/'diagnosis-2024.json');verify=read(D/'verification.json')
    predictions=read(D/'test-predictions.json')
    control={key(v):v for v in predictions if v['split']=='all2026' and v['version']=='2024_only'}
    adapted={key(v):v for v in predictions if v['split']=='all2026' and v['version']=='best_adapted'}
    transitions={}
    for label,event_value in [('tp',True),('fp',False)]:
        kk=[k for k,v in control.items() if (v['actual'][0]-v['persistence'][0]<=-.10)==event_value]
        transitions[label]=dict(removed=sum(control[k]['new_alarm'][0] and not adapted[k]['new_alarm'][0] for k in kk),
            added=sum(not control[k]['new_alarm'][0] and adapted[k]['new_alarm'][0] for k in kk))
    dump(D/'signal-transitions.json',dict(index='ndvi',versus='2024_only',changes=transitions,
        purpose='Descriptive post-freeze analysis only; no selection or thresholds changed'))
    years=[]
    for y,v in diag['by_year'].items():
        a,b=v['median_index']
        years.append(f"| {y} | {v['pairs']} / {v['dates']} | {a:.3f} / {b:.3f} | {', '.join(v['baselines'])} |")
    choice=[]
    for j,group in enumerate(f['choices']):
        for c in group:
            if c['enabled']:
                m=c['metrics'];detail=f"{m['tp']} из {m['tp']+m['fn']}; 0 ложных; {m['tp_dates']} даты"
            else:detail='Не прошёл условия отбора'
            choice.append(f"| {NAMES[j].upper()} | {LABELS[c['regime']]} | {detail} |")
    result=[]
    for j,name in enumerate(NAMES):
        for label,version,which in [('Сохранение индекса','primary','persistence'),('Рабочий Ridge','primary','legacy'),
            ('Контроль только 2024','2024_only','new'),('Простой пул ранних лет','raw_pool','new'),
            ('Лучший адаптированный кандидат по 2024','best_adapted','new')]:
            m=r['results']['all2026'][version][name][which]
            if version=='best_adapted' and not f['best_adapted'][j]['enabled']:
                label+=' — отказ, сохранён старый выход'
            if version=='raw_pool' and not f['evaluations']['raw_pool'][j]['enabled']:
                label+=' — отказ, сохранён старый выход'
            result.append(f"| {name.upper()} | {label} | {m['tp']} из {m['tp']+m['fn']} | {m['fp']} | {m['fp_dates']} | {percent(m['recall'])} | {percent(m['precision'])} |")
    spatial=[]
    for split,title in [('spatial2024','Резервные площади 2024'),('spatial2026','Резервные площади 2026'),('temporal2026','Остальные площади 2026')]:
        m=r['results'][split]['best_adapted']['ndvi']['new']
        spatial.append(f"| {title} | {m['pairs']} | {m['tp']} из {m['tp']+m['fn']} | {m['fp']} | {m['fp_dates']} |")
    asset=ROOT/'docs/assets/farm-reference/season-version-decline.png'
    fig,axes=plt.subplots(1,2,figsize=(12.5,4.8),layout='constrained')
    labels=['Сохранить\nиндекс','Рабочий\nRidge','Контроль\n2024','Простой\nпул','Сезонный\nкандидат']
    entries=[('primary','persistence'),('primary','legacy'),('2024_only','new'),('raw_pool','new'),('best_adapted','new')]
    for ax,metric,title in zip(axes,['tp','fp'],['Найденные снижения NDVI','Ложные сигналы NDVI']):
        values=[r['results']['all2026'][a]['ndvi'][b][metric] for a,b in entries]
        bars=ax.bar(np.arange(5),values,color=['#bac8c0','#8ca1aa','#a6bbad','#c6b183','#b46548'])
        ax.bar_label(bars,padding=3);ax.set_xticks(np.arange(5),labels,fontsize=9)
        ax.set_title(title,fontsize=12);ax.set_ylim(0,max(values)+4)
        ax.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))
        ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True);ax.spines[['top','right']].set_visible(False)
    fig.suptitle('Сезонная адаптация: меньше ложных тревог, но ниже полнота',fontsize=14,fontweight='bold')
    fig.supxlabel('2026: 499 зависимых пар / 27 дат; 50 снижений NDVI. Выбор и дополнительный кандидат зафиксированы только по 2024.',fontsize=9)
    fig.savefig(asset,dpi=160,facecolor='white');plt.close(fig)
    diagnosis_asset=ROOT/'docs/assets/farm-reference/season-version-diagnosis.png'
    fig,axes=plt.subplots(1,2,figsize=(11.5,4.4),layout='constrained')
    shares=diag['fold_weight_shares']
    bars=axes[0].bar(np.arange(5),[100*v['source_share'] for v in shares],color='#a6b9b0')
    axes[0].bar_label(bars,fmt='%.0f%%',padding=3);axes[0].axhline(25,color='#b46548',linestyle='--',label='Новый предел: 25%')
    axes[0].set_xticks(np.arange(5),['Июнь','Июль','Август','Сентябрь','Октябрь']);axes[0].set_ylim(0,105)
    axes[0].set_title('Вес ранних лет при простом объединении');axes[0].legend(fontsize=9)
    x=np.arange(4)
    for j,name in enumerate(NAMES):
        axes[1].plot(x,[v['median_index'][j] for v in diag['by_year'].values()],marker='o',label=name.upper())
    axes[1].set_xticks(x,list(diag['by_year']));axes[1].set_title('Медианы индексов с равным весом дат');axes[1].legend()
    for ax in axes:ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True);ax.spines[['top','right']].set_visible(False)
    fig.supxlabel('Диагностика только 2019–2021 и 2024. Различия не устанавливают причину; состав дат и культур может отличаться.',fontsize=9)
    fig.savefig(diagnosis_asset,dpi=160,facecolor='white');plt.close(fig)
    frozen_primary='; '.join(name.upper()+': '+LABELS.get(c['regime'],'отказ') for name,c in zip(NAMES,f['primary']))
    ndvi=r['results']['all2026']['best_adapted']['ndvi']['new']
    text=f'''# Сезонные различия и версии Sentinel-2: отдельный эксперимент

Дата: 21 октября 2026. **Цель «обнаруживать больше резких снижений без роста ложных тревог» не достигнута.** Сезонная адаптация уменьшила ложные сигналы относительно прежнего исследовательского классификатора, но сохранила их больше рабочего Ridge. Кандидаты не подключены. Точечный прогноз, диапазоны, старый детектор, интерфейс и журнал сохранены.

## Почему ранний архив не помог при простом добавлении

Диагностика использует только обучающие ранние годы и 2024 вне резервных площадей.

1. **Ранний архив определял большую часть обучения.** При равном весе дат его доля — {percent(diag['raw_pool_source_share'])} в итоговом пуле, в первой проверке июня — {percent(shares[0]['source_share'])}. Это измеренная доля веса, а не доказательство единственной причины ухудшения.
2. **Различаются уровни и динамика индексов.** Ранние годы неоднородны между собой. Медианы ниже рассчитаны с одинаковым весом дат, поэтому отличаются от обычной медианы всех пар.

| Год | Пар / дат | Медиана NDVI / NDMI | Версии обработки |
|---|---:|---:|---|
{chr(10).join(years)}

3. **Один и тот же признак продолжения снижения имеет разную связь с целью.** Среди пар с совместным прошлым отрицательным темпом и заданным уровнем индекса падение NDVI на следующем снимке встречается в 2024 у 38 из 58 пар (65,5%), в 2021 — у 16 из 39 (41,0%). Для NDMI — 38 из 58 против 14 из 36 (38,9%). Это зависимые пары и описательная связь. Более ранние годы меняют не только распределение входов, но и наблюдаемую связь входа с событием. Одно перевзвешивание входов не гарантирует её восстановления.
4. **Сезон и версия обработки смешаны.** Все ранние обучающие пары имеют PB05.00. В 2024 PB05.10 приходится на март–июль, PB05.11 — на август–октябрь. В сохранённом наборе нет пар одного срока съёмки и места, переобработанных двумя версиями, чтобы независимо оценить эффект версии.

По [официальному описанию Copernicus](https://sentiwiki.copernicus.eu/web/s2-processing), версии обозначают изменения обработки; Collection-1 предусматривает согласованную переобработку раннего архива. Это основание учитывать метаданные, но не основание автоматически прибавлять поправку к NDVI/NDMI. Scale и offset уже проверены при сборе. Из этого эксперимента нельзя причинно приписать сдвиг именно PB, культуре, погоде или операциям.

![Диагностика распределений и веса](assets/farm-reference/season-version-diagnosis.png)

Ранее проверенные метки в основном сохраняются на общих пригодных пикселях: NDVI 172 из 175, NDMI 154 из 157. Поэтому одну смену облачной поддержки нельзя считать достаточным объяснением проблемы. Остаточные облака, тени и неопределённость контуров полностью не исключены.

## Что проверено

До обучения зафиксированы девять вариантов: контроль только 2024; прежний простой пул; пул с фиксированным суммарным весом; ограничение старого архива 25%; относительная сезонная динамика; версия обработки; оба блока; перевзвешивание по сходству входов; согласие адаптации и контроля.

В новых взвешенных моделях ранние даты получают суммарно 25%, текущие известные даты 2024 — 75%. Суммарный эффективный вес ограничен числом обучающих пар 2024, чтобы дополнительный объём не менял силу регуляризации незаметно. Отдельный контроль фиксированного веса позволяет отличать это от ограничения доли источника.

Фиксация суммарного веса контролирует масштаб взвешенной функции потерь. У ExtraTrees минимальный лист по-прежнему задаётся числом строк, а не суммой весов: полная эквивалентность сложности на разных объёмах данных не заявляется.

Сезонные признаки — амплитуда наблюдавшейся к дате истории, относительный предыдущий темп и отклонение от наблюдавшегося максимума, флаги достаточности истории. История только прошлая, того же года и PB. Это не известная культура, не реальная фаза BBCH и не будущий максимум сезона.

Версия представлена категориальными признаками. Для неизвестной версии предусмотрено заранее фиксированное усреднение по поддержанным версиям 2024 с весами из обучающих дат. Это статистическая гипотеза переноса, не проверенная радиометрическая гармонизация. Согласие двух моделей означает минимум их оценок. Оценки не объявляются откалиброванными агрономическими вероятностями.

Для перевзвешивания сходства отдельная логистическая модель различает старые и известные текущие входы. Она не использует метки снижения и признаки проверяемого месяца; отношения весов ограничены [0,1; 10], затем применяется общий предел 25%. Такое перевзвешивание не исправляет произвольное изменение связи входов с целью.

## Выбор только по 2024

Те же 274 пары / 25 дат, июнь–октябрь. Обучение содержит только целевые даты строго раньше начала проверяемого месяца; пересекающие границу пары исключены. Все R23–R27 вне обучения, выбора, перевзвешивания и расчёта нормировок во всех годах. Порог события прежний: снижение медианы ядра минимум на 0,10 через 6–8 дней. Критерии: 0 ложных пар и дат, минимум 2 попадания на 2 датах; затем максимальная полнота с равным весом дат. Эти оценки использованы для выбора и оптимистичны.

| Индекс | Вариант | Результат отбора 2024 |
|---|---|---|
{chr(10).join(choice)}

Основной выбор: **{frozen_primary}**. Сезонный NDVI и согласие моделей сравнялись с контролем, поэтому при равенстве остался более простой контроль. Лучший адаптированный NDVI — `season25`, выбран до 2026 для отдельной заранее предусмотренной проверки. Для NDMI адаптация не прошла условия уже по 2024.

До теста зафиксированы только четыре оценки: основной выбор, лучший адаптированный вариант и два контроля. Другие варианты не проверялись по 2026 и не выбирались по его результатам. Новых порогов после проверки нет.

## Проверка на том же секторе 2026

499 одинаковых пар / 27 дат, выдачи с 28 июля, факты до 19 октября. 2026 уже изучался в предыдущих работах: это проверка замороженных вариантов без подбора, а не новый слепой тест. 2025 отсутствует в существующем наборе. Предсказанные события — спектральные снижения, не подтверждённый ущерб или необходимость обработки.

| Индекс | Вариант | Найдено / событий | Ложные пары | Дат с ложными | Полнота | Доля верных тревог |
|---|---|---:|---:|---:|---:|---:|
{chr(10).join(result)}

Сезонный NDVI уменьшил ложные тревоги 17→{ndvi['fp']}, но снизил попадания 12→{ndvi['tp']}. При сопоставлении конкретных сигналов убраны {transitions['fp']['removed']} прежних ложных, добавлены {transitions['fp']['added']} новых ложных и потеряны {transitions['tp']['removed']} верных попадания. Рабочий Ridge даёт 3 ложные, сохранение индекса — 0: заданный бюджет всё равно превышен. Поэтому это компромисс между чувствительностью и тревогами, а не достигнутая цель улучшения.

![Попадания и ложные тревоги](assets/farm-reference/season-version-decline.png)

| Проверка адаптированного NDVI | Пар | Найдено / событий | Ложные | Дат с ложными |
|---|---:|---:|---:|---:|
{chr(10).join(spatial)}

Соседние резервные фрагменты не являются независимыми хозяйствами. Выбирать только удачные места по 2026 нельзя. На 2026 встречаются ранее не поддержанные версии: {r['unknown_test_baselines']}. Версионные варианты не выиграли отбор по 2024 и на 2026 отдельно не испытывались; нельзя заявлять доказанное исправление этих версий. Выбранный сезонный кандидат не использует категориальный PB-блок.

## Решение и ограничения

**Новая модель в продукт не подключена.** Основной выбор и лучший адаптированный кандидат не удовлетворяют условию большего обнаружения без роста ложных тревог. Результат не доказывает, что сезонная адаптация или ранние годы вообще бесполезны: проверены конкретные способы переноса на ограниченной выборке.

Для проверки именно влияния версии нужны сравнимые продукты одной съёмки в разных PB либо независимый согласованный источник. Для агрономической полезности нужны фактические культуры, операции и результаты осмотров: текущее событие не различает нормальную уборку/созревание и проблему. Повторный подбор по уже просмотренному 2026 не выполняется; следующая проверка должна быть заранее выделена.

## Воспроизведение

`scripts/train_season_version_decline.py protocol|fit|evaluate`; `scripts/verify_season_version_decline.py`. После заморозки повторное обучение/оценка запрещены. Проверка воспроизвела выбор и четыре оценки на 499 парах, контрольные OOF совпали с предыдущим экспериментом с точностью 10⁻¹², проверены хронология, предел веса, отсутствие проверочных данных в доменной модели и неизменность {verify['protected_files']} прежних файлов. Прежний точечный прогноз, детектор, интервалы и журнал сохранены. Заморозка: `{r['frozen_sha256']}`.
'''
    doc=ROOT/'docs/SEASON_VERSION_DECLINE_REPORT.md';doc.write_text(text,encoding='utf-8')
    files=[doc,asset,diagnosis_asset,ROOT/'agro_checker/season_version_decline.py',
        ROOT/'tests/test_season_version_decline.py',ROOT/'scripts/train_season_version_decline.py',
        ROOT/'scripts/verify_season_version_decline.py',Path(__file__),*[q for q in D.iterdir() if q.is_file()]]
    bundle=ROOT/'outputs/season-version-decline-2026-10-21.zip'
    with zipfile.ZipFile(bundle,'w',zipfile.ZIP_DEFLATED) as z:
        for q in files:z.write(q,q.relative_to(ROOT).as_posix())
    dump(ROOT/'outputs/season-version-decline-2026-10-21/manifest.json',dict(archive=bundle.relative_to(ROOT).as_posix(),sha256=digest(bundle),
        files=[dict(path=q.relative_to(ROOT).as_posix(),sha256=digest(q)) for q in files],production_promoted=False))
    print(doc);print(bundle)
