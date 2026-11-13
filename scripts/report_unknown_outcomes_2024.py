"""Export the frozen audit as a readable report and one row per unknown outcome."""
from collections import Counter
import csv
import hashlib
from html import escape
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
D=ROOT/'data/farm-research/unknown-outcome-audit-2024'
LABELS={
    'cloud_or_shadow_scl':'Облака и тени по SCL',
    'cloud_edge_guard_loss':'Потеря после защитного расширения маски',
    'other_rejected_scl':'Другие непригодные классы SCL (1/2/7)',
    'conflicting_adjacent_scl':'Конфликт классификации соседних тайлов',
    'nodata_or_tile_edge':'Нет данных / край тайла',
    'no_same_baseline_in_window':'Нет совместимой версии обработки в окне',
    'no_catalogue_acquisition_in_window':'Нет съёмки в целевом окне',
    'adjacent_target_not_independently_confirmed':'Соседний тайл не подтверждён независимо',
}


def read(name):return json.loads((D/name).read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    report=read('report.json');ledger=read('unknown-ledger.json');protocol=read('protocol.json')
    if report['protocol_sha256']!=sha(D/'protocol.json') or report['catalogue_sha256']!=sha(D/'catalogue.json'):
        raise ValueError('Changed audit snapshot')
    for name,value in protocol['inputs'].items():
        if sha(ROOT/name)!=value:raise ValueError('Changed frozen audit input: '+name)
    if len(ledger)!=report['unknown_before'] or len({(v['fid'],v['issue']) for v in ledger})!=len(ledger):
        raise ValueError('Missing or duplicate unknowns')
    if Counter(v['reason'] for v in ledger)!=report['reasons']:raise ValueError('Wrong reason counts')
    if report['queue_metrics']['before']!=report['queue_metrics']['after']:raise ValueError('Ranking results changed')
    selected={name:{(v['fid'],v['issue']) for v in m['selected_cases']} for name,m in report['queue_metrics']['before'].items()}
    columns=['fragment','issue_utc_date','processing_baseline','reason_code','reason_ru','target_from','target_through',
             'best_valid_fraction','selected_baseline','selected_candidate','eligible_scene_ids']
    with (D/'unknown-outcomes.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=columns);writer.writeheader()
        for v in ledger:
            k=(v['fid'],v['issue'])
            writer.writerow(dict(fragment=v['fid'],issue_utc_date=v['issue'],processing_baseline=v['baseline'],
                reason_code=v['reason'],reason_ru=LABELS[v['reason']],target_from=v['nearest_target_window'][0],
                target_through=v['nearest_target_window'][1],best_valid_fraction=v['best_valid_fraction'],
                selected_baseline=k in selected['baseline'],selected_candidate=k in selected['candidate'],
                eligible_scene_ids=' | '.join(v['current_same_baseline_items'])))
    bars=''.join(f'<tr><th>{escape(LABELS[k])}</th><td><span style="width:{100*n/len(ledger):.2f}%"></span></td><td>{n}</td></tr>'
                 for k,n in sorted(report['reasons'].items(),key=lambda x:-x[1]))
    queues=''.join(f'<tr><th>{"Простая база" if name=="baseline" else "Исправленный кандидат"}</th><td>{v["tp"]}</td><td>{v["fp"]}</td><td>{v["unknown"]}</td><td>{v["slots"]}</td></tr>'
                   for name,v in report['queue_metrics']['before'].items())
    html='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Onlyfarms · неизвестные исходы 2024</title><style>
body{max-width:980px;margin:60px auto;padding:0 24px;background:#f7f8f3;color:#1e342b;font:16px/1.6 system-ui}
h1{font-size:36px;line-height:1.15;max-width:730px}h2{font-size:22px;margin-top:38px}.muted{color:#65746b}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{border-bottom:1px solid #dce1d8;padding:12px 8px;text-align:left}
th{font-weight:500}td span{display:block;height:12px;background:#397653;min-width:1px}td:nth-child(2){min-width:120px}
a{color:#246344}.lead{font-size:22px}footer{margin-top:42px;font-size:12px;color:#65746b}
</style><p class="muted">ONLYFARMS / АУДИТ АРХИВА / 2024</p>
<h1>Облака остаются неизвестным исходом</h1>
<p class="lead">Проверены все 331 пропуска. Достоверно восстановлено: <b>0</b>.</p>
<p>Повторная полная выгрузка каталога нашла те же 118 записей. Новых подходящих источников нет.
Из 699 пар остаются 368 известных и 331 неизвестный исход. Фильтры качества, окно 6–8 дней,
версии обработки, модели и очереди сохранены.</p><h2>Почему исход не определён</h2><table>'''+bars+'''</table>
<p class="muted">При нескольких сценах причина определяется по лучшей доступной доле пригодного ядра.
Это аудит спутниковой наблюдаемости, а не подтверждение агрономической причины. Защитная маска
расширяет все непригодные классы SCL, включая края без данных.</p>
<h2>Результат очередей не изменился</h2><table><tr><th>Схема</th><th>Снижение</th><th>Без снижения</th><th>Неизвестно</th><th>Выбрано</th></tr>'''+queues+'''</table>
<p>Это разработческий 2024 год. Числа относятся к будущему спутниковому снижению по прежнему
пороговому правилу, а не к полезности осмотра. Неизвестные не приравниваются к ложным выборам.
2025–2026 не использовались для настройки или оценки в этом аудите.</p>
<p><a href="unknown-outcomes.csv">Все 331 случая · CSV</a> · <a href="unknown-ledger.json">Подробные доказательства · JSON</a> ·
<a href="report.json">Машиночитаемый отчёт</a> · <a href="protocol.json">Протокол</a></p>
<footer>Снимок каталога и исходные хеши сохранены. Этот отчёт не меняет зафиксированный эксперимент будущих выпусков.
Нет вымышленных осмотров, диагнозов или подтверждённых границ хозяйства.</footer></html>'''
    (D/'index.html').write_text(html,encoding='utf-8')
    sources=['protocol.json','catalogue.json','new-satellite.json','unknown-ledger.json','evaluation-only-rows.json','report.json',
             'unknown-outcomes.csv','index.html']
    manifest=dict(files={name:sha(D/name) for name in sources},source_code={
        name:sha(ROOT/name) for name in ('scripts/audit_unknown_outcomes_2024.py','scripts/report_unknown_outcomes_2024.py')})
    (D/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(dict(unknowns=len(ledger),recovered=len(report['recovered']),exported=['index.html','unknown-outcomes.csv','manifest.json']))


if __name__=='__main__':main()
