"""Reporting and provenance only; never change the frozen prior experiment."""
from collections import Counter
from datetime import datetime
import json
from .pipeline import ROOT, UTC, write_json
from .product_data import sha
from . import krymsk_observed_data as data
from .krymsk_observed_study import run, LOCK, CANDIDATES

OFFICIAL_PASSPORT = 'https://www.meteorf.gov.ru/upload/iblock/58b/%D0%9F%D0%B0%D1%81%D0%BF%D0%BE%D1%80%D1%82%20%D0%93%D0%9C%D0%91%20%D0%9A%D1%80%D0%B0%D1%81%D0%BD%D0%BE%D0%B4%D0%B0%D1%80%D1%81%D0%BA%D0%BE%D0%B3%D0%BE%20%D0%BA%D1%80%D0%B0%D1%8F.pdf'


def provenance():
    snapshots = {}
    for name in ['provider-about.html', 'provider-faq.html', 'provider-agreement.html', 'provider-station.html']:
        path = data.DIRECTORY/'raw'/name; m = data.read(path.with_name(path.name+'.meta.json'))
        if sha(path) != m['sha256']: raise ValueError('Provenance evidence changed')
        snapshots[name] = m
    return {'snapshots': snapshots, 'official_station_metadata_source': OFFICIAL_PASSPORT,
            'station_identity': 'Roshydromet2017 passport confirms Krymsk37002, coarse44.90/38.00,35m; not a current instrument/relocation certificate',
            'provider_coordinates': [44.9109, 38.0053],
            'archive_origin_claim': 'Provider About: weather archives use international SYNOP and US METAR; no per-record transport/source lineage supplied',
            'processing_claim': 'Provider FAQ describes KN-01 encoding, Washington data centre, decoding, automated temperature QC and some manual corrections; general description, not independently verified per Krymsk record',
            'measurement_definition': 'Archive legend: air temperature at2m, UTC timestamps. Exact nominal observation time, not instrument acquisition-second precision',
            'original_synop_bulletins_verified': False, 'original_per_record_qc_verified': False,
            'station_relocation_and_instrument_history_verified': False,
            'published_tmin_window_verified': False, 'historical_revision_times_verified': False,
            'independent_same_time_temperature_match_available': False,
            'commercial_permission_obtained': False, 'production_admitted': False,
            'access': 'Only existing local2022-2024 observation cache used. No new archive cycles, account access, anti-bot bypass or messages to data holders.',
            'terms': {'url': snapshots['provider-agreement.html']['url'],
                      'summary': '3.1 personal noncommercial;3.5 prior written permission for commercial use;3.7 source attribution;3.3 not an official source for actions involving risk of material loss'},
            'official_export_status': 'EIP catalogue lists station-term data. This session direct HTTPS failed certificate validation and web fetch timed out; no official Krymsk observation export acquired. This does not prove absence.',
            'next_source_acceptance': ['Official/permitted export with station identity, UTC times, units, original QC and revision history',
                                       'Paired comparison against cached temperatures, disagreements quarantined rather than averaged',
                                       'Confirm extrema windows separately before any minimum/duration target',
                                       'Local orchard2m sensor observations required before field-quality claims'],
            'reviewed_at_utc': datetime.now(UTC).isoformat()}


def chart(r):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10})
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.7))
    for ax, kind, title in zip(axes, ['all_slots', 'cold_slots'], ['Все наблюдённые сроки', 'Только наблюдённая температура < 0°C']):
        for i, (name, label, color) in enumerate([('gfs', 'GFS', '#93a9b3'), (r['selected'], 'Коррекция', '#19765b')]):
            values = [r['validation'][name][str(y)][kind]['mae_c'] for y in [2023, 2024]]
            bars = ax.bar(np.arange(2)+(i-.5)*.34, values, .34, label=label, color=color)
            ax.bar_label(bars, labels=[f'{v:.2f}' for v in values], padding=3)
        ax.set_xticks([0, 1], ['2023', '2024']); ax.set_ylim(0, 4.6 if kind == 'cold_slots' else 2.2)
        ax.set_title(title, fontsize=11, pad=14); ax.set_ylabel('Средняя абсолютная ошибка, °C')
        ax.spines[['top', 'right']].set_visible(False); ax.grid(axis='y', alpha=.15); ax.set_axisbelow(True)
    axes[0].legend(frameon=False)
    fig.suptitle('Крымск37002 · температура в фактические сроки наблюдений', fontsize=14, y=.98)
    fig.text(.5, .015, '2023: 366 отсчётов, холодных9 · 2024: 368 отсчётов, холодных10\nВыбор по этим же сезонам; независимой проверки и полевой валидации нет.', ha='center', fontsize=10)
    fig.tight_layout(rect=[0, .09, 1, .92])
    path = ROOT/'docs/assets/krymsk-observed-time-quality.png'; path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160, facecolor='white'); plt.close(fig)


def report():
    r = run(); rows, _ = data.load(); origin = provenance()
    if any(sha(ROOT/path) != h for path, h in r['preserved_sha256'].items()):
        raise ValueError('Existing point/detector/range artifacts changed')
    selected = r['selected']; v = r['validation']; missing = [x['observed_at_utc'] for x in rows if x['target_temperature_c'] is None]
    table = []; cold = []; candidate = []; times = []
    for y in ['2023', '2024']:
        b, n = v['gfs'][y], v[selected][y]
        gain = 100*(1-n['all_slots']['mae_c']/b['all_slots']['mae_c'])
        table.append(f"|{y}|{n['all_slots']['n']}|{b['all_slots']['mae_c']:.3f} → {n['all_slots']['mae_c']:.3f}|{gain:.1f}%|{b['all_slots']['p90_absolute_error_c']:.3f} → {n['all_slots']['p90_absolute_error_c']:.3f}|")
        cold.append(f"|{y}|{n['cold_slots']['n']}|{b['cold_slots']['mae_c']:.3f} → {n['cold_slots']['mae_c']:.3f}|{b['sampled_night']['hits']}/{b['sampled_night']['cold_nights']} → {n['sampled_night']['hits']}/{n['sampled_night']['cold_nights']}|{b['sampled_night']['false_cold']} → {n['sampled_night']['false_cold']}|")
        for h, local in [('12', '21:00'), ('15', '00:00'), ('18', '03:00'), ('21', '06:00')]:
            a, z = b['by_lead'][h], n['by_lead'][h]
            times.append(f"|{y}|{local}|{z['n']}|{a['mae_c']:.3f} → {z['mae_c']:.3f}|{a['bias_c']:+.3f} → {z['bias_c']:+.3f}|")
    for name in CANDIDATES:
        a, b = v[name]['2023'], v[name]['2024']
        gate = 'база' if name == 'gfs' else 'прошёл' if r['gates'][name]['eligible'] else 'не прошёл'
        candidate.append(f"|{name}|{a['all_slots']['mae_c']:.3f}|{b['all_slots']['mae_c']:.3f}|{a['cold_slots']['mae_c']:.3f}|{b['cold_slots']['mae_c']:.3f}|{gate}|")
    interval = r['paired_mae'][selected]['descriptive_95_c']
    result = {'experiment': r, 'selection_sha256': sha(LOCK), 'provenance': origin,
              'missing_exact_times_utc': missing, 'year_counts': dict(Counter(x['year'] for x in rows)),
              'production_routing_modified': False}
    write_json(data.DIRECTORY/'provenance-review.json', origin)
    write_json(ROOT/'docs/KRYMSK_OBSERVED_TIME_SUMMARY.json', result)
    text = f'''# Крымск: происхождение данных и проверка по срокам наблюдений

17 октября2026. Отдельный исследовательский эксперимент; **не включён в полевой прогноз**. Выбран `{selected}`. Температура в проверках2023–2024 улучшилась; качество реального предупреждения на ферме этим не подтверждено. Исходные точечная модель, детектор, его порог/маски и диапазон сохранены.

## Что подтверждено о происхождении

Станция Крымск37002 присутствует в [официальном паспорте Росгидромета2017]({OFFICIAL_PASSPORT}):44.90,38.00,35м. [Провайдер](https://www.pogodaiklimat.ru/station/37002.htm) даёт более подробные44.9109,38.0053,35м. Это примерно14,58км от точки фермы. Текущая установка прибора и история переносов независимо не подтверждены.

[Описание источников](https://www.pogodaiklimat.ru/about.php) говорит об архивах на основе международного обменаSYNOP и американскихMETAR. [FAQ](https://www.pogodaiklimat.ru/faq.php) описывает цепочку КН-01 → центр данных в Вашингтоне → декодирование и обработка, автоматический контрольT и ручные исправления. Это заявление поставщика о своей системе: исходных телеграмм для каждой записи Крымска у нас нет. Архив может содержать поздние исправления, время которых неизвестно.

Легенда архивных таблиц определяетT как температуру воздуха на2м, время какUTC. Под «фактическим сроком» здесь понимается опубликованный номинальный срок; секундная точность снятия измерения не установлена. Модельные курсивныеT исключаются парсером; в данном срезе их0. Отсутствие такой пометки не заменяет первичныеQC-флаги. ОпубликованныеTmin не использованы: окно каждого экстремума пока не подтверждено. Описанный вFAQ суточный агрегат также не наша ночная цель.

Сохранены исходные страницы справки,URL, дата получения иSHA; каждая использованная страница наблюдений тоже проверена поSHA. [Машинная сводка](KRYMSK_OBSERVED_TIME_SUMMARY.json) разделяет заявления провайдера и независимо подтверждённые факты. Прямое получение официального каталогаЕИП в этой сессии завершилось ошибкой проверкиTLS, веб-запрос — тайм-аутом. Официального экспортаT Крымска пока нет; это не доказательство отсутствия данных.

У [источника](https://www.pogodaiklimat.ru/agreement.php) п3.1 задаёт личное некоммерческое использование, п3.5 требует предварительного письменного разрешения для коммерческого использования, п3.7 — атрибуцию, п3.3 ограничивает статус данных при решениях с риском материальных потерь. **Разрешения для продукта нет.** Использован только уже локальный исследовательский кэш; новые сезоны архива не собирались, сообщения держателям не отправлялись, исходные наблюдения не публикуются вGit.

## Данные и точное сопоставление

2022–2024, март–май, с нужными сроками1июня для последней ночи. **1102 допустимыхT из1104 ожидаемых**,276ночей,275полных по4срокам. Не хватает21UTC16апреля и03UTC17апреля2023; эти два срока пропущены в почасовой метрике, вся соответствующая ночь исключена из метрики4-точечного минимума. Остальные наблюдения ночи остаются допустимыми отдельнымиT.

| СрокUTC | СрокМСК | Заблаговременность от06UTC |
|---|---|---|
|18:00|21:00|12ч|
|21:00|00:00 следующего дня|15ч|
|00:00 следующего дня|03:00 следующего дня|18ч|
|03:00 следующего дня|06:00 следующего дня|21ч|

GFS извлечён заново **в координатах станции**, исходныеGRIB06UTC, по одному выпуску на ночь; егоT/RH/Td/ветер/облачность,14часов, все поля и временные признаки проходят прежние проверки. Ближайшая ячейка45.00,38.00,9,92км от станции. У фермы та же ячейка, но это не превращает станционнуюT в факт фермы. Время выдачи сценария15UTC; Last-Modified+10мин используется только как прокси доступности, первая историческая публикация не доказана. В признаки входят только прогнозы и календарь; измерения для входных признаков не используются.

Нет интерполяции, подстановки суточногоTmin, реанализа или измерений другой станции. Из четырёх отсчётов нельзя установить непрерывный минимум, точное начало или длительность заморозка; отсутствие наблюдённого холода не исключает холод между сроками.

## Протокол выбора

Реестр7вариантов и критерии зафиксированы в `config/krymsk_observed_time.json` до расчёта ошибок. Проверка2023: обучение2022; проверка2024: обучение2022–2023. Выбор по среднемуMAE двух сезонов с равным весом. Для допуска нужна экономияMAE≥5% в каждом году, без ухудшенияMAE на холодных сроках и без дополнительных ложных пересечений нуля на полных4-точечных ночах в каждом году. Иначе оставался быGFS.

Ridge корректирует остатокGFS; масштабирование признаков учится только на предыдущих сезонах. Признаки: температура и форма прогнозной ночи, срок/календарь; в погодном варианте дополнительно дефицит точки росы,RH, облачность, ветер и фиксированные взаимодействия. После выбора сохранён отдельный исследовательский fit2022–2024; приведённые метрики относятся к последовательным проверкам, **не к качеству этого финальногоfit в будущем**.

Данные2025–2026 не загружались и не использовались этим экспериментом.2023–2024 уже применены для выбора, поэтому это development-результат, не новый слепой тест. Объекты прежнего детектора на Крымске не вычислялись: его перенос на эту станцию пока не валидирован.

## Результат температуры

| Год проверки | Отсчёты | MAE GFS → коррекция,°C | СнижениеMAE |90-й процентиль абсолютной ошибки,°C |
|---|---:|---:|---:|---:|
{chr(10).join(table)}

![Ошибки температуры](assets/krymsk-observed-time-quality.png)

| Год | Холодные отсчётыT<0 | ИхMAE GFS → коррекция,°C | Холодные4-точечные ночи с прогнозомT<0 | Ложные холодные4-точечные ночи |
|---|---:|---:|---:|---:|
{chr(10).join(cold)}

Последние два столбца — **диагностика пересечений нуля температурой, не качество прежнего детектора**. В2023 осталось2пропуска из3холодных ночей; в2024 —2из4. Всего19холодных отсчётов в7ночах; они не независимые19событий. Ноль ложных пересечений на этом срезе не гарантирует их отсутствие в будущем.

Описательный парный bootstrap по7-дневным блокам, с отдельной выборкой внутри каждого года: разницаMAE «коррекция−GFS»95% от{interval[0]:.3f} до{interval[1]:.3f}°C.28блоков,2000повторов. Это проверка устойчивости на тех же development-сезонах, без поправки на выбор лучшей модели; независимого подтверждения или гарантии покрытия диапазона ошибки она не даёт.90-й процентиль ошибки в таблице — характеристика полученных ошибок, не готовый прогнозный диапазон.

### Ошибка по срокам

Bias=прогноз−наблюдение; положительный означает слишком тёплый прогноз.

| Год | МСК |N| MAE GFS → коррекция,°C | Bias GFS → коррекция,°C |
|---|---|---:|---:|---:|
{chr(10).join(times)}

На холодных отсчётах bias положительный и после коррекции: прогноз по-прежнему слишком тёплый. Поэтому среднее улучшение ещё не делает модель достаточным основанием для запуска защиты сада.

### Весь заранее заданный реестр

| Вариант |MAE2023|MAE2024|ColdMAE2023|ColdMAE2024|Допуск|
|---|---:|---:|---:|---:|---|
{chr(10).join(candidate)}

## Что можно заявить и следующий шаг

Можно показать воспроизводимое улучшение станционнойT в известных сроках на двух последовательных prior-проверках и честно показать оставшиеся промахи. Нельзя обещать такую же ошибку в саду, точную температуру цветка, надёжное обнаружение всех заморозков, их длительность, проценты ущерба или экономию урожая. Коррекция не подключена к интерфейсу решений.

Следующий приоритет: получить официальный либо согласованный экспорт срочныхT37002 сQC и историей станции; сопоставить одинаковыеUTC-сроки с нашим кэшем и изолировать расхождения. Затем независимые будущие наблюдения и датчик2м в хозяйстве. Для 2025–2026 заранее создать отдельный протокол проверки с этим уже зафиксированным вариантом; ни выбор, ни повторная калибровка по ним не допускаются. Более частый ряд нужен отдельно для начала/длительности холода.

## Воспроизведение и сохранность

Только локальный кэш, без изменения сервисов:

```powershell
.\\.venv\\Scripts\\python.exe -m agro_checker.krymsk_observed_data
.\\.venv\\Scripts\\python.exe -m agro_checker.krymsk_observed_study
.\\.venv\\Scripts\\python.exe -m agro_checker.krymsk_observed_report
```

Повтор проверяетSHA исходных данных, научных модулей, конфигурации, моделей и прогнозов. Изменённые источники требуют нового эксперимента; нельзя перезаписывать замороженный выбор. Данные и модель находятся в игнорируемомGit каталоге `data/working-zone-01/krymsk-observed-time/`; локальные сырые данные не становятся публичным датасетом.

SHA выбора: `{sha(LOCK)}`. Исходный детекторSHA `{r['preserved_sha256']['models/research-frost/event_duration_weather/model.json']}` сохранён; прежний point и range artifacts проверены тем же способом. Полевая выдача и записи решений не менялись.
'''
    (ROOT/'docs/KRYMSK_OBSERVED_TIME_REPORT.md').write_text(text, encoding='utf-8')
    chart(r)
    print('Krymsk observed-time report generated; frozen selection', sha(LOCK))


if __name__ == '__main__': report()
