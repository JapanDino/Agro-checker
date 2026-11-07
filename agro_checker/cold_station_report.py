"""Reporting/admission diagnosis only; frozen choices cannot change here."""
from collections import defaultdict
from .pipeline import ROOT, write_json
from .product_data import sha
from . import cold_station_data as data
from . import cold_station_study as study


def spatial_verdict(selection, spatial):
    out = {}
    for family, name in selection['selected'].items():
        reasons = []; years = {}
        for y in ['2023', '2024']:
            base, candidate = spatial['scores']['gfs'][y], spatial['scores'][name][y]
            years[y] = {'hourly_mae_gain_percent': 100*(1-candidate['site_macro_hourly_mae_c']/base['site_macro_hourly_mae_c']),
                        'false_cold_delta': candidate['false_cold']-base['false_cold'], 'miss_delta': candidate['misses']-base['misses']}
            if candidate['site_macro_hourly_mae_c'] >= base['site_macro_hourly_mae_c']: reasons.append(y+': no hourly improvement over GFS')
            if candidate['site_macro_cold_slot_mae_c'] > base['site_macro_cold_slot_mae_c']: reasons.append(y+': cold-hour error worsened')
            if candidate['misses'] > base['misses']: reasons.append(y+': additional cold-night misses')
            for sid, bm in base['by_site'].items():
                cm = candidate['by_site'][sid]
                if cm['complete_14_slot_nights']['false_cold'] > bm['complete_14_slot_nights']['false_cold']:
                    reasons.append(y+': additional false-cold nights '+sid)
        out[family] = {'selected': name, 'years': years, 'fulfills_accuracy_without_false_cold_growth': not reasons,
                       'reasons': reasons, 'production_admitted': False,
                       'selection_changed_after_spatial_check': False}
    return out


def new_false_cold_cases(predictions, name):
    grouped = {}
    for key in ['gfs', name]:
        by = defaultdict(list)
        for r in predictions[key]: by[(r['id'], r['date'])].append(r)
        grouped[key] = {key: rr for key, rr in by.items() if len(rr)==14 and all(r['target_temperature_c'] is not None for r in rr)}
    out = []
    for (sid, day), rr in grouped[name].items():
        actual = min(r['target_temperature_c'] for r in rr); forecast = min(r['prediction_c'] for r in rr)
        baseline = min(r['prediction_c'] for r in grouped['gfs'][(sid, day)])
        if actual >= 0 and forecast < 0 <= baseline:
            out.append({'id': sid, 'date': day, 'actual_14_term_min_c': actual,
                        'gfs_min_c': baseline, 'candidate_min_c': forecast,
                        'is_original_detector_alert': False})
    return out


def plot(selection, spatial):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10})
    fig, axes = plt.subplots(1, 3, figsize=(13, 5.2))
    local = selection['selected']['local_history']
    for ax, source, field, title in [(axes[0], selection['validation'], 'site_macro_hourly_mae_c', 'Разработка · 3 станции'),
                                    (axes[1], spatial['scores'], 'site_macro_hourly_mae_c', 'Отложенные · 2 станции'),
                                    (axes[2], spatial['scores'], 'false_cold', 'Ложные пересечения нуля\nна отложенных станциях')]:
        for i, (name, label, color) in enumerate([('gfs', 'GFS', '#8aa4b3'), (local, '25% местной поправки', '#247762')]):
            values = [source[name][y][field] for y in ['2023', '2024']]
            bars = ax.bar(np.arange(2)+(i-.5)*.34, values, .34, color=color, label=label)
            ax.bar_label(bars, labels=[str(v) if field=='false_cold' else f'{v:.2f}' for v in values], padding=4)
        ax.set_xticks([0, 1], ['2023', '2024']); ax.set_title(title, fontsize=11, pad=15)
        ax.set_ylabel('Число полных ночей' if field=='false_cold' else 'Средняя по станциям MAE, °C')
        ax.set_ylim(0, 5 if field=='false_cold' else 2.2)
        ax.spines[['top', 'right']].set_visible(False); ax.grid(axis='y', alpha=.15); ax.set_axisbelow(True)
    axes[0].legend(frameon=False, fontsize=9)
    fig.suptitle('Местная калибровка улучшила температуру, но условие по ложным тревогам не перенеслось', fontsize=12)
    fig.text(.5, .025, 'Выбор по 2023–2024 до оценки двух отложенных станций · обучение только на прошлых сезонах\nЭто температурные пересечения, не тревоги исходного детектора. Детектор и сервис сохранены.', ha='center', fontsize=10)
    fig.tight_layout(rect=[0, .12, 1, .94]); path = ROOT/'docs/assets/cold-station-transfer.png'
    path.parent.mkdir(parents=True, exist_ok=True); fig.savefig(path, dpi=160, facecolor='white'); plt.close(fig)


def report():
    s = study.selection(); a = study.evaluate_spatial(); m = data.read(data.DIRECTORY/'dataset.meta.json')
    verdict = spatial_verdict(s, a); local = s['selected']['local_history']
    predictions = data.read(data.DIRECTORY/'spatial-predictions.json'); cases = new_false_cold_cases(predictions, local)
    raw_rows = 0; quantization = {}
    for site in m['sites']:
        selected = data.read(data.DIRECTORY/site['id']/'annual-observations.json')
        other = data.read(data.DIRECTORY/site['id']/'other-positions.json'); raw_rows += len(selected)+len(other)
        valid = [r['temperature_c'] for r in selected if r['temperature_c'] is not None and r['observed_at_utc'][5:7] in ['03','04','05']]
        quantization[site['id']] = {'accepted_spring_values': len(valid), 'non_integer_values': sum(abs(v-round(v))>1e-8 for v in valid)}
    summary = {'selection': s, 'selection_sha256': sha(study.LOCK), 'dataset': m, 'spatial': a,
               'spatial_sha256': sha(data.DIRECTORY/'spatial-check.json'), 'spatial_verdict': verdict,
               'new_false_cold_cases': cases, 'raw_annual_records': raw_rows, 'quantization': quantization,
               'original_detector_preserved': True, 'farm_validated': False, 'production_admitted': False}
    write_json(ROOT/'docs/COLD_STATION_TRANSFER_SUMMARY.json', summary); plot(s, a)
    roster = []; quality = []; external = []; individual = []; registry = []; case_rows = []
    for site in m['sites']:
        sid = site['id']; st = m['statistics'][sid]
        roster.append(f"|{site['name_ru']}|{site['latitude']}, {site['longitude']} / {site['elevation_m']}м|{'выбор' if site['role']=='development' else 'отложенная проверка'}|"+
                      ' / '.join(str(st[y]['complete_nights']) for y in ['2022', '2023', '2024'])+'|'+
                      ' / '.join(str(st[y]['cold_complete_nights']) for y in ['2022', '2023', '2024'])+'|')
    for year in ['2023', '2024']:
        for collection, source in [(quality, s['validation']), (external, a['scores'])]:
            b, c = source['gfs'][year], source[local][year]
            collection.append(f"|{year}|{b['complete_nights']} / {b['cold_complete_nights']}|{b['site_macro_hourly_mae_c']:.3f} → {c['site_macro_hourly_mae_c']:.3f}|{b['site_macro_cold_slot_mae_c']:.3f} → {c['site_macro_cold_slot_mae_c']:.3f}|{b['site_macro_minimum_mae_c']:.3f} → {c['site_macro_minimum_mae_c']:.3f}|{b['misses']} → {c['misses']}|{b['false_cold']} → {c['false_cold']}|")
        for site in m['sites']:
            if site['role'] != 'spatial_check': continue
            sid = site['id']; b = a['scores']['gfs'][year]['by_site'][sid]; c = a['scores'][local][year]['by_site'][sid]
            bn, cn = b['complete_14_slot_nights'], c['complete_14_slot_nights']
            individual.append(f"|{site['name_ru']}|{year}|{b['all_slots']['mae_c']:.3f} → {c['all_slots']['mae_c']:.3f}|{b['cold_slots']['mae_c']:.3f} → {c['cold_slots']['mae_c']:.3f}|{bn['hits']}/{bn['cold_nights']} → {cn['hits']}/{cn['cold_nights']}|{bn['false_cold']} → {cn['false_cold']}|")
    for name in study.CANDIDATES:
        v = s['validation'][name]; gate = s['gates'].get(name)
        status = 'выбран' if name in s['selected'].values() else 'не прошёл' if gate else 'сравнение'
        registry.append(f"|{name}|{v['2023']['site_macro_hourly_mae_c']:.3f} / {v['2024']['site_macro_hourly_mae_c']:.3f}|{v['2023']['site_macro_cold_slot_mae_c']:.3f} / {v['2024']['site_macro_cold_slot_mae_c']:.3f}|{v['2023']['false_cold']} / {v['2024']['false_cold']}|{status}|")
    names = {site['id']: site['name_ru'] for site in m['sites']}
    for c in cases:
        case_rows.append(f"|{names[c['id']]}|{c['date']}|{c['actual_14_term_min_c']:.2f}|{c['gfs_min_c']:+.3f}|{c['candidate_min_c']:+.3f}|")
    ci = a['paired_blocks']['local_history']['gfs']['interval_95_c']
    raw_bytes = sum(v['bytes'] for v in m['sources'].values())
    text = f"""# Перенос на станции с холодными ночами

17 октября 2026 года. **Температуру удалось улучшить, но перенос без роста ложных пересечений нуля пока не подтверждён.** На трёх станциях разработки прошла местная поправка с весом 25%; на двух отложенных станциях она добавила по одной ложной холодной ночи в каждом году. Исследовательский выбор сохранён, к продукту не допущен. Исходный детектор и сервис не изменялись.

![Результат проверки](assets/cold-station-transfer.png)

## Данные и разделение

Дополнительно скачаны **15 полных годовых NOAA ISD файлов за 2022–2024**, {raw_rows} сообщений, {raw_bytes} байт gzip. Это не прежние ISD-Lite: сохранены исходный срок до минуты, QC температуры, тип сообщения, координаты, высота, номер строки и оригинальные байты с URL/SHA/датой получения. Принимается только NOAA QC1/5; QC2/9 не превращаются в измерения. Полные файлы содержат другие физические позиции — они сохранены отдельно, ни одна не подмешана к аэропортовой серии.

Площадки зафиксированы до чтения новых наблюдений по прежнему реестру, географии и доступности. Выбирается одна преобладающая координатная группа около позиции реестра по числу сообщений, без использования холода/ошибок. Отличия высоты реестра до 0.3м соответствуют целочисленной высоте ISD. Прогноз GFS и HGT из старого проверенного архива используются только после подтверждения той же ближайшей ячейки 0.25°. Новых погодных выпусков или подмены целей реанализом нет.

|Станция|Координаты / высота|Роль|Полных 14-часовых ночей 2022 / 2023 / 2024|Холодных полных ночей 2022 / 2023 / 2024|
|---|---|---|---:|---:|
{chr(10).join(roster)}

Всего 1219 полных станция-ночей за три весны; из них 175 холодных. В годах проверки 2023–2024 — 787 полных ночей, 69 холодных. На двух отложенных площадках — 314 полных ночей, 22 холодные; это станция-ночи, а не 22 независимые погодные волны. Все обычные тёплые ночи сохранены для оценки ложных пересечений. Кроме полных ночей, доступны 18640 точных часовых целей за три весны; пропуски не заполнялись, минуты не округлялись. Полнота и QC по годам — в JSON-сводке.

Прежние данные, включая смешанный код 370010, не переписывались. Витязево и Анапа не входят в новый целевой набор. Модель Витязево остаётся лишь отдельным диагностическим примером прямого переноса.

## Честная проверка двух режимов

1. **Без местных измерений:** общая погодная регрессия обучается на других станциях. В проверке каждой из трёх станций разработки её данные исключены из обучения целиком, включая предыдущие годы. Отдельный вариант добавляет высоту и разницу высоты станции с поверхностью GFS, взаимодействия с ветром и ясностью неба.
2. **С местной историей:** модель использует только прошлые сезоны той же физической станции. Проверены средняя ошибка по сроку, погодные регрессии и поправки с весом 25% / 50%. Это местная адаптация, не перенос на станцию без измерений.

Хронология обоих режимов: **обучение 2022 → проверка 2023; обучение 2022–2023 → проверка 2024**. Масштабирование признаков обучается только на прошлых данных. Станции и ночи сбалансированы по весу. Для местной модели нужно минимум 20 разных прошлых ночей; это технический минимум поддержки, не гарантия качества. Исходные погодные признаки взяты из одного GFS06UTC, горизонты 10–23 часа, сценарий выдачи 15UTC. Архивная доступность проверена прежним LastModified+10 минут; историческая первая публикация не доказана.

Два выбора сохраняются с SHA **до оценки Шпаковского и Минеральных Вод**. Их ошибки не участвуют в настройке. Для проверки режима без меток их данные отсутствуют в обучении; для местной адаптации разрешены только их прошлые годы, и это явно помечено. Эти станции раньше уже изучались в других экспериментах: новая очистка источника не делает проверку новым слепым тестом.

2025–2026 не открывались и не оценивались этим опытом. Настройки выбирались только по 2023–2024; ни один кандидат не дообучен на 2024. Прежние модели/детектор/интервалы/выборы сохранены.

## Выбор на трёх станциях разработки

Реестр 10 вариантов сохранён до расчёта ошибок в [протоколе](../config/cold_station_transfer.json). Кандидат должен в каждом году улучшить среднюю по станциям часовую MAE минимум на 5%, не ухудшить холодную MAE, минимум полной ночи и суммарные пропуски. Ложные холодные полные ночи не должны вырасти **ни на одной отдельной станции в отдельном году**. Общим снижением нельзя скрыть ухудшение одной площадки.

Выбор без местных меток — **GFS**. Общие погодные модели снижают ошибку, но увеличивают ложные пересечения. Добавление высоты без достаточного разнообразия станций особенно нестабильно: при исключении одной площадки остаются всего две для обучения зависимости от рельефа, а при переносе приходится экстраполировать. Этот вариант отклонён, физическая универсальная поправка по высоте не заявляется.

Выбор с местной историей — **`{local}`**: GFS + 25% местной погодной поправки Ridge1000. Более сильные поправки тоже снижают MAE, но не проходят ограничение по ложным пересечениям.

|Год|Полных / холодных ночей|Часовая MAE GFS → кандидат, °C|Холодная часовая MAE, °C|MAE минимума, °C|Пропуски|Ложные холодные ночи|
|---|---:|---:|---:|---:|---:|---:|
{chr(10).join(quality)}

Средние MAE здесь имеют равный вес станций, а не число доступных часов. Выигрыш по общей температуре 7.5% / 10.1%. Число ложных ночей 2 / 1 сохранено на каждой площадке, пропуски сократились 9→8 и 10→6.

|Вариант|Часовая MAE 2023 / 2024, °C|Холодная MAE 2023 / 2024, °C|Ложные ночи 2023 / 2024|Выбор|
|---|---:|---:|---:|---|
{chr(10).join(registry)}

Полные причины отклонения каждого варианта сохранены в JSON. Результат разработки не считается доказательством качества на новых площадках.

## Что произошло на двух отложенных станциях

Фиксированный кандидат с местной историей, без изменения веса 25% после проверки:

|Год|Полных / холодных ночей|Часовая MAE GFS → кандидат, °C|Холодная часовая MAE, °C|MAE минимума, °C|Пропуски|Ложные холодные ночи|
|---|---:|---:|---:|---:|---:|---:|
{chr(10).join(external)}

Температура улучшилась на 4.0% / 4.8%; холодная MAE — на 5.4% / 7.1%. Но ложные пересечения выросли 1→2 и 3→4, а пропуски сократились только в 2023. На 22 полных холодных станция-ночах найдено 10 против 9 у GFS; осталось 12 пропусков против 13. Такой результат не позволяет обещать надёжное обнаружение холода.

|Станция|Год|Часовая MAE GFS → кандидат, °C|Холодная MAE, °C|Найдено холодных GFS → кандидат|Ложные ночи|
|---|---|---:|---:|---:|---:|
{chr(10).join(individual)}

Описательный парный bootstrap недель по тем же датам всех станций даёт диапазон разницы часовой MAE [{ci[0]:+.3f}; {ci[1]:+.3f}]°C относительно GFS, 28 непустых календарных блоков, 2000 повторов, равный вес годов. Здесь часы объединены, поэтому это не та же величина, что равновесная станционная метрика выбора. Диапазон поддерживает небольшой выигрыш температуры, но не доказывает отсутствие роста ложных тревог, независимость станций или качество на ферме.

Новые ложные случаи:

|Станция|Дата ночи|Минимум измеренных 14 часов, °C|Минимум GFS, °C|Минимум кандидата, °C|
|---|---|---:|---:|---:|
{chr(10).join(case_rows)}

Наблюдения основных площадок приходят из FM-15; проверка их дискретности сохранена отдельно в сводке. Зарегистрированные 0°C не означают известный физический минимум с точностью сотой градуса. Однако задним числом округлять −0.06/−0.08°C к нулю, менять порог или удалять эти случаи нельзя: это изменило бы условие проверки после результата. На текущих зарегистрированных данных оба считаются дополнительными ложными температурными пересечениями.

На неполных ночах ситуация тоже не скрыта: по минимуму только доступных сроков на двух станциях GFS имеет 29 холодных станция-ночей и 15 пропусков, кандидат — те же 29 и 13 пропусков; ложных ночей 4→6. Это отдельная частичная цель, не доказательство минимума между наблюдениями. Подробности доступны в `available_term_sampled_minima`.

**Условие «точнее без роста ложных тревог» на отложенных площадках не выполнено.** Ни вес, ни порог, ни выбор после этого не менялись. Исследовательский кандидат остаётся для дальнейшей проверки; производственный допуск не выдан. Для площадки без местных измерений исследование сохраняет GFS без чужой калибровки.

## Что это даёт продукту

Теперь есть проверка на действительно холодных полных ночах, с контролем качества и физической идентичности станций, с раздельной оценкой переноса без меток и местного обучения. Прямой перенос аэропортовой модели на эти станции заметно увеличивает ложные пересечения: на отложенных площадках 10 / 22 против GFS 1 / 3. Хорошая холодная MAE сама по себе не делает модель пригодной для предупреждений.

Исходный продуктовый детектор, его вероятности, порог, маски и времена эпизода сохранены. Все приведённые «ложные ночи» — диагностика минимума температуры <0°C, **не новые тревоги детектора**. Нельзя обещать температуру цветков яблони, повреждение урожая или время запуска защиты по этим станционным данным. Непрерывный минимум и длительность холода между измерениями не проверены; часовые серии не описывают весь сад.

Следующий полезный опыт: заранее зарегистрировать работу с пограничной температурой и её неопределённостью, а также проверку переноса вне диапазона высот/погодных признаков обучения. Нужны более точные местные датчики и независимые будущие холодные эпизоды; проверять отдельно стоимость лишнего выезда и пропуска, сохраняя чувствительность. Это план следующего опыта, а не изменение текущего порога. Рабочая точка фермы пока не имеет собственных целей и не получает обещаний качества по этим станциям.

## Воспроизводимость и ограничения

- Проверка 17 октября: **470 Python + 20 JavaScript = 490 тестов прошли, без пропусков**. В том числе 12 новых проверок QC/позиции/LOSO/местного обучения/хронологии/пороговых ограничений. Дополнительно подтверждены SHA прежних моделей и выборов, неизменность полевого выпуска, отсутствие новых измерений фермы; график просмотрен и ссылки проверены.
- [JSON-сводка со всеми станциями, QC, ошибками и решениями](COLD_STATION_TRANSFER_SUMMARY.json).
- Данные: `agro_checker/cold_station_data.py`; модели: `cold_station_study.py`; отчёт отдельно: `cold_station_report.py`.
- Новый кэш `data/working-zone-01/cold-station-transfer/`: оригинальные gzip, раздельные annual/other-positions, цели, источник GFS/HGT, модели, прогнозы и протоколы с SHA.
- Выбор SHA `{sha(study.LOCK)}`; пространственная оценка SHA `{sha(data.DIRECTORY/'spatial-check.json')}`. Научные источники, протокол и кэш после фиксации неизменяемы. Последующий поиск — новым экспериментом.
- Источник: NOAA NCEI full ISD, например [Гумрак 2022](https://www.ncei.noaa.gov/pub/data/noaa/2022/345600-99999-2022.gz), [Шпаковское 2024](https://www.ncei.noaa.gov/pub/data/noaa/2024/349490-99999-2024.gz), [спецификация ISD](https://www.ncei.noaa.gov/pub/data/noaa/isd-format-document.pdf). Все 15 фактических URL, SHA и время получения — в JSON-сводке; это источник наблюдений станций, не измерения фермы.

Запуск из корня: `.venv\\Scripts\\python.exe -X utf8 -m agro_checker.cold_station_report`. Повтор проверяет зафиксированные результаты, не выбирает новый вариант по пространственным ошибкам.
"""
    (ROOT/'docs/COLD_STATION_TRANSFER_REPORT.md').write_text(text, encoding='utf-8'); return summary


if __name__ == '__main__':
    r = report(); print('Report ready', r['selection_sha256'], r['spatial_verdict'])
