"""Post-control reporting only; never changes frozen model selection."""
import json
from datetime import date
import numpy as np
from .pipeline import ROOT
from .product_data import sha
from .coherent_cold_study import LOCK


def paired_brier_interval(records, seed=42):
    # Weather autocorrelation: paired calendar-week blocks within each season.
    # An uncertainty illustration, not another score used for selection.
    blocks={}
    for r in records:
        actual=r['sample']['target_temperature_c']
        if any(v is None for v in actual):continue
        y=int(min(actual)<0);old=r['legacy_detector']['night_event_model_score']
        new=r['threshold_forecasts']['0']['probability']
        key=(date.fromisoformat(r['date'])-date(r['year'],3,1)).days//7
        blocks.setdefault(key,[]).append((new-y)**2-(old-y)**2)
    groups=list(blocks.values());rng=np.random.default_rng(seed);values=[]
    for _ in range(2000):
        selected=[groups[i] for i in rng.integers(0,len(groups),len(groups))]
        values.append(sum(map(sum,selected))/sum(map(len,selected)))
    return {'delta_brier':sum(map(sum,groups))/sum(map(len,groups)),
            'paired_calendar_7day_blocks':len(groups),
            'percentile_95_interval':np.quantile(values,[.025,.975]).tolist()}


def build():
    r=json.loads((ROOT/'docs/COHERENT_COLD_SUMMARY.json').read_text(encoding='utf-8'))
    s=r['selection'];file=ROOT/'data/processed/coherent-cold/night-forecasts.json'
    if sha(file)!=r['forecast_sha256'] or sha(LOCK)!=r['selection_sha256']:raise ValueError('Frozen artifacts changed')
    frames=json.loads(file.read_text(encoding='utf-8'))
    old=json.loads((ROOT/'docs/PRODUCT_RISK_SUMMARY.json').read_text(encoding='utf-8'))
    old_lock=ROOT/'models/research-product-risk/selection-before-controls.json'
    if sha(old_lock)!=s['previous_selection_sha256'] or sha(old_lock)!=old['selection_sha256']:raise ValueError('Previous experiment changed')
    validation=json.loads((LOCK.parent/'validation-forecasts.json').read_text(encoding='utf-8'))
    events={str(q):sum(min(n['sample']['target_temperature_c'])<q for n in validation['legacy'] if all(v is not None for v in n['sample']['target_temperature_c'])) for q in [-3,-2,-1,0]}
    lines=['# Единая вероятностная модель холода', '',
           'Дата: 14 октября 2026 года. Локация сада не выбрана. Прежние точечный прогноз, детектор, его часовые маски и времена сохранены на всех 184 контрольных ночах.', '',
           'Получено согласованное распределение минимума: ложных тревог меньше, чем у предыдущего сценарного эксперимента. Однако на контроле новая вероятность уступает прежнему детектору по Brier, а в 2026 имеет больше ложных тревог. В полевой контур не внедряется. На экране 8790 показана отдельная исследовательская карточка с четырьмя порогами.', '',
           '## Метод и защита от подбора', '',
           'Проверены 11 голов: сценарные аналоги 32/64 с одной возрастающей калибровочной функцией; порядковая логистическая модель минимума с GFS, сигналом прежнего детектора или обоими входами, со штрафами 0,1/1/10. Для каждой головы — пороги исследовательского решения 0,1/0,25/0,5/0,75. Итого 44 варианта плюс исходные сравнения; выбор среди многих вариантов может быть оптимистичным.', '',
           'В порядковой модели F(q)=sigmoid(a·q/s + b·z + c), a>0, b≥0. Варианты с GFS используют q−min(GFS), z — стандартизованный logit исходного сигнала. Общие параметры обучаются по вероятности того интервала (−∞,−3), [−3,−2), [−2,−1), [−1,0), [0,+∞), куда попал фактический минимум. Это единое распределение, а не четыре независимых классификатора. Отрицательная температура означает погодное событие, а не повреждение яблони.', '',
           'Сценарная калибровка нормирована к 0/1, сохраняет конечную поддержку и порядок порогов. Отдельные физически несуществующие холодные траектории не добавляются. Она не выиграла выбор.', '',
           'Сигнал детектора для обучения новой головы вычисляется на шести исключённых блоках целых дат: все часы даты исключаются из обучения детектора. Признаки масштабируются только на обучающем наборе. Этот OOF ретроспективный, внутри прошлых лет; он не является перспективной калибровкой. Дополнительно исключаются целые следующие сезоны: 2022→2023 и 2022–2023→2024. Финальная голова обучена по 2022–2023. Никакие цели 2024 не входят в финальное обучение, а 2025–2026 — ни в обучение, ни в калибровку, ни в выбор.', '',
           f"Выбрано **{s['winner']}**. Вход — только исходный сигнал детектора; новых погодных признаков выбранная голова не использует. Штраф 0,1, порог исследовательского решения 0,25. Прежний детектор сам не переобучен и не изменён.", '',
           'Критерии: пропусков и ложных тревог не больше прежнего детектора, Brier при 0 °C не хуже. Среди прошедших — минимум Brier, затем ложных/пропусков и порядок реестра. Ни один более холодный порог отдельно не подбирается. Согласованность выполнена по построению, но качество редких хвостов этим не доказано.', '',
           f"Фиксация UTC: {s['selection_saved_at_utc']}; SHA `{r['selection_sha256']}`. Контроли ранее просмотрены в других экспериментах, это не новый слепой тест.", '',
           '## Валидация 2023–2024', '',
           '| Метод | Попадания | Пропуски | Ложные | Brier ↓ | Несогласованных / проверено |',
           '|---|---:|---:|---:|---:|---:|']
    for name,label in [('legacy','Прежний детектор'),('previous_candidate','Прежние сценарии'),(s['diagnostic'],'Единое распределение')]:
        v=s['validation'][name];m=v['thresholds']['0']
        lines.append(f"| {label} | {m['hits']} | {m['misses']} | {m['false_alarms']} | {m['brier']:.6f} | {v['nonmonotonic_nights']} / {v['monotonicity_checked_nights']} |")
    lines+=['',f"Число событий по порогам на валидации: {events}. Малое число холодных событий ограничивает достоверность выбора и калибровки хвостов.",'',
            '## Контроль без изменения настроек', '',
            '| Сезон | Метод при 0 °C | Полных ночей | Попадания | Пропуски | Ложные | Brier ↓ | Несогласованных / проверено |',
            '|---|---|---:|---:|---:|---:|---:|---:|']
    for year,season in r['seasons'].items():
        for name,label in [('gfs','GFS'),('legacy','Прежний детектор'),('previous_candidate','Прежние сценарии'),('candidate','Единое распределение')]:
            v=season['scores'][name];m=v['thresholds']['0']
            count=str(v['monotonicity_checked_nights']) if v['monotonicity_checked_nights'] else 'нет разных порогов'
            lines.append(f"| {year} | {label} | {m['complete_nights']} | {m['hits']} | {m['misses']} | {m['false_alarms']} | {m['brier']:.6f} | {v['nonmonotonic_nights']} / {count} |")
    lines+=['', 'Новая модель: 6/6 и 4/4 событий обнаружены, ложные тревоги 11→3 и 8→4 относительно сценарного эксперимента. Прежний детектор: 3 и 1 ложная тревога, пропусков также нет. Следовательно, относительное улучшение сценарной версии не является основанием заменить основной детектор.', '',
            'Согласованность проверяется и на неполных контрольных ночах (по прогнозу): 184 из 184. Метрики факта считаются только по полным ночам — 91/86. Неполные факты не интерполируются, трудные случаи не удаляются из прогноза.', '',
            '| Сезон | Порог | Событий | Попадания | Пропуски | Ложные | Brier ↓ |',
            '|---|---|---:|---:|---:|---:|---:|']
    for year,season in r['seasons'].items():
        for q in [-3,-2,-1,0]:
            m=season['scores']['candidate']['thresholds'][str(q)]
            lines.append(f"| {year} | < {q} °C | {m['hits']+m['misses']} | {m['hits']} | {m['misses']} | {m['false_alarms']} | {m['brier']:.6f} |")
    uncertainty={y:paired_brier_interval([f for f in frames if str(f['year'])==y]) for y in r['seasons']}
    lines+=['','Парная разность Brier (новая−прежний детектор); положительная означает ухудшение. Пересэмплирование календарных семидневных блоков, 2000 повторов, seed=42; это иллюстрация неопределённости, не новый критерий выбора. Блоки приблизительно учитывают временную зависимость, но не обеспечивают независимость погодных волн.']
    lines.append('')
    for y,u in uncertainty.items():lines.append(f"- {y}: {u['delta_brier']:+.6f}; 95% диапазон [{u['percentile_95_interval'][0]:+.6f}; {u['percentile_95_interval'][1]:+.6f}], {u['paired_calendar_7day_blocks']} блоков.")
    lines+=['', '## Использование и ограничения', '',
            'Новая карточка вероятности отделена от старого сценарного расписания и диапазона минимума: CDF сама не задаёт часовые траектории, начало или длительность. API прогноза не возвращает будущие фактические температуры. Старые выдачи/журнал/профиль не меняются. Отсутствие выбранного сада, датчиков, фазы и регламента остаётся явным.', '',
            'Для уверенной калибровки нужны независимые холодные события и перспективная локальная проверка. Следующий протокол следует оценивать по новому сезону/площадке; продолжать оптимизацию по просмотренным 2025–2026 нельзя. Смена выпусков и области сигнала детектора может менять калибровку. Процент повреждений и экономия не выводятся из погодных вероятностей.', '',
            '## Воспроизведение', '',
            '```powershell', r'.venv\Scripts\python.exe -m agro_checker.coherent_cold_study --select-only',
            r'.venv\Scripts\python.exe -m agro_checker.coherent_cold_study',
            r'.venv\Scripts\python.exe -m agro_checker.coherent_cold_report',
            r'.venv\Scripts\python.exe -m agro_checker.product_server', '```', '',
            'После фиксации не редактировать coherent_cold.py, coherent_cold_study.py, config/coherent_cold.json, модели и родительские источники: проверяются SHA. Форматирование отчёта и UI развиваются отдельно.', '',
            '[Численные результаты и происхождение](COHERENT_COLD_SUMMARY.json). [Предыдущий эксперимент](PRODUCT_IMPLEMENTATION_REPORT.md).']
    (ROOT/'docs/COHERENT_COLD_REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    (ROOT/'docs/COHERENT_COLD_UNCERTAINTY.json').write_text(json.dumps(uncertainty,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':build();print('Coherent CDF report saved')
