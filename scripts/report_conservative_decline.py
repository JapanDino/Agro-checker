"""Create an honest, reproducible comparison; never select models here."""
import sys
from collections import Counter
from pathlib import Path
import json
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tmp/georef-deps')]
from agro_checker.farm_context_data import read, dump, digest
from agro_checker.conservative_decline import D, NAMES


def percent(v):
    return '—' if v is None else f'{100*v:.1f}%'


if __name__ == '__main__':
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np

    r = read(D/'report.json')
    f = read(D/'frozen-selection.json')
    p = read(D/'protocol.json')
    verification = read(D/'verification.json')
    previous = read(ROOT/'data/farm-research/field-decline-model/report.json')
    rows = read(D/'test-predictions.json')
    allrows = [v for v in rows if v['split'] == 'all2026']
    asset = ROOT/'docs/assets/farm-reference/conservative-decline.png'
    fig, axes = plt.subplots(2, 2, figsize=(11.5, 7), layout='constrained')
    colors = ['#b7c4bc', '#8a9fa7', '#b55d42']
    labels = ['Сохранить индекс', 'Прежний Ridge', 'Новый кандидат']
    for j, index in enumerate(NAMES):
        for col, (metric, title) in enumerate([('tp', 'Найденные снижения'), ('fp', 'Ложные сигналы')]):
            ax = axes[j, col]
            values = [r['results']['all2026'][index][k][metric] for k in ['persistence', 'legacy', 'new']]
            bars = ax.bar(np.arange(3), values, color=colors, width=.6)
            ax.bar_label(bars, padding=4)
            ax.set_xticks(np.arange(3), labels, fontsize=9)
            ax.set_title(index.upper()+' · '+title, fontsize=12)
            ax.set_ylim(0, max(values)+5)
            ax.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))
            ax.grid(axis='y', alpha=.2)
            ax.set_axisbelow(True)
            ax.spines[['top', 'right']].set_visible(False)
    fig.suptitle('Перенос на 2026: обнаружений больше, но ложные тревоги выросли', fontsize=14, fontweight='bold')
    fig.supxlabel('499 зависимых пар «фрагмент × дата», 27 дат. Порог события −0,10 через 6–8 дней. Выбор только по 2024.', fontsize=9)
    asset.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(asset, dpi=160, facecolor='white')
    plt.close(fig)

    oof = []
    for c in f['selected']:
        m = c['metrics']
        oof.append(f"| {c['index'].upper()} | {c['candidate']} | {c['threshold']} | {m['tp']} / {m['tp']+m['fn']} | {m['tp_dates']} | {m['fp']} |")
    main = []
    for index in NAMES:
        for label, source in [('Сохранить индекс', 'persistence'), ('Прежний Ridge', 'legacy'), ('Новый кандидат', 'new')]:
            m = r['results']['all2026'][index][source]
            main.append(f"| {index.upper()} | {label} | {m['tp']} / {m['tp']+m['fn']} | {m['fp']} | {m['fp_dates']} | {percent(m['recall'])} | {percent(m['precision'])} |")
        m = previous['results']['all2026'][index]['new']
        main.append(f"| {index.upper()} | Прежний отклонённый классификатор | {m['tp']} / {m['tp']+m['fn']} | {m['fp']} | {m['fp_dates']} | {percent(m['recall'])} | {percent(m['precision'])} |")
    spatial = []
    for split, title in [('spatial2024', 'Отложенные площади, 2024'), ('spatial2026', 'Отложенные площади, 2026'), ('temporal2026', 'Остальные площади, 2026')]:
        for index in NAMES:
            m = r['results'][split][index]
            a, b = m['legacy'], m['new']
            spatial.append(f"| {title} | {index.upper()} | {a['pairs']} | {a['tp']} → {b['tp']} из {b['tp']+b['fn']} | {a['fp']} → {b['fp']} | {b['fp_dates']} |")
    diagnosis = {}
    for j, index in enumerate(NAMES):
        false = [v for v in allrows if v['new_alarm'][j] and v['actual'][j]-v['persistence'][j] > -.10]
        changes = [v['actual'][j]-v['persistence'][j] for v in false]
        diagnosis[index] = dict(false_alarm_count=len(false), nonnegative_actual_change=sum(v >= 0 for v in changes),
            false_alarm_fragments=dict(Counter(v['fid'] for v in false)),
            actual_changes=changes, rule='Descriptive only; no new threshold, filter or target reinterpretation')
    dump(D/'descriptive-errors.json', diagnosis)
    combinations = len(read(D/'selection-table.json'))//2
    text = f'''# Консервативный детектор резких снижений NDVI/NDMI

Проверка 20 октября 2026. **Обнаружение улучшилось, но цель «без роста ложных тревог» на всём секторе не достигнута. Оба кандидата отклонены для подключения.** Прежние точечный прогноз, детектор, диапазоны, интерфейс и журнал сохранены.

## Постановка и данные

Предсказывается падение медианного индекса фиксированного внутреннего ядра не менее чем на 0,10 на ближайшем пригодном снимке через 6–8 дней. Это спектральное событие, не болезнь и не подтверждённый ущерб. Маски, исходные пары и приблизительные контуры не менялись для улучшения метрик.

Полный набор 2024: 573 пары. Пять южных фрагментов R23–R27 исключены из обучения и выбора; итоговое обучение — {f['fit_pairs']} пар на {f['fit_dates']} датах, последняя целевая дата {f['fit_max_target']}. Последовательный выбор: 274 пары на 25 датах, июнь–октябрь 2024. Обучение каждого месяца использует только целевые даты строго раньше его начала; пересекающие границу окна исключаются. Близкие площади одной даты находятся вместе. Даты имеют одинаковый суммарный вес.

2026 не участвовал в обучении, выборе признаков, моделей, условий или порогов. Выбор и веса зафиксированы до оценки. Этот архив уже изучался ранее; **новым слепым тестом его называть нельзя**. 2025 отсутствует в существующем спутниковом наборе и не проверялся.

## Что попробовали

До обучения зафиксированы 13 кандидатов: прошлый темп как простой контроль; логистическая регрессия с двумя уровнями регуляризации; ExtraTrees с двумя минимальными размерами листа; регрессия изменения CatBoost с квантилями 0,5 и 0,9. Для обучаемых семейств — семь прежних входов либо 40 входов с прошлыми пиксельными/сезонными признаками. Догадки о культуре, календарь будущих операций, ID поля, будущие снимки и целевые аудиты не являются признаками.

Проверены два заранее заданных условия сигнала: без ограничения либо совместное предыдущее снижение обоих индексов не менее 0,05 в пересчёте на неделю при текущем NDVI≥0,40 / NDMI≥0,00. Пороги заданы сеткой до обучения. Приоритет отбора — полнота с равным весом дат; жёсткий бюджет — **0 ложных пар и 0 дат с ложной тревогой**, как у сохранения индекса; минимум 2 попадания на 2 датах. Проверено {combinations} комбинаций на индекс. Отбор по этим же 25 датам оптимистичен и не является независимой проверкой. Числа выходов классификаторов не объявляются откалиброванными вероятностями; квантильная регрессия не даёт гарантии покрытия.

## Выбор только по 2024

| Индекс | Кандидат | Порог | Найдено / событий | Дат попаданий | Ложные |
|---|---|---:|---:|---:|---:|
{chr(10).join(oof)}

Оба выбранных варианта используют совместный прошлый отрицательный темп. Поэтому проверяется прежде всего прогноз **продолжения уже начавшегося снижения**, а не предупреждение до его начала. Финальные модели обучены на полном пригодном сезоне 2024 после выбора; численная шкала и перенос в другой сезон не гарантируются нулём ошибок на отборе.

## Перенос на весь сектор 2026

499 одинаковых пар «фрагмент × дата», 27 дат выдачи с 28 июля; факты до 19 октября. Полнота и точность тревог ниже рассчитаны по парам; они не совпадают с полнотой с равным весом дат, используемой при отборе. Событийные пары связаны общими снимками и пересекающимися окнами.

| Индекс | Вариант | Найдено / событий | Ложные пары | Дат с ложными | Полнота | Доля верных тревог |
|---|---|---:|---:|---:|---:|---:|
{chr(10).join(main)}

Сохранение индекса всегда предсказывает нулевое изменение, поэтому его нулевые ложные тревоги сопровождаются нулевыми попаданиями. Это прозрачный контроль, а не хороший детектор. Новый NDVI находит больше, чем прежний отклонённый классификатор, но также выдаёт больше ложных тревог. NDMI впервые находит часть событий в этом эксперименте, но на всём секторе также превышает бюджет 2→3.

С равным весом дат полнота новых сигналов: NDVI {percent(r['results']['all2026']['ndvi']['new']['date_balanced_recall'])}, NDMI {percent(r['results']['all2026']['ndmi']['new']['date_balanced_recall'])}. Точечный прогноз не менялся: MAE NDVI 0,0460 против сохранения 0,0441; NDMI 0,0445 против 0,0431. Нельзя приписывать новой классификации улучшение этих MAE.

![Попадания и ложные тревоги](assets/farm-reference/conservative-decline.png)

## Пространственная проверка

| Поднабор | Индекс | Пар | Попадания: прежний → новый | Ложные: прежний → новый | Дат с новыми ложными |
|---|---|---:|---:|---:|---:|
{chr(10).join(spatial)}

На пяти отложенных площадях NDMI в 2026 находит 3 из 15 событий без ложных тревог. Это ограниченный положительный результат. На остальных площадях у того же кандидата 0 попаданий и 3 ложные тревоги. **Выбирать только удачные фрагменты по 2026 или подключать модель на них нельзя.** Соседние фрагменты не являются независимыми хозяйствами. Хороший пространственный результат в 2024 также не подтвердил перенос во времени.

## Что выяснили об ошибках

Из 17 ложных NDVI-сигналов {diagnosis['ndvi']['nonnegative_actual_change']} сопровождались нулевым или положительным фактическим изменением. Все 3 ложные NDMI-тревоги сопровождались ростом индекса. Следовательно, проблему нельзя объяснить только пограничным случаем «снижение чуть меньше 0,10»: отрицательная прошлая динамика не всегда продолжается. Почему она разворачивается — развитие культуры, уборка, операции, состав пикселей или другие причины — без полевых данных не установлено. Пороги и метки после этого анализа не менялись.

## Решение и следующий шаг

Условие замены проверено одновременно относительно Ridge и сохранения индекса, по ложным парам и датам, на всём секторе и пространственном резерве. **Оба кандидата не прошли. Подключения к продукту нет.** Нельзя повышать порог по увиденным ошибкам 2026 и повторно называть этот же архив независимой проверкой.

Следующий содержательный эксперимент требует дополнительных обучающих сезонов с заранее выделенной новой проверкой, а также фактических культур и операций. Они позволят отличать продолжение снижения от нормального разворота или уборки. Результаты осмотров нужны отдельно, чтобы проверять полезность сигнала для агронома. Ни метки культур, ни осмотры в этой работе не выдумывались.

## Воспроизведение и контроль

Последовательность: `scripts/train_conservative_decline.py protocol`, затем `fit`, затем `evaluate`; после замораживания повторное обучение запрещено. Проверка `scripts/verify_conservative_decline.py` воспроизводит выбор из 2024, сверяет сроки, резерв, хеши, все 499 исходных прогнозов и тревог, неизменность {verification['protected_files']} прежних файлов. Исходный mean-прогноз, интервалы и журнал сохранены. Фиксация: `{r['frozen_sha256']}`.
'''
    doc = ROOT/'docs/CONSERVATIVE_DECLINE_REPORT.md'
    doc.write_text(text, encoding='utf-8')
    output = ROOT/'outputs/conservative-decline-2026-10-20'
    output.mkdir(parents=True, exist_ok=True)
    files = [doc, asset, ROOT/'agro_checker/conservative_decline.py',
        ROOT/'tests/test_conservative_decline.py', ROOT/'scripts/train_conservative_decline.py',
        ROOT/'scripts/verify_conservative_decline.py', Path(__file__),
        *[q for q in D.iterdir() if q.is_file()]]
    archive = ROOT/'outputs/conservative-decline-2026-10-20.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for q in files:
            z.write(q, q.relative_to(ROOT).as_posix())
    dump(output/'manifest.json', dict(archive=str(archive.relative_to(ROOT)), sha256=digest(archive),
        files=[dict(path=q.relative_to(ROOT).as_posix(), sha256=digest(q)) for q in files],
        production_promoted=False))
    print(doc)
    print(archive)
