"""Export actual radar audit and frozen ablation; no fitting, scoring or source changes."""
from collections import Counter
import csv
from html import escape
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agro_checker import radar_data as d,radar_experiment as study

LABELS=dict(baseline='Простая база',fixed_candidate='Исправленный кандидат',
    optical_logit='Logistic · оптика',optical_extra='ExtraTrees · оптика',
    radar_logit='Logistic · оптика + радар',radar_extra='ExtraTrees · оптика + радар',radar_consensus='Консенсус · оптика + радар')


def main():
    p=d.read(study.D/'protocol.json');study.checked(p)
    r=d.read(study.D/'report.json');s=d.read(d.D/'satellite.json');q=d.read(d.D/'quality/scene-risk.json');catalogue=d.read(d.D/'catalogues.json')
    if r['protocol_sha256']!=d.sha(study.D/'protocol.json') or r['radar_manifest_sha256']!=d.sha(d.D/'satellite.json'):raise ValueError('Changed experiment evidence')
    risks={v['id']:v for v in q['scenes']};tracks=Counter(v['track'] for v in s['scenes'])
    counts=Counter();csv_rows=[]
    for scene in s['scenes']:
        for fid,value in scene['fields'].items():
            core=value['values'].get('core');counts[fid]+=int(core is not None)
            csv_rows.append(dict(fragment=fid,acquired_utc=scene['at'],scene=scene['id'],track=scene['track'],
                vv_db=core['vv_db'] if core else None,vh_db=core['vh_db'] if core else None,
                vh_minus_vv_db=core['ratio_db'] if core else None,core_coverage=value['support']['core']['coverage'],
                core_valid_cells=value['support']['core']['cells'],rfi_source_status=risks[scene['id']]['status'],
                farm_pixels_proven_affected=False))
    with (d.D/'sector-series.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(csv_rows[0]));writer.writeheader();writer.writerows(csv_rows)
    ages=[v['features'][6] for v in d.read(study.D/'feature-audit.json')['validation'] if v['current']]
    source_ids={v['id'].removesuffix('_rtc') for v in catalogue['pc_rtc']['items']};grd_ids={v['id'] for v in catalogue['earth_grd']['items']}
    audit=dict(rtc_scenes=len(s['scenes']),grd_scenes=len(grd_ids),missing_rtc_ids=sorted(grd_ids-source_ids),
        tracks=dict(tracks),core_usable_scenes_by_fragment=dict(counts),derived_series_rows=len(csv_rows),
        source_rfi_risk_scenes=q['potential_source_rfi_count'],validation_feature_pairs_with_source_risk=q['validation_pairs_using_potential_rfi_source'],
        all_nonlisted_quality_clearance='unknown',rfi_matching_is_sector_mask=False,
        model_inputs=len(ages),old_unknown_optical_outcomes_recovered=0,min_radar_age_days=min(ages),max_radar_age_days=max(ages))
    d.dump(d.D/'audit-report.json',audit)
    metrics=''.join(f'<tr><th>{escape(LABELS[v["method"]])}</th><td>{v["metrics"]["tp"]}</td><td>{v["metrics"]["fp"]}</td><td>{v["metrics"]["unknown"]}</td><td>{v["paired"]["fixed_candidate"]["lost_known_baseline_tp"]}</td></tr>' for v in r['table'])
    documents=''.join(f'<li><a href="../sentinel1-2024/{escape(x["path"].split("sentinel1-2024/")[1])}">{escape(Path(x["path"]).name)}</a> · {x["pages"]} стр.</li>' for x in q['documents'])
    document=f'''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Onlyfarms · Sentinel-1 и очередь осмотров</title><style>
body{{max-width:1040px;margin:55px auto;padding:0 24px;background:#f5f6f0;color:#21392d;font:16px/1.6 system-ui}}
h1{{font-size:36px;line-height:1.15;max-width:850px}}h2{{font-size:23px;margin-top:36px}}.muted{{color:#68756b}}table{{width:100%;border-collapse:collapse;font-size:14px}}
th,td{{text-align:left;padding:12px 8px;border-bottom:1px solid #d3dace}}th{{font-weight:500}}a{{color:#286348}}img{{width:100%;height:auto}}.lead{{font-size:21px}}footer{{font-size:12px;margin:40px 0}}</style>
<p class="muted">ONLYFARMS / РАЗРАБОТКА МОДЕЛИ / 9 НОЯБРЯ 2026</p>
<h1>Радарные признаки пока не улучшили очередь</h1>
<p class="lead">102 реальных съёмки 2024 года. Допуск новой модели не пройден. Прежние модели и будущий эксперимент сохранены.</p>
<h2>Что удалось собрать</h2><p>Архив RTC содержит 102 сцены четырёх орбитальных серий, исходный каталог GRD — 120 записей.
18 исходных записей отсутствуют в RTC. Скачать удалось все 102 вырезки VV/VH. Для 26 из 27 приблизительных
обводок есть пригодное ядро; R18 не проходит геометрический минимум. <a href="../sentinel1-2024/sector-series.csv">Ряды по всем площадям · CSV</a>.</p>
<p>Радарная пара доступна для всех 699 исходных кандидатов модели. Возраст последнего радарного входа: {min(ages):.2f}–{max(ages):.2f} суток.
Сравниваются только одна платформа, относительная орбита и направление, на одинаковых пригодных пикселях.
Это данные обратного рассеяния, не NDMI и не измеренная влажность почвы. 331 неизвестный оптический исход сохранён.</p>
<h2>Результат при одинаковом бюджете</h2><p>По 133 выбора на 46 датах. Настройки фиксированы до опыта; временные блоки июнь–октябрь 2024,
обучение только на завершённых предыдущих исходах. 2025–2026 для обучения и настройки не использовались.
Этот многократно исследованный 2024 остаётся разработческим, не независимым тестом.</p>
<table><tr><th>Метод</th><th>Снижение</th><th>Без снижения</th><th>Неизвестно</th><th>Потеряно находок исправленного кандидата</th></tr>{metrics}</table>
<p>Радарный консенсус повторил общие числа 23 / 45 / 65, но потерял две известные находки кандидата
(R22 · 1 июня и R11 · 10 августа), заменив их другими положительными случаями. С учётом неизвестных
возможный рост отрицательных выборов относительно кандидата — до 6. Критерии сохранения находок и отсутствия роста ложных выборов не выполнены.</p>
<p>Выбор только по предшествующим месяцам во всех пяти блоках оставил прежнего кандидата.
Оптические контрольные модели воспроизвели сохранённые оценки, поэтому отличие опыта связано с добавлением радарных признаков и их переобучением,
а не тихой сменой старых параметров. Причина изменения поверхности и полезность осмотра не размечены.</p>
<img src="../../../docs/assets/radar-priority-2024.png" alt="Результаты выбора мест с известными и неизвестными исходами">
<h2>Официальные риски качества</h2><p>После фиксации результатов отдельно проверены 12 месячных отчётов SAR-MPC ESA.
У 13 из 102 наших исходных съёмок есть совпадения с перечнями продуктов/перекрывающихся съёмок с остаточными RFI.
Они участвовали в 273 из 699 входных радарных пар.</p>
<p><b>Это риск исходного продукта, а не доказательство помех на пикселях хозяйства.</b> Перечни неполные,
а помехи могут занимать только часть сцены. Отсутствие в перечне также не означает подтверждённую чистоту.
Эта проверка не меняла маски, метки, очереди или модели и не доказывает причину проигрыша радара.</p>
<p><a href="../sentinel1-2024/quality/scene-risk.json">Совпадения, страницы и исходные имена продуктов</a>.</p>
<details><summary>12 официальных отчётов, использованных в проверке</summary><ul>{documents}</ul></details>
<h2>Что работает параллельно</h2><p>Ежедневный будущий эксперимент сохранил прежние модели и протокол.
Планировщик успешно выполнил цикл 9 ноября в 18:00 МСК. Первый выпуск ещё ожидает целевые даты
14–16 ноября; окончательная проверка — не раньше 20 ноября. Ожидающие исходы не входят в метрики.</p>
<p><a href="report.json">Полный результат</a> · <a href="protocol.json">Протокол опыта</a> · <a href="feature-audit.json">Даты и источники каждого входа</a> ·
<a href="../sentinel1-2024/audit-report.json">Аудит данных</a></p>
<footer>Новые модели остаются отдельными исследовательскими артефактами. Фактическое время публикации исторических RTC
не установлено: это опыт по датам приобретения архива, а не исторический live-бэктест. Границы не подтверждены; вымышленных осмотров нет.</footer></html>'''
    (study.D/'index.html').write_text(document,encoding='utf-8')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    names=['baseline','fixed_candidate','radar_logit','radar_extra','radar_consensus']
    entries={v['method']:v['metrics'] for v in r['table']}
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'axes.spines.top':False,'axes.spines.right':False})
    fig,ax=plt.subplots(figsize=(11.8,5.4),facecolor='#f5f6f0');ax.set_facecolor('#f5f6f0');left=np.zeros(len(names))
    for key,label,colour in [('tp','Снижение','#397653'),('fp','Без снижения','#b18b71'),('unknown','Неизвестно','#d1d8cc')]:
        values=np.array([entries[n][key] for n in names]);ax.barh(names,values,left=left,color=colour,label=label,height=.58)
        for i,(v,a) in enumerate(zip(values,left)):ax.text(a+v/2,i,str(v),ha='center',va='center',color='white' if key=='tp' else '#243d30')
        left+=values
    ax.set_yticks(range(len(names)),[LABELS[n] for n in names]);ax.invert_yaxis();ax.set_xlim(0,136);ax.set_xlabel('Выбранные позиции · одинаковый бюджет');ax.legend(ncol=3,frameon=False,loc='upper left',bbox_to_anchor=(0,-.17))
    ax.set_title('Радар не прошёл критерии замены очереди',loc='left',pad=22,fontsize=18)
    fig.text(.03,.015,'Разработка на 2024 · будущие спутниковые снижения, не полезность осмотра · неизвестные сохранены',fontsize=9,color='#68756b')
    fig.tight_layout(rect=(0,.07,1,1));image=d.ROOT/'docs/assets/radar-priority-2024.png';fig.savefig(image,dpi=180);plt.close(fig)
    files={}
    for folder in (d.D,study.D):
        for path in sorted(folder.rglob('*')):
            if path.is_file() and path.suffix in ('.json','.csv','.pdf','.html','.joblib') and path.name!='artifact-manifest.json':files[path.relative_to(d.ROOT).as_posix()]=d.sha(path)
    files[image.relative_to(d.ROOT).as_posix()]=d.sha(image)
    code=['agro_checker/radar_data.py','agro_checker/radar_experiment.py','scripts/audit_sector_radar.py','scripts/experiment_radar_priority.py',
        'scripts/audit_radar_disclaimers.py','scripts/resolve_radar_quality.py','scripts/match_radar_quality.py','scripts/report_radar_priority.py']
    d.dump(study.D/'artifact-manifest.json',dict(files=files,code_sha256={n:d.sha(d.ROOT/n) for n in code},
        crop_cache_ignored_in_git=True,crop_hashes_in_scene_manifests=True))
    print('Exported readable report,2754-row radar series, figure and checksum manifest')


if __name__=='__main__':main()
