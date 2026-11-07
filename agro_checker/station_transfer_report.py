"""Read-only reporting over the frozen station-transfer experiment."""
from collections import defaultdict
import math
import re
from .pipeline import ROOT, write_json
from .product_data import sha
from . import station_transfer_study as study
from . import vityazevo_split_data as data


def plot(selection, diagnosis):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10})
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.2))
    ax = axes[0]; labels = []; values = [[], []]
    for y in ['2023', '2024']:
        for sid, short in [('vityazevo_airport', 'Аэропорт'), ('anapa_synoptic', 'Анапа')]:
            labels.append(short+'\n'+y)
            for j, name in enumerate(['gfs', 'naive_reference']):
                values[j].append(diagnosis[y]['matched_scores'][sid][name]['bias_c'])
    for j, label, color in [(0, 'GFS', '#91a8b5'), (1, 'Поправка аэропорта', '#b65550')]:
        bars = ax.bar(np.arange(4)+(j-.5)*.35, values[j], .35, color=color, label=label)
        ax.bar_label(bars, labels=[f'{v:+.2f}' for v in values[j]], padding=4, fontsize=9)
    ax.axhline(0, color='#405568', lw=.8); ax.set_xticks(range(4), labels)
    ax.set_ylabel('Средняя ошибка: прогноз − факт, °C'); ax.set_ylim(-2.1, 1.7)
    ax.set_title('Разные местные ошибки в одинаковые часы', pad=14)
    ax.legend(frameon=False, fontsize=9, loc='upper right')
    ax = axes[1]
    for j, name, label, color in [(0, 'naive_reference', 'Перенос поправки', '#b65550'),
                                  (1, 'identity_gfs', 'Выбран GFS для Анапы', '#16735a'),
                                  (2, 'local_bias_5', 'Местная калибровка · не принята', '#9bb8a1')]:
        vv = [selection['validation'][name]['anapa_synoptic'][y]['all_slots']['mae_c'] for y in ['2023', '2024']]
        bars = ax.bar(np.arange(2)+(j-1)*.24, vv, .24, color=color, label=label)
        ax.bar_label(bars, labels=[f'{v:.2f}' for v in vv], padding=4, fontsize=9)
    ax.set_xticks([0, 1], ['2023 · 15 отсчётов', '2024 · 5 отсчётов']); ax.set_ylim(0, 2.35)
    ax.set_ylabel('Средняя абсолютная ошибка, °C'); ax.set_title('Анапа: устранено ухудшение переноса', pad=14)
    ax.legend(frameon=False, fontsize=9, loc='upper left')
    for ax in axes:
        ax.spines[['top', 'right']].set_visible(False); ax.grid(axis='y', alpha=.15); ax.set_axisbelow(True)
    fig.suptitle('Одна ячейка GFS · разные площадки наблюдений', fontsize=14)
    fig.text(.5, .025, 'Выбор по 2023–2024 · обучение только на предыдущих сезонах · 2025–2026 не использованы\nВ Анапе нет наблюдённого холода и полных ночей. Улучшение относительно GFS не подтверждено.', ha='center', fontsize=10)
    fig.tight_layout(rect=[0, .12, 1, .94]); path = ROOT/'docs/assets/station-transfer-quality.png'
    path.parent.mkdir(parents=True, exist_ok=True); fig.savefig(path, dpi=160, facecolor='white'); plt.close(fig)


def weather_diagnosis(diagnosis):
    """Descriptive only: these buckets do not choose any model or settings."""
    buckets = defaultdict(list)
    for y in ['2023', '2024']:
        for r in diagnosis[y]['paired_rows']:
            wind = math.hypot(r['gfs']['u10'], r['gfs']['v10']); cloud = r['gfs']['cloud']
            for key in [('wind_lt2' if wind < 2 else 'wind_ge2'), ('cloud_lt50' if cloud < 50 else 'cloud_ge50')]:
                buckets[key].append(r)
    return {key: {'n': len(rr), 'mean_anapa_minus_airport_c': sum(r['anapa_minus_airport_c'] for r in rr)/len(rr),
                  'gfs_mae_c': sum(abs(r['gfs_c']-r['anapa_c']) for r in rr)/len(rr),
                  'naive_mae_c': sum(abs(r['naive_prediction_c']-r['anapa_c']) for r in rr)/len(rr),
                  'used_for_selection': False} for key, rr in buckets.items()}


def report():
    s = study.selection(); diagnosis = data.read(study.DIRECTORY/'diagnosis-before-candidates.json')
    prior_spatial = data.read(data.DIRECTORY/'anapa-check.json')
    if prior_spatial['prediction_sha256'] != sha(data.DIRECTORY/'anapa-check-predictions.json'):
        raise ValueError('Prior Anapa predictions changed')
    weather = weather_diagnosis(diagnosis)
    summary = {'selection': s, 'selection_sha256': sha(study.LOCK), 'diagnosis': diagnosis,
               'weather_descriptive_only': weather, 'original_detector_preserved': True,
               'orchard_validation': False, 'unseen_station_validation': False}
    write_json(ROOT/'docs/STATION_TRANSFER_SUMMARY.json', summary); plot(s, diagnosis)
    registry = []; reference_rows = []; spatial_rows = []; matched_rows = []
    for name in study.CANDIDATES:
        m = s['validation'][name]['anapa_synoptic']; gate = s['gates'].get(name)
        status = 'выбран' if name == s['selected'] else ('не прошёл' if gate else 'сравнение')
        registry.append(f"|{name}|{m['2023']['all_slots']['mae_c']:.3f}|{m['2024']['all_slots']['mae_c']:.3f}|{m['2023']['calibrated_observed_slots']} / {m['2024']['calibrated_observed_slots']}|{status}|")
    for y in ['2023', '2024']:
        v = s['validation']; base = v['gfs']; old = v['naive_reference']; chosen = v[s['selected']]; local = v['local_bias_5']
        am = chosen['vityazevo_airport'][y]; ab = base['vityazevo_airport'][y]
        reference_rows.append(f"|{y}|{am['all_slots']['n']}|{ab['all_slots']['mae_c']:.3f} → {am['all_slots']['mae_c']:.3f}|{ab['cold_slots']['mae_c']:.3f} → {am['cold_slots']['mae_c']:.3f}|{ab['complete_14_slot_nights']['minimum_error']['mae_c']:.3f} → {am['complete_14_slot_nights']['minimum_error']['mae_c']:.3f}|")
        cm = chosen['anapa_synoptic'][y]; om = old['anapa_synoptic'][y]; lm = local['anapa_synoptic'][y]
        spatial_rows.append(f"|{y}|{cm['all_slots']['n']}|{om['all_slots']['mae_c']:.3f}|{cm['all_slots']['mae_c']:.3f}|{100*(1-cm['all_slots']['mae_c']/om['all_slots']['mae_c']):.1f}%|{lm['all_slots']['mae_c']:.3f}|")
        for sid, label in [('vityazevo_airport', 'Витязево'), ('anapa_synoptic', 'Анапа')]:
            m = diagnosis[y]['matched_scores'][sid]
            matched_rows.append(f"|{y}|{label}|{diagnosis[y]['paired_slots']}|{m['gfs']['bias_c']:+.3f}|{m['naive_reference']['bias_c']:+.3f}|{m['gfs']['mae_c']:.3f} → {m['naive_reference']['mae_c']:.3f}|")
    interval = s['paired_blocks']['local_bias_5']['gfs']['interval_95_c']
    weather_labels = {'wind_lt2': 'Ветер < 2м/с', 'wind_ge2': 'Ветер ≥ 2м/с', 'cloud_lt50': 'Облачность < 50%', 'cloud_ge50': 'Облачность ≥ 50%'}
    weather_rows = [f"|{weather_labels[key]}|{m['n']}|{m['mean_anapa_minus_airport_c']:+.2f}|{m['gfs_mae_c']:.2f} → {m['naive_mae_c']:.2f}|" for key, m in weather.items()]
    text = f"""# Перенос коррекции Витязево → Анапа

Отдельный эксперимент, 17 октября 2026 года. Выбор: **сохранить прежнюю поправку в Витязево, для другой физической позиции использовать GFS** (`{s['selected']}`). Ухудшение от переноса устранено. Убедительного выигрыша местной калибровки относительно GFS пока нет. Сервис и исходный детектор не изменялись.

![Причина ухудшения и результат](assets/station-transfer-quality.png)

## Почему прежняя коррекция ухудшает Анапу

Это две позиции: аэропорт45.002,37.347,53м и синоптическая точка44.883,37.283,32м. У них одна ближайшая ячейка GFS45.0,37.25 и одинаковые модельные входы. Прежняя регрессия не получает местных признаков и предсказывает одинаковую температуру для обеих точек. Одинаковый код исходного архива не означает одинаковую площадку наблюдения.

На одинаковых UTC-сроках Анапа теплее аэропорта в среднем на1.15°C в2022 (8пар),1.42°C в2023 (15пар),1.44°C в2024 (5пар). Поэтому перенос аэропортового охлаждения даёт отрицательное смещение в Анапе:

|Год|Физическая точка|Общих сроков|Смещение GFS, °C|Смещение коррекции, °C|MAE GFS → коррекция, °C|
|---|---|---:|---:|---:|---:|
{chr(10).join(matched_rows)}

Поправка в среднем составляет−1.129°C в2023 и−1.133°C в2024 на этих общих сроках. Она помогает аэропорту, где GFS теплее измерений, но охлаждает уже слегка заниженный GFS Анапы. Сравнение по одним датам и часам отделяет это различие от неравномерной полноты данных.

Разница не постоянна: в2024 от−1.9 до+6.2°C. Например,7апреля00UTC GFS10.19°C, аэропорт4.0°C, Анапа10.2°C, перенос даёт7.52°C. По GFS в этот час ветер1.38м/с, облачность6.1%. А16апреля03UTC при ветре10.90м/с и облачности100% аэропорт16.0°C, Анапа14.1°C: знак различия обратный. Это прогнозные погодные поля, не показания ветромера на участке.

Описательные срезы уже после выбора, **не использованные для подбора**:

|Срез GFS|Часов|Анапа − аэропорт, °C|MAE Анапы GFS → перенос, °C|
|---|---:|---:|---:|
{chr(10).join(weather_rows)}

Эти редкие сроки не доказывают причинную роль моря, высоты или рельефа. Высота отличается лишь на21м; данных двух площадок недостаточно, чтобы обучить надёжный закон поправки по высоте/берегу. Дополнительно различаются типы сообщений: аэропортFM-15 с целыми°C, АнапаFM-12 с десятыми. Измеренное различие включает микроклимат, площадку и особенности наблюдений. В2023–2024 у Анапы15 из20 отсчётов приходятся на03UTC, остальные5 на00UTC; каждую из20 ночей представляет только один срок. По ним нельзя восстановить минимум всей ночи.

## Что проверено

Использован чистый разделённый [архив2022–2024](VITYAZEVO_SPLIT_REPORT.md). Новых загрузок не понадобилось. Наблюдения с QC1/5, точные UTC, без интерполяции и смешения площадок. Старые ISD-Lite данные с подменой позиции в обучение не попали.

Реестр9 вариантов и правила сохранены до расчёта их ошибок в [протоколе](../config/station_transfer.json) и `protocol-before-fit.json`. Варианты: GFS, прямой перенос, отключение переноса на другую позицию и три местных поправки с двумя величинами сглаживания: ошибка GFS на площадке; разность одновременных показаний двух площадок; остаточная ошибка регрессии аэропорта на местных измерениях.

Для местной поправки нужны минимум3 разные прошлые ночи на **том же сроке**, прогнозная температура должна лежать внутри прошлого местного диапазона. Поправка сглаживается множителем n/(n+5) или n/(n+20); при нехватке данных возвращается GFS с причиной. Это ограничитель экстраполяции, а не доказательство достаточности3 измерений. Новый участок без собственных прошлых измерений всегда получает GFS с отметкой отсутствия местной проверки.

Хронология: обучение2022 → проверка2023; обучение2022–2023 → проверка2024. Ни один прогноз не использует измерения своего сезона или текущей ночи. Нормализатор/погодная регрессия аэропорта взяты из соответствующего прежнего фолда, без повторного подбора. Итоговый исследовательский файл сохраняет обучение2022–2023, **без дообучения на2024**. Прогнозы аэропорта побитово совпадают с прежними на всех2576 часах двух проверочных сезонов.

Теперь Анапа — площадка разработки этого нового опыта: её старые измерения используются для местной калибровки, а2023–2024 — для выбора. **Это не новая независимая проверка переноса на неизвестную станцию.**2025–2026 не открывались, не оценивались и не участвовали в выборе этим экспериментом.

## Результаты Анапы и выбор

|Год|Измерений|Прямой перенос, MAE°C|Выбранный GFS, MAE°C|Снижение против переноса|Местная поправка `local_bias_5`, MAE°C|
|---|---:|---:|---:|---:|---:|
{chr(10).join(spatial_rows)}

Местная поправка даёт выигрыш относительно GFS лишь4.6% /3.5%: ниже заранее принятого порога5% **в каждом году**. Её описательный недельный bootstrap-интервал разницы MAE относительно GFS —[{interval[0]:+.3f};{interval[1]:+.3f}]°C, включает отсутствие выигрыша. Всего15 непустых календарных блоков,2000 повторов, равный вес годов; это иллюстрация неопределённости на годах выбора, не независимое подтверждение и не доверительный диапазон температуры.

Даже90-й перцентиль абсолютной ошибки2024 у этой поправки хуже GFS:2.754 против2.614°C. Поэтому не снижаем порог принятия после просмотра результата. Калибровка применялась к9 из15 проверочных часов2023 и4 из5 часов2024; остальные оставались GFS. В2023 обучение местной поправки03UTC —6 ночей2022; в2024 —18 ночей для03UTC и4 для00UTC. Другие сроки не получили достаточной поддержки.

|Вариант|MAE2023, °C|MAE2024, °C|Местно скорректированных часов2023 /2024|Решение|
|---|---:|---:|---:|---|
{chr(10).join(registry)}

Кандидат допускается, если в каждом году улучшает GFS Анапы минимум на5%, не увеличивает ложные/пропущенные пересечения0°C на наблюдённых сроках и точно сохраняет аэропортовые прогнозы. Ни одна местная поправка не прошла. Ноль наблюдённых холодных сроков означает **отсутствие проверки холода**, а не идеальный детектор. Все варианты имеют0 наблюдённых ложных пересечений в Анапе; между наблюдениями ложные тревоги не оценены.

## Витязево и сохранённые ограничения

|Год|Наблюдённых часов|MAE всех часов GFS → сохранённая поправка, °C|MAE холодных часов, °C|MAE минимума полных14-часовых ночей, °C|
|---|---:|---:|---:|---:|
{chr(10).join(reference_rows)}

Это прежние результаты, нового улучшения в аэропорту не заявляем. Холодная ошибка3.3–3.6°C остаётся большой; на5 полных холодных ночах2023–2024 поправка даёт3 обнаружения и2 пропуска пересечения0°C. Исходный продуктовый детектор с его порогом, вероятностями и временем эпизода сохранён и на эти площадки не переносился. Отдельные температурные пересечения его не заменяют.

В Анапе0 отрицательных наблюдений и0 полных14-часовых ночей. Начало, длительность, минимум, покрытие холодного диапазона ошибки и ущерб яблоне здесь не проверены. По ферме45.024106,37.911914 новых измерений нет. Выбранная стратегия — исследовательский результат, к полевому сервису не подключена; исходный прогноз фермы не изменён.

## Практический вывод для продукта и агронома

Подтверждённое улучшение — устранение ухудшения от чужой поправки, а не победа над GFS в новой локации. Совпадение ячейки GFS или кода архива больше не основание переносить калибровку. Выдача эксперимента содержит источник прогноза, причину возврата к GFS, наличие местной калибровки и явный признак отсутствия местной проверки холода. Нельзя обещать точность0.7°C или надёжность заморозков на участке по этим20 тёплым отсчётам.

Следующий содержательный шаг с агрономом — получить непрерывные местные ряды с координатами, высотой/размещением датчиков, точным временем и контролем качества, включая спокойные ясные холодные ночи, ветреные эпизоды и обычные тёплые ночи. По возможности нужны одновременные измерения в низине и обычной части сада: один датчик не описывает всю площадку. До получения данных не придумываем поправку на рельеф и не обещаем температуру цветков или эффективность защиты. Нужны дневник фенофазы, действий и повреждений, чтобы затем проверить полезность решений, а не только MAE.

Для следующего опыта заранее зафиксировать более строгую проверку: другая физическая станция полностью вне обучения/выбора и будущие сезоны после заморозки протокола. Раздельно оценивать режим без местных меток и калибровку на прошлых местных сезонах. Текущие2023–2024 остаются годами разработки, а ранее просмотренные2025–2026 нельзя объявить новым слепым тестом.

## Воспроизводимость

- Проверка 17 октября: **458 Python + 20 JavaScript = 478 тестов прошли**, без пропусков. Из них 13 новых тестов границ переноса/калибровки. Дополнительно подтверждены SHA прежних моделей и выборов, неизменность исходного полевого выпуска и отсутствие новых полевых измерений; график просмотрен.
- Модель: `agro_checker/station_transfer_study.py`; отчёт отдельно: `station_transfer_report.py`.
- Протокол: [config/station_transfer.json](../config/station_transfer.json).
- Итог со всеми метриками/парными сроками: [STATION_TRANSFER_SUMMARY.json](STATION_TRANSFER_SUMMARY.json).
- Кэш: `data/working-zone-01/station-transfer/`: протокол, диагноз до сравнения, модели фолдов, все прогнозы/причины и неизменяемый выбор.
- SHA выбора: `{sha(study.LOCK)}`. Родительский выбор: `{s['parent_selection_sha256']}`.
- Исходные данные/родительские выборы и артефакты защищены SHA; повтор запуска проверяет их и читает сохранённый выбор. Любой следующий поиск требует нового эксперимента.

Запуск из корня: `.venv\\Scripts\\python.exe -X utf8 -m agro_checker.station_transfer_report`.
"""
    text = text.replace('побитово совпадают', 'точно совпадают')
    text = re.sub(r'([А-Яа-яЁё])(?=\d)', r'\1 ', text)
    text = re.sub(r'(?<=\d)([А-Яа-яЁё])', r' \1', text)
    text = text.replace('составляет−', 'составляет −').replace('от−', 'от −').replace('Например,7', 'Например, 7')
    text = text.replace('аэропортFM', 'аэропорт FM').replace('АнапаFM', 'Анапа FM')
    path = ROOT/'docs/STATION_TRANSFER_REPORT.md'; path.write_text(text, encoding='utf-8'); return summary


if __name__ == '__main__':
    r = report(); print('Report ready; selected', r['selection']['selected'], r['selection_sha256'])
