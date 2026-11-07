"""Reporting only: reads frozen scores, never trains or selects science."""
import json
import numpy as np
from .pipeline import ROOT


def build():
    r = json.loads((ROOT/'docs/COLD_RELIABILITY_SUMMARY.json').read_text(encoding='utf-8'))
    s = r['selection']; fmt = lambda x: '—' if x is None else f'{x:.3f}'
    names = {'previous': 'Прежний диапазон', 'selected': 'Выбранный диапазон', 'diagnostic': 'Диагностический кандидат'}
    frames = json.loads((ROOT/'data/processed/cold-reliability/reference-control-forecasts.json').read_text(encoding='utf-8'))
    ood = {y: sum(f['extrapolation'] for f in frames if f['date'].startswith(y)) for y in ['2025', '2026']}
    lines = ['# Надёжность диапазона на холодных ночах и новых станциях', '',
             f"Выбор только по 2023–2024: `{s['winner']}`. Диагностический кандидат `{s['diagnostic']}` также зафиксирован до проверки. Исходный минимум Краснодара и детектор сохранены; на новых станциях точка — исходный GFS, детектор не перенесён. Полевой выпуск не изменён.", '',
             '## Что изменено', '',
             'Нижняя граница может расширяться по хвосту ошибок фактически холодных калибровочных ночей других станций. На входе прогнозирования используются только прогнозный минимум и высота станции; фактическая температура и метка холода входят только в калибровку. Для Huber и GFS используются отдельные ошибки соответствующей точки. Указанная квантиль хвоста не является обещанием покрытия холодных ночей или вероятностью ущерба.', '',
             'В prior-проверке исключается вся календарная неделя плюс два дня с обеих сторон на всех станциях. В региональном банке дополнительно исключается оцениваемая станция. Один максимум ошибки на дату заменяет много коррелированных станция-ночей при оценке хвоста. Недели и даты всё равно могут разделять одно похолодание: независимость не доказана. При недостатке дат конечная квантиль не подменяется максимумом; явно сохраняется прежняя граница.', '',
             'Проверены 16 вариантов расширения и база. Выбор требует отсутствия ухудшения покрытия в каждом prior-году и ограничивает среднюю ширину тёплых ночей 1.10×, всех ночей 1.15× базы. Эти ограничения относятся к prior-проверке, а не являются гарантией на новом климате.', '',
             '## Проверка 2023–2024 до новых данных', '',
             '| Год | Срез | Метод | Холодные покрыты | Нижние промахи | Ширина всех, °C | Холодных, °C | Без доступного хвоста |',
             '|---|---|---|---:|---:|---:|---:|---:|']
    for fold in s['folds']:
        for scope, title in [('primary', 'Краснодар'), ('regional', 'Другие development-станции')]:
            for label, name in [('previous', 'previous'), ('selected', s['winner'])]:
                a = fold['scores'][scope][name]
                lines.append(f"| {fold['year']} | {title} | {names[label]} | {a['cold_covered']}/{a['cold_nights']} | {a['lower_cold_misses']} | {fmt(a['mean_width_c'])} | {fmt(a['cold_mean_width_c'])} | {a['tail_unavailable_nights']} |")
    lines += ['', '## Новые станции: список до загрузки целей', '',
              'Восемь станций выбраны по ICAO, географии и периоду работы из метаданных NOAA; все исключены из прежних инвентаризаций и загруженных наблюдений. Из них пять имеют не менее 30 полных ночей в 2024 и 2025. Непригодные станции не заменялись. Для 2026 все восемь ISD-Lite URL вернули HTTP404: новых станционных метрик этого года нет. Краснодарский 2026 из прежнего отдельного источника сохранён.', '',
              '| Станция | Полных 2024 | Полных 2025 | 2026 |', '|---|---:|---:|---|']
    for site in r['new_station_audit']['stations']:
        lines.append(f"| {site['name']} · {site['id']} | {site['years']['2024']['complete_nights']} | {site['years']['2025']['complete_nights']} | {site['years']['2026'].get('error', 'нет полных ночей')} |")
    lines += ['', '2024 на новых станциях использует только банк ошибок 2023; 2025 — банк 2024. Их цели не использованы для выбора, калибровки или изменения точки. Однако prior-выбор видел 2024 других станций, а погода пространственно зависима. Новизна станции не превращает этот результат в независимый перспективный тест.', '',
              '## Контрольные результаты', '',
              'Ширина — полная верхняя граница минус нижняя, не ±указанное число. Региональная база — эмпирический диапазон ошибки GFS, построенный по development-станциям; GFS сам такого вероятностного диапазона здесь не выдаёт.', '',
              '| Срез | Метод | Полных ночей | Холодные покрыты | Нижние / верхние холодные промахи | Ширина всех, °C | Холодных, °C | Тёплых, °C | Холодный interval score90 |',
              '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for group, seasons in [('Новые станции', r['new_stations']), ('Краснодар', r['reference_controls'])]:
        for year, result in seasons.items():
            for label in ['previous', 'selected', 'diagnostic']:
                a = result['scores'][label]
                title = 'База диапазона GFS' if group == 'Новые станции' and label == 'previous' else names[label]
                lines.append(f"| {group} {year} | {title} | {a['nights']} | {a['cold_covered']}/{a['cold_nights']} | {a['lower_cold_misses']}/{a['upper_cold_misses']} | {fmt(a['mean_width_c'])} | {fmt(a['cold_mean_width_c'])} | {fmt(a['warm_mean_width_c'])} | {fmt(a['cold_interval_score'])} |")
    lines += ['', 'Interval score90 = ширина + 20×выход факта за границу; меньше лучше. Это сравнительный штраф, не доказанная вероятность. Диагностический кандидат не заменяет выбранный после удачного контроля.', '',
              'На новых сайтах2024 холодное покрытие72.4%→85.7% сопровождается ростом холодной ширины на63.1%, общей на24.8% и тёплой на11.5%. В2025 покрытие67.6%→98.6%, холодная ширина+46.9%, общая+17.3%, тёплая+11.3%. Перенос превышает prior-ограничения ширины: их нельзя выдавать за обещание продукта. Число диапазонов, пересекающих0°C, выросло74→86 и124→142; это дополнительные неоднозначные ночи, не новые тревоги детектора. В2024 остаются2 нижних и12 верхних холодных промахов. ВКраснодаре2026 покрытие не улучшилось, а ширина/штраф выросли: выигрыш не универсален.', '',
              '## Результаты по каждой новой станции', '',
              '| Год | Станция | Метод | Холодные покрыты | Нижние / верхние промахи | Ширина холодных, °C | Всех, °C |',
              '|---|---|---|---:|---:|---:|---:|']
    station_names = {q['id']: q['name'] for q in r['new_station_audit']['stations']}
    for year, result in r['new_stations'].items():
        for sid in result['scores']['previous']['by_site']:
            for label in ['previous', 'selected']:
                a = result['scores'][label]['by_site'][sid]
                lines.append(f"| {year} | {station_names[sid]} | {names[label]} | {a['cold_covered']}/{a['cold_nights']} | {a['lower_cold_misses']}/{a['upper_cold_misses']} | {fmt(a['cold_mean_width_c'])} | {fmt(a['mean_width_c'])} |")
    lines += ['', '## Объём и зависимость холодных данных', '',
              '| Срез | Холодных станция-ночей | Холодных дат | Групп эпизодов | Полностью покрытых групп, база → выбранный |',
              '|---|---:|---:|---:|---:|']
    for group, seasons in [('Новые станции', r['new_stations']), ('Краснодар', r['reference_controls'])]:
        for year, result in seasons.items():
            a = result['scores']['selected']; b = result['scores']['previous']
            lines.append(f"| {group} {year} | {a['cold_nights']} | {a['cold_dates']} | {a['cold_episode_groups']} | {b['entirely_covered_cold_episode_groups']} → {a['entirely_covered_cold_episode_groups']} |")
    lines += ['', 'Группы эпизодов — холодные даты с разрывом не более двух дней; это приближение, не независимые метеорологические события. Парная недельная описательная оценка изменений покрытия/ширины сохранена в JSON. Её интервал не является доказательством полевой надёжности.', '',
              '## Что можно обещать агроному', '',
              f"Численные исследовательские метрики не отфильтрованы по области исходного Huber. В reference-records сохранены признаки экстраполяции на {ood['2025']}/{ood['2026']} из92 прогнозных ночей2025/2026. Поле extrapolation_nights в scores новых диапазонов не вычисляет этот отдельный контроль области; его ноль нельзя читать как отсутствие OOD. Изменённый диапазон не обходил прежние полевые ограничения и в полевой выпуск не подключён.", '',
              'Показываем исходный прогноз, его эмпирический диапазон неопределённости и необходимость локальной проверки. Исходная выдача и решение сохраняются для последующего сравнения с фактами. Диапазон не меняет тревоги прежнего детектора; интервальные пересечения нуля не превращаются в новые сигналы.', '',
              'Нельзя обещать 90/95% покрытия холодных ночей на новом участке, отсутствие пропущенного холода, точное включение защиты или вероятность повреждения яблони. Проверка относится к минимуму 14 часовых измерений воздуха на станции, а не непрерывному минимуму цветка. ISD-Lite не содержит всех исходных подробных QC-флагов. Новые территории требуют перспективной проверки и локальных датчиков.', '',
              '## Неизменность и воспроизведение', '',
              f"Выбор зафиксирован: {s['selection_saved_at_utc']}. SHA256: `{r['selection_sha256']}`. Проверены все {r['original_detector_verified']} исходных объектов детектора: вероятность, событие, маска, начало и длительность совпадают. Точечный прогноз Краснодара совпадает с прежним архивом; новая станционная точка остаётся GFS. 2025–2026 исключены из выбора/калибровки; прежние контрольные годы уже просматривались и не названы слепыми.", '',
              '```powershell', '.\\.venv\\Scripts\\python.exe -m agro_checker.cold_reliability_study --select-only',
              '.\\.venv\\Scripts\\python.exe -m agro_checker.cold_reliability_archive --extract',
              '.\\.venv\\Scripts\\python.exe -m agro_checker.cold_reliability_study',
              '.\\.venv\\Scripts\\python.exe -m agro_checker.cold_reliability_report', '```', '',
              'При офлайн-повторе отсутствующие404-файлы нельзя скачать: используйте сохранённый аудит и извлечение, либо повторите онлайн-аудит доступности. Замороженный source/config нельзя менять после контроля; следующий эксперимент создаётся отдельно.', '',
              '![Покрытие и полная ширина диапазона](assets/cold-reliability-quality.png)', '']
    (ROOT/'docs/COLD_RELIABILITY_REPORT.md').write_text('\n'.join(lines), encoding='utf-8')
    plot(r)


def plot(r):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    groups = [(f'Новые {y}', s) for y, s in r['new_stations'].items() if s['scores']['previous']['cold_nights']]
    groups += [(f'Краснодар {y}', s) for y, s in r['reference_controls'].items() if s['scores']['previous']['cold_nights']]
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10})
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.3)); x = np.arange(len(groups)); width = .34
    for label, dx, color, title in [('previous', -width/2, '#87939c', 'База диапазона'), ('selected', width/2, '#246d87', 'Выбранный по 2023–2024')]:
        values = [s['scores'][label] for _, s in groups]
        bars = axes[0].bar(x+dx, [100*a['cold_coverage'] for a in values], width, color=color, label=title)
        for bar, a in zip(bars, values):
            axes[0].text(bar.get_x()+bar.get_width()/2, bar.get_height()+2, f"{a['cold_covered']}/{a['cold_nights']}", ha='center', fontsize=9)
        bars = axes[1].bar(x+dx, [a['cold_mean_width_c'] for a in values], width, color=color, label=title)
        for bar, a in zip(bars, values):
            axes[1].text(bar.get_x()+bar.get_width()/2, bar.get_height()+.13, f"{a['cold_mean_width_c']:.2f}", ha='center', fontsize=9)
    axes[0].set(title='Факт внутри диапазона на холодных ночах', ylabel='Покрытие, %', ylim=(0, 115))
    axes[1].set(title='Цена покрытия: полная ширина на холодных ночах', ylabel='Верхняя − нижняя граница, °C')
    axes[1].set_ylim(0, axes[1].get_ylim()[1]*1.12)
    for ax in axes:
        ax.set_xticks(x, [g for g, _ in groups]); ax.grid(axis='y', alpha=.2); ax.set_axisbelow(True)
    axes[0].legend(loc='upper left', bbox_to_anchor=(0, -.15), frameon=False, ncol=2)
    fig.suptitle('Точка и детектор сохранены; новые станции используют точку GFS', y=.98)
    fig.text(.5, .015, 'Архивная станционная проверка. Проценты не являются гарантией покрытия в саду.', ha='center', fontsize=9)
    fig.subplots_adjust(left=.07, right=.98, top=.83, bottom=.23, wspace=.24)
    path = ROOT/'docs/assets/cold-reliability-quality.png'; path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160); plt.close(fig)


if __name__ == '__main__':
    build()
