"""Reviewable study report and an isolated, offline research viewer; never a field-model promotion."""

import json
from pathlib import Path

from .pipeline import ROOT


def fmt(value, digits=3): return f"{value:.{digits}f}" if isinstance(value, (float, int)) else "—"


def write_report(s, directory):
    selected = s["selection"]; decision = selected["selected_decision_model"]
    lines = ["# Новые прогнозные цели и проверка моделей", "", "Дата: 13 октября 2026 года. Станция Краснодар 34929 / URKK; это не сад-партнёр.", "",
             "Улучшены непрерывные прогнозы температуры и влажности; добавлен прогноз скорости ветра и первичная проверка его единиц. "
             "Новый сценарный детектор не превзошёл прежний: его нельзя использовать как улучшенную замену предупреждений. "
             "Полевая выдача остаётся GFS, исследовательские коррекции не перенесены в неизвестный сад или на другие горизонты.", "",
             "## Протокол и модель", "",
             "Девять фиксированных вариантов: GFS, прежние почасовые коррекции, три сглаженных регрессии и четыре варианта ансамбля аналогов. "
             "Два последовательных разбиения: обучение 2022 → выбор 2023; обучение 2022–2023 → выбор 2024. "
             "Финальные модели обучены на 2022–2023. Год 2025 исключён из обучения/выбора и обозначен ранее просмотренным историческим контролем.", "",
             "Сглаженные регрессии корректируют остаток GFS с погодными признаками и гладкими взаимодействиями по часу ночи. "
             "Сценарии аналогов переносят целые совместные 14-часовые остатки T/RH/ветра похожих прогнозных ночей. "
             "Признаки только из общего выпуска GFS; scaler только из обучения. Целые сценарии сохраняют совместную временную структуру. "
             "Библиотека аналогов требует полных трёх целей; исключения и число ночей записаны, цели не заполняются.", "",
             "Точечные модели выбираются отдельно по почасовой MAE каждой величины. "
             "Сценарная модель выбирается по заранее записанной сумме ошибок обнаружения, длительности, начала, дозы холода и почасовых ошибок. "
             "Веса — исследовательский критерий, не экономический регламент хозяйства. Для аналогов тревога определяется долей холодных сценариев ≥0,25; "
             "расписание — один целый представитель холодных/тёплых сценариев. Это отдельная задача от точечной медианной температуры.", "",
             f"Выбраны: T — **{selected['selected_point_models']['temperature']}**, RH — **{selected['selected_point_models']['humidity']}**, "
             f"ветер — **{selected['selected_point_models']['wind']}**. Сценарии — **{decision}**.", "",
             f"Выбор сохранён: `{selected['selection_saved_at_utc']}` UTC. SHA выбора: `{s['selection_artifact_sha256']}`. "
             f"Совпадение исходных байтов learner/selector до добавления отчётных функций: **{s['learner_source_unchanged_since_selection']}**. "
             "SHA всех моделей проверены до и после оценки; после просмотра оценочных лет ни параметры, ни победители не изменены.", "",
             "## Почасовые величины", "",
             "| Сезон | Цель | GFS MAE | Выбранные точечные модели MAE | Парных часов | Улучшение: 95% парный интервал, блок 7 дней |",
             "|---|---|---:|---:|---:|---|"]
    for year, key in [(2025, "historical"), (2026, "fresh")]:
        e = s.get(key)
        if not e: continue
        base, new = [e["scores"][name] for name in ["gfs", "selected_points"]]
        for target, label in [("temperature", "T, °C"), ("humidity", "RH, п.п."), ("wind", "ветер, м/с")]:
            ci = e["paired_bootstrap_point_vs_gfs"][target]["7"]["interval_95"]
            lines.append(f"| {year} | {label} | {fmt(base['mae'][target])} | {fmt(new['mae'][target])} | {new['paired_hours'][target]} | {fmt(ci[0])}…{fmt(ci[1])} |")
    lines += ["", "Положительное улучшение означает уменьшение MAE. Температура улучшилась по среднему, но её интервал включает ноль в обоих оценочных сезонах. "
              "Для RH и ветра интервалы с блоком 7 дней положительны; также сохранены блоки 3/14 дней. "
              "Одна станция и два сезона не доказывают переносимость на сад. Ветер: GFS мгновенный вектор на 10 м против станционного сообщения, "
              "высота/репрезентативность станционного анемометра отдельно не подтверждены.", "", "## Заморозки: честное сравнение", "",
              "| Сезон | Метод | Попадания / холодных ночей | Пропуски | Ложные тревоги | MAE длительности холодных ночей, ч | Ошибка первого холодного отсчёта на своих попаданиях, ч | Пар начала |",
              "|---|---|---:|---:|---:|---:|---:|---:|"]
    for year, key in [(2025, "historical"), (2026, "fresh")]:
        e = s.get(key)
        if not e: continue
        for name, label in [("gfs", "GFS"), ("previous_hourly", "Прежние почасовые модели"), ("selected_points", "Новые точечные модели"), (decision, "Новый сценарный детектор")]:
            v = e["scores"][name]
            lines.append(f"| {year} | {label} | {v['hits']}/{v['cold_nights']} | {v['misses']} | {v['false_alarms']} | {fmt(v['cold_duration_mae_h'])} | {fmt(v['onset_mae_hits_h'])} | {v['onset_pairs']} |")
        old = e["previous_detector"]
        lines.append(f"| {year} | Прежний отдельный детектор | {old['hits']}/{old['cold_nights']} | {old['misses']} | {old['false_alarms']} | {fmt(old['cold_bin_duration_mae_cold_h'])} | {fmt(old['first_cold_sample_mae_hits_h'])} | {old['first_sample_pairs']} |")
    lines += ["", "**Новый сценарный детектор не рекомендуем вместо прежнего.** В 2025 он сохранил 6/6, но дал 10 ложных тревог против трёх. "
              "В новом 2026 прежний детектор распознал 4/4 с одной ложной тревогой, новый — 3/4 с пятью. "
              "MAE на своих попаданиях нельзя сравнивать без одинакового набора: совпадающие даты и ошибки начала перечислены в JSON. "
              "Улучшения расписания против GFS не установлено. Краевые холодные эпизоды цензурированы; «первый отсчёт» не равен точному моменту начала.", "",
              "## Дополнительные цели", "",
              "- Минимум ночи: функционал целой кривой; для ансамбля берётся распределение минимумов, а не минимум медианной кривой.",
              "- Интенсивность холода: сумма `max(0, −T)` по часовым интервалам, °C·ч. Это погодный показатель, не модель повреждений цветков.",
              "- Часы высокой RH ≥90%, слабого ветра <2 м/с и совместного холода/высокой RH. Пороги фиксированы заранее; они не являются агрономическими диагнозами.", "",
              "| Сезон | Показатель | GFS MAE | Новые точечные модели MAE | Сценарная модель MAE |",
              "|---|---|---:|---:|---:|"]
    for year, key in [(2025, "historical"), (2026, "fresh")]:
        e = s.get(key)
        if not e: continue
        for metric, label in [("minimum_mae_c", "Минимум, °C"), ("cold_degree_hours_mae_cold", "Доза холода на холодных ночах, °C·ч"), ("high_humidity_hours_mae", "Высокая RH, ч"), ("low_wind_hours_mae", "Слабый ветер, ч"), ("cold_high_humidity_hours_mae", "Холод + высокая RH, ч")]:
            lines.append(f"| {year} | {label} | {fmt(e['scores']['gfs'][metric])} | {fmt(e['scores']['selected_points'][metric])} | {fmt(e['scores'][decision][metric])} |")
    lines += ["", "Средняя ошибка RH улучшилась, но длительность RH ≥90% у выбранных точечных моделей не улучшилась. "
              "Доза холода улучшилась в 2026 и существенно ухудшилась в 2025; устойчивость не доказана. "
              "Часы слабого ветра чувствительны к округлению сообщений на границе 2 м/с. "
              "RH не заменяет увлажнение листа, прогноз болезней/вредителей требует новых целей и протоколов; ущерб/урожай здесь не оцениваются.", "",
              "## Новый сезон и первичный контроль данных", ""]
    if s.get("fresh"):
        a = s["fresh_wind_crosscheck"]
        lines += [f"Загружены 92 ночи весны 2026: 1 288 часов и все шесть погодных полей одного GFS 06 UTC. "
                  f"Полных ночей по T/RH — {s['fresh']['complete_target_nights']['temperature']}; по проверенному первичному ветру — {s['fresh']['complete_target_nights']['wind']}. "
                  "Неполные цели исключены из соответствующих агрегатов, прогнозы сохранены и для этих ночей. Публикация по архивному Last-Modified с 10-минутным буфером — proxy, не подтверждение первой публичной выдачи.", "",
                  "В 2026 наблюдения Meteostat полностью перешли на METAR, ранее преобладал ISD-lite. "
                  f"Сверено {a['wind']['pairs']} часовых значений ветра с текстами URKK, архивированными IEM: "
                  f"{a['wind_close_to_correct_ms_pairs']} близки к правильной конверсии, {a['wind_numeric_mps_left_as_kmh_pattern_pairs']} имеют численный рисунок «м/с осталось как км/ч». "
                  "Это несогласованность масштаба в агрегаторе, точная причина его обработки не установлена. "
                  f"Температура совпадает почти полностью: средняя разница {fmt(a['temperature']['mean_abs_delta'], 4)} °C.", "",
                  "Заявлять улучшение ветра по исходному смешанному масштабу нельзя. Финальная оценка использует ветер из первичных METAR с явными MPS "
                  "в те же часы. Первоначальные результаты сохранены отдельно в `fresh_as_downloaded_meteostat`, итоговые — в `fresh`. "
                  "Файл Meteostat не изменён; модель, выбор и T/RH-цели не менялись. Исправление оценочной цели сделано после первичной диагностики и не считается новым слепым тестом.", ""]
    else: lines += ["Новый сезон не оценён: "+s.get("fresh_error", s["fresh_status"]), ""]
    lines += ["## Неопределённость", "", "Интервалы аналогов — эмпирические 10–90 перцентили, а не гарантированные 80% интервалы. "
              "CRPS, Brier, ширина, покрытие и число случаев в reliability bins сохранены. "
              "Для T/RH в 2026 покрытие лишь около 64/63%, поэтому вероятности и интервалы **не откалиброваны**. "
              "Одночленные GFS/регрессионные интервалы — нулевая ширина для сравнения CRPS, не интервал неопределённости. "
              "В интерфейсе не показываем проценты как надёжную вероятность повреждения сада.", "",
              "## Воспроизведение", "", "```powershell", ".\\.venv\\Scripts\\python.exe -X utf8 -m agro_checker scenario-select --offline",
              ".\\.venv\\Scripts\\python.exe -X utf8 -m agro_checker scenario-fetch --workers 10",
              ".\\.venv\\Scripts\\python.exe -X utf8 -m agro_checker scenario-study --offline --include-fresh",
              ".\\.venv\\Scripts\\python.exe -X utf8 -m agro_checker scenario-replay --night 2026-03-07", "```", "",
              "Новый архив: `data/scenario-2026/`; модели и выбор: `models/research-scenarios/`; выдачи: `data/processed/scenario-study/`. "
              "Эти данные исключены из Git. Сводка: [SCENARIO_STUDY_SUMMARY.json](SCENARIO_STUDY_SUMMARY.json). "
              "Локальный отдельный просмотр: [127.0.0.1:8770/research](http://127.0.0.1:8770/research).", "",
              "## Первичные источники", "",
              "Метод — собственная адаптация [ансамбля аналогов Delle Monache et al., 2013](https://journals.ametsoc.org/doi/pdf/10.1175/MWR-D-12-00281.1), "
              "с остатками целых ночей. Проверка калибровки следует разграничению точности и [reliability ECMWF](https://www.ecmwf.int/en/forecasts/datasets/reliability). "
              "Единицы Meteostat: [почасовые параметры](https://dev.meteostat.net/parameters). Первичные тексты: [IEM METAR archive](https://mesonet.agron.iastate.edu/request/download.phtml) "
              "и [описание API](https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?help). IEM также предупреждает об ограниченном QC своего архива; "
              "сверка текстов не подтверждает калибровку анемометра в саду.", ""]
    (directory/"SCENARIO_STUDY_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    plot_report(s, directory)


def plot_report(s, directory):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    years = [(2025, s["historical"])]+([(2026, s["fresh"])] if s.get("fresh") else [])
    fig, axes = plt.subplots(len(years), 3, figsize=(12, 3.3*len(years)), squeeze=False)
    for row, (year, e) in enumerate(years):
        for col, (target, label) in enumerate([("temperature", "Температура, °C"), ("humidity", "Влажность, п.п."), ("wind", "Ветер, м/с")]):
            ax = axes[row, col]; values = [e["scores"][n]["mae"][target] for n in ["gfs", "selected_points"]]
            ax.bar(["GFS", "Новая модель"], values, color=["#769986", "#355eaa"])
            for i, value in enumerate(values): ax.text(i, value, f"{value:.3f}", ha="center", va="bottom")
            ax.set_ylim(0, max(values)*1.25); ax.set_title(f"{year} · {label}"); ax.set_ylabel("Почасовая MAE")
            ax.spines[["top", "right"]].set_visible(False)
    fig.suptitle("Выбор 2023–2024, финальное обучение 2022–2023\n2026: ветер проверен по первичным METAR; одна станция, не сад", fontsize=12)
    fig.tight_layout(rect=[0, 0, 1, .91]); assets = directory/"assets"; assets.mkdir(exist_ok=True)
    fig.savefig(assets/"scenario-quality.png", dpi=170); plt.close(fig)


def build_viewer(summary, data_dir):
    frames = json.loads((data_dir/"processed/scenario-study/night-forecasts.json").read_text(encoding="utf-8"))
    payload = {"frames": frames, "scores": {str(y): summary[k]["scores"]["selected_points"] for y, k in [(2025, "historical"), (2026, "fresh")] if summary.get(k)},
               "base_scores": {str(y): summary[k]["scores"]["gfs"] for y, k in [(2025, "historical"), (2026, "fresh")] if summary.get(k)},
               "detectors": {str(y): {"new": summary[k]["scores"][summary["selection"]["selected_decision_model"]], "previous": summary[k]["previous_detector"]} for y, k in [(2025, "historical"), (2026, "fresh")] if summary.get(k)},
               "uncertainty": {str(y): summary[k]["paired_bootstrap_point_vs_gfs"] for y, k in [(2025, "historical"), (2026, "fresh")] if summary.get(k)},
               "selection_at": summary["selection"]["selection_saved_at_utc"]}
    template = (ROOT/"web/research/index.html").read_text(encoding="utf-8")
    content = template.replace("__RESEARCH_DATA__", json.dumps(payload, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c"))
    output = ROOT/"dist/research"; output.mkdir(parents=True, exist_ok=True)
    (output/"index.html").write_text(content, encoding="utf-8")
    return output/"index.html"
