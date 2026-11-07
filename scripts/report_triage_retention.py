"""Explain frozen2024 rescue experiment; never evaluates2025/2026."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tmp/georef-deps')]
from agro_checker import triage_retention as m
from agro_checker.farm_context_data import read,dump,digest


if __name__=='__main__':
    import numpy as np,matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from datetime import date
    p=read(m.D/'protocol.json');f=read(m.D/'frozen-selection.json');diag=read(m.D/'diagnostics.json')
    verification=read(m.D/'verification.json');table=read(m.D/'selection-table.json');meta=read(m.base.D/'meta-oof.json')
    rows=meta['rows'];records={r['method']:r['scores'] for r in meta['records']}
    # A development diagnostic, NOT a second acceptance rule or frozen winner.
    alternatives=[e for e in table if e['checks']['four_restored'] and e['checks']['all_baseline_events_retained']
        and e['checks']['total_events_not_below_extra'] and e['checks']['known_negatives_not_above_either']]
    example=min(alternatives,key=lambda e:(e['metrics']['fp'],p['variants'].index(e['variant']))) if alternatives else table[0]
    chosen=m.choose(rows,records['recent_drop'],records['extra'],example['variant'],p)
    selected_keys={m.base.key(r) for r,c in zip(rows,chosen) if c}
    y=m.base.labels(rows);old=m.base.topk(rows,records['recent_drop'],p);ml=m.base.topk(rows,records['extra'],p)
    swaps={}
    for name,reference in [('baseline',old),('extra',ml)]:
        swaps[name]={}
        for kind,mask in [('added',chosen&~reference),('removed',reference&~chosen)]:
            cases=[dict(fid=r['fid'],issue=r['issue'],outcome=label) for r,t,label in zip(rows,mask,y) if t]
            swaps[name][kind]=dict(cases=cases,tp=sum(c['outcome'] is True for c in cases),
                fp=sum(c['outcome'] is False for c in cases),unknown=sum(c['outcome'] is None for c in cases))
    evidence=dict(kind='2024_development_comparison_not_inspection_assignments',variant=example['variant'],
        frozen_selected_variant=f['selection']['variant'],accepted=example['eligible'],diagnostic_example_only=True,
        candidates=[dict(fid=r['fid'],issue=r['issue'],previous=r['previous'],outcome=label,
            baseline_selected=bool(a),extra_selected=bool(b),candidate_selected=bool(c),
            actual_human_visit=None) for r,label,a,b,c in zip(rows,y,old,ml,chosen)],
        swaps=swaps,frozen_sha256=digest(m.D/'frozen-selection.json'),test_years_evaluated=[],tasks_created=0)
    dump(m.D/'candidate-comparison-2024.json',evidence)
    assets=ROOT/'docs/assets/farm-reference';assets.mkdir(parents=True,exist_ok=True)
    colors=['#3978a3','#2b8b72'];fig,axes=plt.subplots(2,2,figsize=(12,8))
    fig.subplots_adjust(left=.08,right=.98,bottom=.15,top=.85,hspace=.60,wspace=.22)
    for ax,c in zip(axes.ravel(),diag['cases']):
        r=next(r for r in rows if m.base.key(r)==m.base.key(c))
        current=np.array(r['x'][:2]);past=current-np.array(r['x'][2:4])*r['x'][4]
        values=np.array([past,current,r['y']]);xx=[date.fromisoformat(d) for d in [r['previous'],r['issue'],r['target']]]
        for j,(label,color) in enumerate(zip(['NDVI','NDMI'],colors)):
            ax.plot(xx,values[:,j],marker='o',color=color,label=label,linewidth=1.5)
            ax.annotate(f'{c["future_delta"][j]:+.3f}',xy=(xx[-1],values[-1,j]),xytext=(-5,-16 if j else 9),
                textcoords='offset points',ha='right',fontsize=9,color=color)
        ax.axvline(xx[1],linestyle='--',color='#777777',linewidth=1)
        ax.set_xticks(xx,[d.strftime('%d.%m') for d in xx]);ax.set_ylim(-.3,.95)
        ax.set_title(f'{c["fid"]} · {c["issue"]}\nМесто: {c["baseline_rank"]} → {c["extra_rank"]}; гибрид вернул',fontsize=11)
        ax.set_ylabel('Значение индекса');ax.grid(axis='y',alpha=.15);ax.spines[['top','right']].set_visible(False)
    fig.suptitle('Четыре потери рейтинга: реальные архивные съёмки 2024',fontsize=15,fontweight='bold',y=.97)
    handles,labels=axes[0,0].get_legend_handles_labels();fig.legend(handles,labels,loc='lower center',bbox_to_anchor=(.5,.05),ncol=2,frameon=False)
    fig.text(.5,.015,'Пунктир — дата выбора. Числа справа — последующее изменение. Линии соединяют съёмки; операций и осмотров нет.',ha='center',fontsize=9)
    cases_asset=assets/'triage-retention-cases.png';fig.savefig(cases_asset,dpi=160,facecolor='white');plt.close(fig)
    show=[table[0],table[1],example];fig,ax=plt.subplots(figsize=(11,4.5));fig.subplots_adjust(left=.27,right=.98,bottom=.29,top=.76)
    totals=np.zeros(3);yy=np.arange(3)
    for key,label,color in [('tp','Дальнейшее снижение','#398571'),('fp','Снижения по порогу нет','#b57054'),('unknown','Исход неизвестен','#cbd0ce')]:
        counts=np.array([r['metrics'][key] for r in show]);ax.barh(yy,counts,left=totals,label=label,color=color)
        for i,(v,left) in enumerate(zip(counts,totals)):ax.text(left+v/2,i,str(v),ha='center',va='center',fontsize=12)
        totals+=counts
    ax.set_yticks(yy,['Простое правило','Исходный ExtraTrees','Гибрид · исследование']);ax.invert_yaxis()
    ax.spines[['top','right']].set_visible(False);ax.set_xlabel('Выбранных позиций · одинаковые 133 места')
    fig.suptitle('2024: четыре случая возвращены, неизвестность остаётся',fontsize=14,fontweight='bold',y=.96)
    fig.legend(*ax.get_legend_handles_labels(),loc='lower center',bbox_to_anchor=(.5,.08),ncol=3,frameon=False,fontsize=9)
    fig.text(.5,.025,'Гибрид не принят: общий выигрыш не подтверждён при неизвестных исходах. Проверки 2025–2026 не проводились.',ha='center',fontsize=9)
    comparison_asset=assets/'triage-retention-comparison.png';fig.savefig(comparison_asset,dpi=160,facecolor='white');plt.close(fig)
    cases=[]
    for c in diag['cases']:
        cases.append(f'| {c["fid"]} | {c["issue"]} → {c["target"]} | {c["future_delta"][0]:+.3f} | {c["future_delta"][1]:+.3f} | {c["baseline_rank"]} → {c["extra_rank"]} | {100*c["common_coverage"]:.1f}% |')
    variants=[]
    for e in table:
        z=e['metrics'];a=e['paired_baseline'];b=e['paired_extra']
        variants.append(f'| `{e["variant"]["id"]}` | {z["tp"]} | {z["fp"]} | {z["unknown"]} | {len(e["restored_cases"])}/4 | {a["lost_known_baseline_tp"]} | {a["negative_growth_upper"]:+d} / {b["negative_growth_upper"]:+d} |')
    bm=example['paired_baseline'];em=example['paired_extra'];mm=example['metrics'];doc=ROOT/'docs/TRIAGE_RETENTION_REPORT.md'
    text=f'''# Возврат четырёх потерь спектрального рейтинга

21 октября 2026. **В исследовании 2024 гибрид вернул все четыре потерянных случая, сохранил все 16 находок простого правила и дал 21 известное снижение при 32 отрицательных выборах.** Исходный ExtraTrees давал 21 / 33. Но гибрид заменил четыре другие находки ExtraTrees, а 80 из 133 исходов неизвестны. Улучшение всей очереди без роста ложных выборов не доказано; рабочие модели и интерфейс сохранены.

## Какие четыре случая потеряны

Это исходы на приблизительных фрагментах, а не подтверждённые повреждения/уборка/проведённые осмотры. Положительный исход — снижение любого индекса минимум на 0,10 на пригодном снимке через 6–8 дней.

| Фрагмент | Выбор → проверочная съёмка | ΔNDVI после выбора | ΔNDMI после выбора | Место: правило → ExtraTrees | Общие пиксели двух входных съёмок |
|---|---|---:|---:|---:|---:|
{chr(10).join(cases)}

Три случая относятся к одной дате 3 июня и четвёртый — к 11 июня; это не четыре независимых сезона/эпизода. Во всех четырёх снизились оба индекса. Даты/ID найдены в диагностике 2024 и **не входят** в правило восстановления.

![Четыре случая](assets/farm-reference/triage-retention-cases.png)

## Почему модель их опустила

Непосредственная причина — сортировка: на 3 июня оценки потерянных фрагментов около 0,067–0,077, тогда как три выбранных получили 0,081–0,087. У R13 11 июня было девятое место. Это выход классификатора, не проверенная вероятность повреждения или пользы выезда.

К июню обучающая часть содержала только **85 известных исходов на семи датах, четыре положительных примера на трёх датах**. Деревья глубины 3 с минимумом 15 примеров в листе сглаживают различия; требования сохранять порядок сильных уже наблюдаемых снижений у них нет. Это ограничение обучающего материала и устройства рейтинга, а не установленная агрономическая причина.

Проверена точная арифметическая декомпозиция путей всех 256 деревьев: прошлое снижение увеличивало оценку этих случаев, то есть сигнал не был полностью проигнорирован. Однако другие кандидаты получили более высокую итоговую оценку. Вклад признака в дереве не доказывает причинность и не объясняет, почему физически изменились растения.

Все четыре входные пары имели поддержку всех 11 сценариев границ, общие пригодные пиксели 85,3–100%. Знак уже видимого снижения сохранялся при сдвигах. При этом три июньских скорости пересчитаны с интервала всего **два дня** на недельный темп: это усиливает короткий скачок и не является измерением изменения ровно за неделю. У R13 прошлый интервал — пять дней. Границы по-прежнему не подтверждены.

## Почему нельзя просто объединить два списка

На 3 июня простое правило выбрало R02/R17/R21, ExtraTrees — R09/R13/R22. Все шесть имеют положительный дальнейший спектральный исход. При лимите **три позиции на дату** сохранить шесть отдельных идентичностей невозможно. Возврат старых случаев заменяет другие верные выборы; увеличение бюджета создало бы несопоставимую проверку.

На 11 июня у списков также разные известные находки — R13 и R12. Для всех четырёх восстановлений новый гибрид теряет четыре известные находки исходного ExtraTrees: R09/R13/R22 от 3 июня и R12 от 11 июня. Число 21 сохраняется, состав — меняется. Ни один фрагмент не объявлен вымышленным осмотром.

## Что попробовали

До расчёта результатов зафиксированы 11 вариантов, включая оба исходных способа:

- смешение процентильных рангов с долями ExtraTrees 0,25 / 0,50 / 0,75;
- сохранение одного или двух первых мест простого правила, остальные места — по ExtraTrees;
- защита уже выбранных простым правилом сильных сигналов с недельным темпом от 0,10 / 0,20; замена остальных мест только при преимуществе оценки ExtraTrees более 0 / 0,05.

Защита применяется только к исходным трём местам, не создаёт новые места и не использует будущую метку. Границы 0,10/0,20 — исследовательские пороги, не агрономические нормативы. Доля рангов — не смешение откалиброванных вероятностей.

Использованы те же 699 входных кандидатов на 46 датах, из них 274 с известной целью внутри проверочного месяца. Все варианты выбирают **133 позиции**. Неизвестные исходы и цели за границей месяца остаются в списках. Исходные месячные оценки ExtraTrees вне обучения переиспользованы без изменения: обучение по завершённым целям до начала месяца. Полный обучающий пул прежнего этапа — 489 известных пар вне пространственного резерва; новый финальный ExtraTrees в этом этапе не обучался. R23–R27 исключены из выбора и обучения.

Результаты уже разобранного 2024 использованы для разработки и выбора — это оптимистичная проверка, не независимое доказательство качества. Данные/исходы **2025–2026 не анализировались** в этом этапе. Никаких новых настроек исходного ExtraTrees или старых детекторов не меняли.

## Все варианты 2024

| Вариант | Снижение | Нет снижения по порогу | Неизвестно | Вернул четыре | Потерял других находок правила | Максимальный прирост отрицательных: против правила / ExtraTrees |
|---|---:|---:|---:|---:|---:|---:|
{chr(10).join(variants)}

Последняя колонка — неблагоприятные сценарии неизвестных исходов в несовпадающих частях списков; общие выборы сокращаются. Это не ожидаемое число ошибок и не доверительная граница.

![Сравнение](assets/farm-reference/triage-retention-comparison.png)

## Перспективный, но не принятый гибрид

`{example['variant']['id']}`: защищает исходные три сигнала с недельным темпом от 0,20; оставшиеся места может заменить более высокой оценкой ExtraTrees. Среди вариантов, сохранивших все четыре и все находки правила, он имеет наименьшее число **известных** отрицательных выборов при числе находок не ниже ExtraTrees. Это диагностический пример из таблицы, а не второй способ принятия модели.

На известных исходах:

- против правила: 16 → **21** событие, 38 → **32** отрицательных, 79 → **80** неизвестных; ни одной прежней известной находки правила не потеряно;
- против ExtraTrees: **21 → 21** событие, 33 → **32** отрицательных, 79 → **80** неизвестных; четыре исходные потери восстановлены ценой четырёх других положительных случаев.

Итоговые суммы скрывают обмены. Против правила добавлены 5 положительных / 9 отрицательных / 28 неизвестных позиций; убраны 0 / 15 / 27. Против ExtraTrees добавлены 4 / 2 / 13; убраны 4 / 3 / 12. Поэтому даже уменьшение итогового числа известных отрицательных не доказывает, что все новые выборы лучше.

При равном весе дат доля событий гибрида находится между **{100*mm['precision_lower']:.1f}% и {100*mm['precision_upper']:.1f}%** в зависимости от неизвестных исходов. Парная разница против правила допускает **{100*bm['difference_lower']:.1f}…+{100*bm['difference_upper']:.1f} п.п.**, против ExtraTrees — **{100*em['difference_lower']:.1f}…+{100*em['difference_upper']:.1f} п.п.** Неизвестные способны дать рост отрицательных выборов до {bm['negative_growth_upper']} / {em['negative_growth_upper']} соответственно. Границы пропусков не являются доверительными интервалами и не учитывают перенос между сезонами.

## Решение и следующий шаг

Условия принятия заданы до сравнения: вернуть все четыре, сохранить каждый прежний положительный случай правила, не снизить число известных событий ExtraTrees, не увеличить известные отрицательные относительно обоих способов, пройти неблагоприятные сценарии неизвестности относительно обоих и получить строго положительную нижнюю парную разницу против правила. **Ни один вариант не прошёл все условия.** Заморожено сохранение простого правила, без обещания улучшения. Никакие отвергнутые варианты не проверялись по 2025–2026 и не подключены к рабочему интерфейсу.

Есть проверяемый кандидат для следующей независимой проверки: он восстанавливает сильные сигналы на разработческом 2024 и не увеличивает известные ложные выборы. Для подтверждения нужно зафиксировать его до нового сезона/раунда и собирать одинаковую проверку выбранных и контрольных мест. Отсутствующий снимок или невыполненный осмотр останется неизвестным, не отрицательной меткой. Эти спутниковые цифры не заменяют реальные результаты осмотров и не доказывают экономию времени.

Результаты осмотров, операции и культуры не выдуманы. Задачи/визиты не создавались; точечные прогнозы, детекторы, диапазоны, UI и журнал прежние. Контуры остаются приблизительными.

## Воспроизведение

`scripts/train_triage_retention.py protocol|run|verify`, `scripts/report_triage_retention.py`. Повторный `run` после заморозки запрещён. Заморозка `{verification['frozen_sha256']}`.

Файлы `diagnostics.json`, `selection-table.json` и `candidate-comparison-2024.json` в `data/farm-research/triage-retention-2024` сохраняют случаи, неизменные входы, состав списков и все обмены. Последний файл — исследовательские прогнозы и спутниковые исходы, не импорт журнала.

Верификация воспроизвела оценки и диагностику, все 11 списков на тех же 699 кандидатах, точные четыре идентичности и 133 позиции; подмена будущих меток не меняет ранжирование. **{verification['protected_files']} прежних защищённых файлов неизменны. 156 целевых тестов прошли, включая 14 новых.** Тесты включают сохранение бюджета, неизвестные исходы, порядок при равных оценках, защиту сильных сигналов, запрет будущих годов/резервных фрагментов в выборе и запрет повторного подбора после заморозки.
'''
    doc.write_text(text,encoding='utf-8');print(doc);print(cases_asset);print(comparison_asset)
