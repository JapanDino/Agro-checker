"""Post-selection diagnosis: exact Brier strata, no parameter fitting."""
import json
import math
from .pipeline import ROOT,write_json
from .product_data import sha
from . import cold_calibration_study as study


def stratum(records,name):
    rows=[r for r in records if all(v is not None for v in r['sample']['target_temperature_c'])]
    result={}
    for label,cold in [('cold',True),('warm',False),('all',None)]:
        rr=[r for r in rows if cold is None or (min(r['sample']['target_temperature_c'])<0)==cold]
        values=[];probabilities=[]
        for r in rr:
            raw=r['legacy_detector']['night_event_model_score']
            p=raw if name=='legacy' else r['threshold_forecasts']['0']['probability']
            y=int(min(r['sample']['target_temperature_c'])<0)
            probabilities.append(p);values.append((p-y)**2)
        result[label]={'nights':len(rr),'brier':sum(values)/len(values) if values else None,
                       'total_brier_contribution':sum(values)/len(rows),
                       'mean_probability':sum(probabilities)/len(probabilities) if probabilities else None}
    return result


def diagnose():
    old_summary=json.loads((ROOT/'docs/COHERENT_COLD_SUMMARY.json').read_text(encoding='utf-8'))
    file=ROOT/'data/processed/coherent-cold/night-forecasts.json'
    if sha(file)!=old_summary['forecast_sha256']:raise ValueError('Old controls changed')
    frames=json.loads(file.read_text(encoding='utf-8'));model_path=ROOT/'models/research-coherent-cold/ordinal_score_0.1-0.25.json'
    model=json.loads(model_path.read_text(encoding='utf-8'))
    a=model['b']/model['score_scale'];b=model['c']-a*model['score_center']
    result={'analysis_scope':'Frozen old-model controls: descriptive diagnosis only; not candidate selection',
            'old_control_sha256':sha(file),'old_model_sha256':sha(model_path),
            'old_logit_at0':{'slope':a,'intercept':b,'crosses_identity_probability':float(1/(1+math.exp(-b/(1-a)))) if a!=1 else None},
            'old_threshold_spacing_per_c':model['a']/model['temperature_scale'],'years':{}}
    for year in [2025,2026]:
        rows=[r for r in frames if r['year']==year];base=stratum(rows,'legacy');old=stratum(rows,'ordinal')
        delta={group:old[group]['total_brier_contribution']-base[group]['total_brier_contribution'] for group in ['cold','warm','all']}
        worst=[]
        for r in rows:
            if any(x is None for x in r['sample']['target_temperature_c']):continue
            y=int(min(r['sample']['target_temperature_c'])<0);raw=r['legacy_detector']['night_event_model_score'];p=r['threshold_forecasts']['0']['probability']
            worst.append({'date':r['date'],'event':bool(y),'raw_probability':raw,'ordinal_probability':p,
                          'squared_error_delta':(p-y)**2-(raw-y)**2})
        result['years'][str(year)]={'legacy':base,'ordinal':old,'delta_contribution':delta,
                                  'largest_error_increases':sorted(worst,key=lambda r:-r['squared_error_delta'])[:5]}
    write_json(ROOT/'docs/COLD_CALIBRATION_DIAGNOSIS.json',result);return result


def build():
    r=json.loads((ROOT/'docs/COLD_CALIBRATION_SUMMARY.json').read_text(encoding='utf-8'));s=r['selection']
    file=ROOT/'data/processed/cold-calibration/night-forecasts.json'
    if sha(file)!=r['forecast_sha256'] or sha(study.LOCK)!=r['selection_sha256']:raise ValueError('Frozen calibration changed')
    d=diagnose();coef=d['old_logit_at0']
    lines=['# Калибровка холода: причина ухудшения и отдельный эксперимент','',
           'Дата: 14 октября 2026 года. Детектор, точечный прогноз, исходные маски и времена сохранены на всех 184 ночах. Локация сада пока не выбрана. Новая обучаемая калибровка не прошла валидационные проверки; выбран исходный сигнал при 0 °C с согласованными более холодными порогами. Это устранение ухудшения предыдущего слоя, а не доказанный выигрыш над детектором.','',
           '## Почему прошлое распределение проиграло по Brier','',
           f"При 0 °C прошлый слой преобразует сигнал детектора: logit(p) = {coef['slope']:.6f}·logit(raw) + {coef['intercept']:.6f}. Ниже примерно {coef['crosses_identity_probability']*100:.1f}% вероятность повышается, выше — снижается. Brier учитывает все ночи, включая большинство тёплых, а не только число тревог.",'',
           'Преобразование помогло холодным ночам, но увеличило ошибку тёплых; в сумме оно проиграло. Ниже — точная сумма квадратов ошибок по группам с общей нормировкой, а не оценка теоретической reliability/resolution по малым корзинам. Факты используются только для диагностики уже замороженных прогнозов.','',
           '| Сезон | Группа | Ночей | Brier детектора | Brier прежней CDF | Вклад группы в Δ общего Brier |',
           '|---|---|---:|---:|---:|---:|']
    for y,v in d['years'].items():
        for group,label in [('cold','Холодные'),('warm','Тёплые'),('all','Все')]:
            a,b=v['legacy'][group],v['ordinal'][group]
            lines.append(f"| {y} | {label} | {a['nights']} | {a['brier']:.6f} | {b['brier']:.6f} | {v['delta_contribution'][group]:+.6f} |")
    lines+=['','Проблема видна и без контроля: на валидации 2024 Brier тёплых ночей повысился с 0,000756 до 0,002797. Общее улучшение валидации достигалось за счёт семи холодных событий. Малое число событий, изменение их частоты и различие OOF-сигналов менее крупных обучающих моделей и итогового детектора делают калибровку нестабильной. Это объяснение возможных причин переноса; по одному архиву причинная роль каждой не доказана.','',
            'Прошлая цель — вероятность интервала минимума, совместная для всех порогов; она не минимизировала Brier события при 0 °C напрямую. Согласованность CDF сама по себе не гарантирует калибровку. Выбранная прошлым экспериментом голова использовала только исходный сигнал детектора: новый независимый погодный сигнал она не добавляла.','',
            '## Новый протокол','',
            'Проверены 14 вариантов: исходный сигнал и прежняя CDF; три фиксированных смеси 25/50/75%; положительная affine-калибровка logit по Brier или log loss на исключённых блоках дат, со штрафами 0,1/1/10 к исходному преобразованию; ещё три Brier-варианта по прогнозам предыдущего сезона. Число новых свободных параметров — два, коэффициент положительный. Никаких class weights, балансировки или нового подбора порога тревоги; исследовательское решение фиксировано на 0,25.','',
            'Для следующего сезона калибровочные записи доступны только из прошлого: первая валидация использует OOF-2022; вторая — OOF-2022/2023 или прогнозы 2023 детектора, обученного по 2022. В последнем варианте всего три холодных события, что ограничивает устойчивость. В первом фолде источник forward явно возвращается к OOF-2022, поскольку более раннего совместимого сезона в этом протоколе нет. При недостатке событий действует исходная вероятность.','',
            'Фолды 2022→2023 и 2022–2023→2024. Все часы целевой даты исключены из OOF обучения детектора. Финальное обучение и калибровка — только 2022–2023. 2024 используется для выбора, 2025–2026 — только после фиксации. Эти контроли уже просмотрены ранее и не являются новым слепым тестом. Протокол и источники прежних экспериментов не менялись.','',
            'Проверки при 0 °C: общие пропуски, ложные тревоги, Brier и Brier холодных не хуже детектора; Brier тёплых не хуже детектора в КАЖДОМ валидационном сезоне. Это продуктовый критерий устойчивости, а не математическая необходимость для калибровки. Среди прошедших — минимум общего Brier, затем порядок реестра; identity — допустимый возврат.','',
            'Более холодные пороги привязаны к вероятности при 0 °C одним положительным наклоном прежнего распределения: logit(F(q))=logit(p0)+slope·q. При 0 °C сохраняется выбранная вероятность; порядок −3/−2/−1/0 согласован без независимых голов. Настройки холодных порогов не подбираются; их качество не обещается.','',
            f"Фиксация UTC: {s['selection_saved_at_utc']}; SHA `{r['selection_sha256']}`. Выбран **{s['winner']}**. Диагностический кандидат **{s['diagnostic']}** выбран до контроля по минимуму Brier без защитных критериев и не допускается в основной слой.",'',
            '## Валидация 2023–2024','',
            '| Вариант | Попадания | Пропуски | Ложные | Brier | Brier холодных | Brier тёплых | Все критерии |',
            '|---|---:|---:|---:|---:|---:|---:|---|']
    for name,v in s['validation'].items():
        m=v['thresholds']['0'];ok=all(s['gates'][name].values())
        lines.append(f"| {name} | {m['hits']} | {m['misses']} | {m['false_alarms']} | {m['brier']:.6f} | {m['cold_brier']:.6f} | {m['warm_brier']:.6f} | {'Да' if ok else 'Нет'} |")
    lines+=['','Лучший общий Brier нового обучаемого варианта: 0,014797 против 0,015951. Но ложные тревоги растут 1→3, Brier тёплых — 0,005679→0,008694. Это не безопасный выигрыш: по критериям выбран identity до просмотра нового контроля.','',
            '## Контроль без изменения параметров','',
            '| Год | Метод при 0 °C | Попадания | Пропуски | Ложные | Brier | Brier холодных | Brier тёплых |',
            '|---|---|---:|---:|---:|---:|---:|---:|']
    for y,season in r['seasons'].items():
        for name,label in [('legacy','Прежний детектор'),('previous_ordinal','Прежняя CDF'),('selected','Выбранный возврат'),('diagnostic','Отклонённая Brier-калибровка'),('gfs','GFS')]:
            m=season['scores'][name]['thresholds']['0']
            lines.append(f"| {y} | {label} | {m['hits']} | {m['misses']} | {m['false_alarms']} | {m['brier']:.6f} | {m['cold_brier']:.6f} | {m['warm_brier']:.6f} |")
    lines+=['','Выбранный слой совпадает при 0 °C с прежним детектором: Brier 0,017811→0,014036 в 2025 и 0,020319→0,018032 в 2026 относительно прошлой CDF; ложных тревог 3→3 / 4→1, пропусков 0/0. Над исходным детектором выигрыша нет. Более холодные пороги исследовательские; полный набор оценок и корзины частот сохранены в JSON, в том числе неполные прогнозные ночи.','',
            'Отклонённая Brier-калибровка на контроле увеличила ложные тревоги до 9/8. Её провал не меняет ни выбранный вариант, ни критерии. Согласованность выбранного слоя выполнена на всех 184 ночах; метрики факта используют 91/86 полных ночей, остальные факты не заполнены.','',
            '## Продукт и продолжение','',
            'Прошли 311 Python + 20 JavaScript = 331 проверка, включая 12 новых: ограничения лет/OOF, редкие события, неизменность исходного сигнала и выдач, согласованность порогов, воспроизведение артефактов, API без будущих фактов. Интерфейс проверяется отдельно на обычном и мобильном размере. Инженерные проверки не заменяют полевую проверку.','',
            'Экран 8790 показывает выбранный возврат отдельно от прошлой CDF и старого сценарного расписания. Калибровка не меняет полевой GFS, точку, исходные тревоги или решения пользователя. Вероятность повреждения яблони и команды защиты из неё не выводятся. Паспорт сада остаётся незаполненным, полевых доказательств нет.','',
            'Дальше нужны новые независимые холодные события, новый сезон и локальные наблюдения. Повторный поиск по 2025–2026 не выполняется. При обновлении исходного детектора калибровка должна проверяться заново на исключённых прогнозах соответствующего режима обучения.','',
            '```powershell',r'.venv\Scripts\python.exe -m agro_checker.cold_calibration_study --select-only',r'.venv\Scripts\python.exe -m agro_checker.cold_calibration_study',r'.venv\Scripts\python.exe -m agro_checker.cold_calibration_report','```','',
            'После фиксации не редактировать cold_calibration.py, cold_calibration_study.py, config/cold_calibration.json и родительские источники/модели; проверяются SHA. Отчёт и UI изменяются отдельно.','',
            '[Численные результаты](COLD_CALIBRATION_SUMMARY.json). [Диагностика ошибок по ночам](COLD_CALIBRATION_DIAGNOSIS.json). [Предыдущий эксперимент](COHERENT_COLD_REPORT.md).']
    (ROOT/'docs/COLD_CALIBRATION_REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


if __name__=='__main__':build();print('Calibration diagnosis and report saved')
