"""Reporting only; frozen model selection cannot change from spatial results."""
from collections import Counter
from datetime import datetime
import gzip
from .pipeline import ROOT, UTC, write_json
from .product_data import sha
from . import vityazevo_split_data as data
from .vityazevo_split_study import selection, evaluate_anapa, CANDIDATES, LOCK


def legacy_comparison(nights):
    """Trace old ISD-Lite values against exact-time separated full-ISD rows."""
    annual = data.read(data.DIRECTORY/'anapa_synoptic/annual-observations.json')
    synoptic = {r['observed_at_utc']: r['temperature_c'] for r in annual if r['temperature_c'] is not None}
    result = {}
    for y in [2022, 2023, 2024]:
        path = ROOT/'data/processed/product-stations/raw'/f'370010-99999-{y}.gz'
        m = data.read(path.with_name(path.name+'.meta.json'))
        if sha(path) != m['sha256']: raise ValueError('Legacy raw bytes changed')
        old = {}
        for line in gzip.decompress(path.read_bytes()).decode('ascii').splitlines():
            r = line.split(); stamp = datetime(*map(int, r[:4]), tzinfo=UTC).isoformat(); v = int(r[4])
            old[stamp] = None if v == -9999 else v/10
        pairs = 0; mismatches = []; coverage_differences = []
        for n in nights:
            if n['year'] != y: continue
            for stamp, value in zip(n['times'], n['target_temperature_c']):
                v = old.get(stamp)
                if (v is None) != (value is None): coverage_differences.append(stamp)
                if v is not None and value is not None:
                    pairs += 1
                    if abs(v-value) > 1e-8:
                        mismatches.append({'observed_at_utc': stamp, 'old_isd_lite_c': v, 'airport_c': value, 'anapa_c': synoptic.get(stamp)})
        result[str(y)] = {'paired_slots': pairs, 'mismatches': mismatches, 'coverage_differences': coverage_differences,
                          'old_raw_sha256': sha(path), 'all_mismatches_match_other_position':
                          all(r['anapa_c'] is not None and abs(r['old_isd_lite_c']-r['anapa_c']) < 1e-8 for r in mismatches)}
    write_json(data.DIRECTORY/'legacy-lite-comparison.json', result)
    return result


def plot(s, a):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10})
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.8))
    for ax, source, key, title in [(axes[0], s['validation'], 'all_slots', 'Витязево · все часы'),
                                    (axes[1], s['validation'], 'cold_slots', 'Витязево · наблюдённое T < 0°C'),
                                    (axes[2], a['scores'], 'all_slots', 'Анапа · перенос без подстройки')]:
        for i, (name, label, color) in enumerate([('gfs', 'GFS', '#93a9b3'), (s['selected'], 'Коррекция', '#19765b')]):
            values = [source[name][str(y)][key]['mae_c'] for y in [2023, 2024]]
            bars = ax.bar(np.arange(2)+(i-.5)*.34, values, .34, color=color, label=label)
            ax.bar_label(bars, labels=[f'{v:.2f}' for v in values], padding=4)
        ax.set_xticks([0, 1], ['2023', '2024']); ax.set_title(title, fontsize=10, pad=14)
        ax.set_ylabel('Средняя абсолютная ошибка, °C'); ax.set_ylim(0, 8 if key == 'cold_slots' else 2.7)
        ax.spines[['top', 'right']].set_visible(False); ax.grid(axis='y', alpha=.15); ax.set_axisbelow(True)
    axes[0].legend(frameon=False)
    fig.suptitle('Две физические позиции · точное UTC · одна ячейка GFS', fontsize=14, y=.98)
    fig.text(.5, .02, 'Витязево: выбор по 2023–2024, не независимый тест · Анапа: всего 15 и 5 отсчётов, без наблюдённого холода\nМодель Витязево улучшилась локально; перенос на Анапу ухудшил температуру.', ha='center', fontsize=10)
    fig.tight_layout(rect=[0, .10, 1, .92]); path = ROOT/'docs/assets/vityazevo-split-quality.png'
    path.parent.mkdir(parents=True, exist_ok=True); fig.savefig(path, dpi=160, facecolor='white'); plt.close(fig)


def report():
    s = selection(); spatial = evaluate_anapa(); nights, m = data.load('development'); old = legacy_comparison(nights)
    selected = s['selected']; chosen = s['validation'][selected]; base = s['validation']['gfs']
    availability = []; validation = []; cold = []; external = []; registry = []; legacy_rows = []
    for y in ['2022', '2023', '2024']:
        v = m['statistics']['vityazevo_airport'][y]; a = m['statistics']['anapa_synoptic'][y]
        availability.append(f"|{y}|{v['annual_reports']} / {a['annual_reports']}|{v['spring_reports']} / {a['spring_reports']}|{v['observed_exact_slots']} / {a['observed_exact_slots']}|{v['complete_nights']} / {a['complete_nights']}|")
        mismatches = old[y]['mismatches']; maximum = max((abs(r['old_isd_lite_c']-r['airport_c']) for r in mismatches), default=0)
        legacy_rows.append(f"|{y}|{old[y]['paired_slots']}|{len(mismatches)}|{maximum:.1f}|{'да' if old[y]['all_mismatches_match_other_position'] else 'нет'}|")
    for y in ['2023', '2024']:
        b, c = base[y], chosen[y]; bn, cn = b['complete_14_slot_nights'], c['complete_14_slot_nights']
        validation.append(f"|{y}|{c['all_slots']['n']}|{b['all_slots']['mae_c']:.3f} → {c['all_slots']['mae_c']:.3f}|{100*(1-c['all_slots']['mae_c']/b['all_slots']['mae_c']):.1f}%|{bn['minimum_error']['mae_c']:.3f} → {cn['minimum_error']['mae_c']:.3f}|")
        cold.append(f"|{y}|{c['cold_slots']['n']}|{b['cold_slots']['mae_c']:.3f} → {c['cold_slots']['mae_c']:.3f}|{bn['hits']}/{bn['cold_nights']} → {cn['hits']}/{cn['cold_nights']}|{bn['false_cold']} → {cn['false_cold']}|")
        ab, ac = spatial['scores']['gfs'][y], spatial['scores'][selected][y]
        external.append(f"|{y}|{ac['all_slots']['n']}|{ab['all_slots']['mae_c']:.3f} → {ac['all_slots']['mae_c']:.3f}|{ab['all_slots']['bias_c']:+.3f} → {ac['all_slots']['bias_c']:+.3f}|0|")
    for name in CANDIDATES:
        v = s['validation'][name]; gate = 'база' if name == 'gfs' else 'прошёл' if s['gates'][name]['eligible'] else 'не прошёл'
        registry.append(f"|{name}|{v['2023']['all_slots']['mae_c']:.3f}|{v['2024']['all_slots']['mae_c']:.3f}|{gate}|")
    result = {'selection': s, 'selection_sha256': sha(LOCK), 'dataset': m, 'anapa_check': spatial,
              'legacy_isd_lite_comparison': old, 'is_orchard_validation': False, 'original_detector_preserved': True}
    write_json(ROOT/'docs/VITYAZEVO_SPLIT_SUMMARY.json', result)
    ci = s['paired_mae'][selected]['interval_95_c']
    text = f'''# Витязево и Анапа: очищенный архив и проверка модели

**17 октября 2026.** Собраны полные годовые NOAA ISD 2022–2024 и разделены две физические позиции. На Витязево температурная коррекция улучшила обе последовательные prior-проверки. **Перенос на Анапу ухудшил температуру**, поэтому результат не является общей моделью района или проверкой сада. Исходный детектор, точечная модель, диапазон и полевой маршрут сохранены.

## Источники и две позиции

Исходные полные файлы: [2022](https://www.ncei.noaa.gov/pub/data/noaa/2022/370010-99999-2022.gz), [2023](https://www.ncei.noaa.gov/pub/data/noaa/2023/370010-99999-2023.gz), [2024](https://www.ncei.noaa.gov/pub/data/noaa/2024/370010-99999-2024.gz). Сохранены оригинальные gzip-байты, URL, SHA и время скачивания.2024 читается из предыдущего неизменённого кэша;2022–2023 скачаны дополнительно. Полные файлы содержат17522,17403 и17693 сообщения соответственно, всего52618.

| Позиция | Координаты | Высота в сообщении | Тип сообщений весной | Роль |
|---|---|---:|---|---|
| Витязево, аэропорт |45.002,37.347|53м|FM-15|Обучение и выбор настроек|
| Анапа, синоптическая позиция |44.883,37.283|32м|FM-12|Только проверка переноса после фиксации выбора|

Названия помогают читать отчёт; разделение определяется точными координатами и высотой каждой записи, а не одним индексом370010. Неизвестные позиции карантинируются; здесь их0. Противоречивые дубли внутри одной позиции исключаются; здесь их0. Совпадающее время двух разных позиций не является дублем одной станции. Полные нормализованные годовые ряды сохранены отдельно для каждого пункта; исходные файлы со всеми прочими полями также сохранены.

По [спецификации NOAA](https://www.ncei.noaa.gov/pub/data/noaa/isd-format-document.pdf), T хранится в десятых°C, +9999 означает отсутствие. Для первичных целей использованы толькоQC1/5 — прошедшие все проверки. Два сомнительных аэропортовых сообщенияQC2 весен2022/2024 и отсутствующее сообщение АнапыQC9 не допускаются; исходные значения/флаги остаются в годовом архиве. **Смысл этих флагов отличается отQC Росгидромета1/2, обозначающих восстановление.**

Все пригодные аэропортовые T весен этого среза имеют целые значения°C. Десятичная шкала хранения не означает инструментальную точность0,1°C. Это точность переданных сообщений; дополнительная высокая точность из необязательных блоков не восстанавливалась.

## Полнота для ночной проверки

Архивы годовые; модель проверяется только в поддерживаемой весенней задаче. Окно ночи16:00–05:00UTC следующего дня, то есть19:00–08:00МСК. По92 ночи и1288 ожидаемых точных часовых сроков на каждый пункт и год. Включены нужные утренние сроки1июня. На30-й минуте записи не округляются к часу, пропуски не заполняются.

В каждой ячейке ниже первым указан Витязево, вторым — Анапа.

| Год | Все годовые сообщения | Весенние сообщения | Пригодные точные ночные часы | Полные14-часовые ночи |
|---|---:|---:|---:|---:|
{chr(10).join(availability)}

Неполные ночи остаются в почасовой оценке по имеющимся срокам. Минимум14 часовых значений и температурные пересечения нуля оцениваются только на полностью наблюдённых ночах. Это не непрерывный минимум между наблюдениями, температура цветка или длительность реального заморозка.

## Проблема старого объединённого ISD-Lite подтверждена

Сопоставлены старыеISD-Lite цели370010 и очищенные аэропортовые значения по тем же ночнымUTC-часам. Полнота совпала, **значения совпали не везде**:

| Год | Парные часы | Расхождения | Максимальная разность,°C | Все отличающиеся старыеT совпадают с другой позицией Анапы |
|---|---:|---:|---:|---|
{chr(10).join(legacy_rows)}

Пример:7апреля2024 00UTC старыйISD-Lite даёт10,2°C; аэропортовая запись —4,0°C; другая позиция Анапы —10,2°C. Это конкретный пример подстановки географически другой серии в объединённый ряд, а не просто неодинакового каталога. Число полных ночей само по себе эту проблему не обнаруживает. Списки расхождений иSHA старых источников сохранены в сводке. Старые наборы не переписаны, в новом обучении используются только раздельные full-ISD цели.

Прежние региональные исследования, включавшие370010, нельзя автоматически считать проверкой чистого аэропортового ряда. Для их исправления нужен отдельный версионированный пересчёт с сохранением прежних результатов. Исходный краснодарский детектор здесь не переобучается.

## GFS и протокол выбора

По обеим позициям заново извлечён **один исходный выпуск06UTC**, leads10–23, T/RH/Td/u10/v10/облачность. Замороженный декодер проверяет поля, единицы, время, битовую маску, SHA и принадлежность одному объекту. Обе точки попадают в ячейку45.00,37.25:7,63км от Витязево и13,27км от Анапы. Прогнозные кривые у них одинаковые. Время сценария15UTC, доступностьLastModified+10мин — прокси, не доказанная первая историческая публикация.

Реестр7 вариантов и правила допуска сохранены **до расчёта ошибок** в `config/vityazevo_split.json`. Проверка2023: обучение толькоВитязево2022; проверка2024: обучение толькоВитязево2022–2023. Стандартизация признаков обучается на тех же prior-строках. Используются прогнозная температура, форма ночной кривой, сезон, срок; погодный вариант добавляет Td-дефицит,RH, ветер, облачность и заранее заданные взаимодействия. Цели, флаги фактического холода и координатный идентификатор в признаки не входят.

Для допуска нужны ≥5% снижения почасовойMAE в каждом году, без ухудшения cold-slotMAE иMAE14-точечного минимума, без дополнительных пропусков и ложных холодных полных ночей. Среди допустимых — минимум среднейMAE двух лет с равным весом. Выбран **`{selected}`**. Финальный исследовательский артефакт — fit2022–2023 из второй проверки; повторногоfit по2024 нет.

После записи и проверкиSHA выбора открыта отдельная цельАнапы. Она не участвовала в обучении/выборе. Архив уже частично рассматривался при поиске данных; ни эта проверка, ни2023–2024 не объявляются новым слепым тестом. Данные2025–2026 этим экспериментом не скачивались, не использовались и не оценивались.

## Витязево: результат

| Проверка | Парные часы | ПочасоваяMAE GFS → модель,°C | Снижение | MAE минимума полных ночей,°C |
|---|---:|---:|---:|---:|
{chr(10).join(validation)}

| Проверка | Холодные отсчётыT<0 | ИхMAE GFS → модель,°C | Холодные полные ночи с прогнозомT<0 | Ложные холодные полные ночи |
|---|---:|---:|---:|---:|
{chr(10).join(cold)}

**Это пересечения нуля температурной кривой, не предупреждения исходного детектора.** В каждой проверке осталась одна пропущенная полная холодная ночь. Среди полных ночей всего2 холодных в2023 и3 в2024: мало для обещания надёжного обнаружения. ХолоднаяMAE после коррекции остаётся около3,3–3,6°C.

В2023 ещё две ночи с наблюдённым холодом неполные и не входят в счёт1/2. По всем имеющимся срокам холод наблюдался в4ночах: GFS пересёк ноль в0/4, модель —2/4. Это дополнительная диагностика на одинаково наблюдённых сроках; она не восстанавливает истинный минимум пропущенных часов. В2024 все3ночи с наблюдённым холодом полные. Пропуски не скрываются числом полных ночей.

Описательный парный bootstrap по7-дневным блокам,2000повторов, равный вес лет: разницаMAE «модель−GFS»95% от{ci[0]:.3f} до{ci[1]:.3f}°C.28 календарных блоков. Интервал посчитан на тех же сезонах выбора, без поправки на выбор победителя, поэтому не является независимым подтверждением. Новый вероятностный диапазон ошибки не обучался.

![Качество и перенос](assets/vityazevo-split-quality.png)

## Анапа: перенос ухудшил результат

| Год | Наблюдённые часы | MAE GFS → модель,°C | Bias GFS → модель,°C | Полные ночи |
|---|---:|---:|---:|---:|
{chr(10).join(external)}

Bias=прогноз−наблюдение. Поправка Витязево сделала прогноз Анапы слишком холодным. Всего20парных ночных значений за две проверочные весны; ни одного наблюдённого отрицательного значения и ни одной полной ночи. **Надёжность обнаружения холода, минимум и длительность на Анапе не проверены.** Ноль пропусков при нуле наблюдённых холодных событий не считается успехом.

Поскольку входнойGFS и календарь совпадают, модель без местных признаков выдаёт одинаковуюT обоим пунктам. Это объясняет принципиальное ограничение переноса, но не доказывает конкретную физическую причину различий. Настройки после результатаАнапы не менялись. В сервис исследовательская поправка не подключена; прежний полевойGFS сохранён.

## Весь зарегистрированный реестр

| Вариант |MAE2023|MAE2024|Допуск|
|---|---:|---:|---|
{chr(10).join(registry)}

ПогоднаяRidge1000 лучше в2023, но хужеRidge100 в2024; победитель выбран по заранее заданному среднему двух лет. Простые общие и почасовые сдвиги не прошли правило≥5% в каждом году. Полные причины допуска/отклонения и ошибки холодных сроков сохранены в [численной сводке](VITYAZEVO_SPLIT_SUMMARY.json).

## Вывод для продукта и продолжение

Можно показать: исправленную идентичность источников, воспроизводимое улучшение локальной температуры и минимума в Витязево, а также честный отрицательный результат переноса на Анапу. Нельзя обещать такую ошибку на ферме45.024106,37.911914, гарантировать отсутствие заморозка или запускать защиту по одной этой модели.

Следующий приоритет — местные записи нашего агронома и дополнительные независимые температурные ряды с проверенной географией. Затем можно отдельно исследовать географические/климатические признаки или условную локальную калибровку. Старый диапазон и региональные банки с объединённым370010 требуют отдельной новой версии, а не подмены исходных данных после контроля2025–2026.

## Воспроизведение и сохранность

```powershell
.\\.venv\\Scripts\\python.exe -m agro_checker.vityazevo_split_data
.\\.venv\\Scripts\\python.exe -m agro_checker.vityazevo_split_study
.\\.venv\\Scripts\\python.exe -m agro_checker.vityazevo_split_report
```

Из локального кэша, без сетевого сбора контрольных лет. Научные модули `vityazevo_split_data.py`, `vityazevo_split_study.py`, протокол и артефакты **заморожены**; повтор проверяетSHA и возвращает тот же выбор, изменение требует нового эксперимента. Reporting-only `vityazevo_split_report.py` отделён от выбора. Кэш/артефакты `data/working-zone-01/vityazevo-split/` не публикуются вGit; сырые2024читаются из прежнего regional-sources/raw.

SHA выбора: `{sha(LOCK)}`. Исходный detectorSHA `fba66530ceca0a4baee688eeb96df88b98b5b3c36150fef3575d96c03828eab8` проверен вместе с прежнимиpoint/range и prior-коррекциейКрымска. Полевые выдачи, решения и профиль не изменены.
'''
    (ROOT/'docs/VITYAZEVO_SPLIT_REPORT.md').write_text(text, encoding='utf-8')
    plot(s, spatial)
    print('Split-position study report ready; selection remains', sha(LOCK))


if __name__ == '__main__': report()
