"""Freeze2024 models, issue pre-outcome comparisons, evaluate later, never tune on future data."""
import argparse
from contextlib import redirect_stdout
from html import escape
import json
from pathlib import Path
import sys
import traceback

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agro_checker import prospective_scouting as study,sector_refresh as satellite
from agro_checker.sentinel_updates import update_lock


def refresh():
    through=study.now();version='prospective-'+through.strftime('%Y%m%dT%H%M%S')
    folder=satellite.DIRECTORY/'versions'/version
    with update_lock(satellite.DIRECTORY):
        p=satellite.initialise(through.isoformat(),folder)
        cat=satellite.catalogue(p,folder)
        previous=None
        pointer=satellite.DIRECTORY/'current.json'
        if pointer.exists():
            # Validate the previous published archive before reusing byte-identical caches.
            study.snapshot(through)
            previous=satellite.DIRECTORY/study.read(pointer)['directory']
        result=satellite.collect(through.isoformat(),folder,prepared_catalogue=cat,cache_from=previous)
        if result['counts']['extraction_error']:raise ValueError('Refresh incomplete; old publication retained')
        satellite.analyse(folder);satellite.publish(folder)
    return dict(version=version,counts=result['counts'])


def save_report(directory):
    report=study.report(directory)
    releases=[study.checked(path)['payload'] for path in sorted((directory/'releases').glob('*.json'))]
    report['release_list']=[{k:r[k] for k in ('anchor_acquisition_date','issued_at_utc','target_window','selected','slots_each')} for r in releases]
    target=directory/'reports';target.mkdir(parents=True,exist_ok=True)
    temporary=target/'latest.json.tmp';temporary.write_bytes(study.encoded(report)+b'\n');temporary.replace(target/'latest.json')
    rows=''.join('<tr><td>'+escape(r['anchor_acquisition_date'])+'</td><td>'+escape(r['issued_at_utc'])+'</td><td>'+escape(', '.join(r['selected']['baseline']))+'</td><td>'+escape(', '.join(r['selected']['candidate']))+'</td><td>'+escape(r['target_window']['finalisation_not_before_utc'])+'</td></tr>' for r in releases)
    cells=[]
    for name,label in [('baseline','Простая база'),('candidate','Исправленный кандидат')]:
        m=report['metrics'][name]
        rate='не оценено' if m['precision_lower'] is None else f"{m['precision_lower']:.1%}…{m['precision_upper']:.1%}"
        cells.append(f"<tr><td>{label}</td><td>{m['slots']}</td><td>{m['tp']}</td><td>{m['fp']}</td><td>{m['unknown']}</td><td>{rate}</td></tr>")
    document='''<!doctype html><html lang="ru"><meta charset="utf-8"><title>Onlyfarms · Будущие выпуски</title>
<style>body{font:16px/1.5 system-ui;background:#f5f6f1;color:#203d30;max-width:1200px;margin:48px auto;padding:0 24px}h1{font-size:32px}table{border-collapse:collapse;width:100%;background:white;margin:24px 0}td,th{padding:12px;text-align:left;border-bottom:1px solid #d9ded7;font-size:14px}small{color:#59675f}.notice{padding:20px;border-left:4px solid #39765a;background:#e7eee5}</style>
<h1>Проверка будущих выпусков</h1><p class="notice">Прогнозы сохранены до исходов. Настройки — только 2024. Это сравнение будущих спутниковых снижений, не болезней и не пользы осмотров. Продвижение в продукт не автоматическое.</p>'''
    document+=f"<p>Выпусков: <b>{report['releases']}</b> · завершено: <b>{report['finalised']}</b> · ожидают: <b>{report['pending']}</b>. Обновлено {escape(report['generated_at_utc'])} UTC.</p>"
    document+='<h2>Только завершённые выпуски</h2><table><tr><th>Способ</th><th>Мест</th><th>Снижения</th><th>Отрицательные</th><th>Неизвестно</th><th>Границы неизвестности</th></tr>'+''.join(cells)+'</table><small>При отсутствии завершённых выпусков качество не оценено. Границы неизвестности не являются доверительным интервалом. Пропуск не считается отрицательным.</small>'
    document+='<h2>Сохранённые списки</h2><table><tr><th>Съёмка</th><th>Выпуск UTC</th><th>База</th><th>Кандидат</th><th>Не оценивать раньше UTC</th></tr>'+rows+'</table><p>В каждой паре одинаковый пул и одинаковое число мест. Горизонт 6–8 дней считается от даты съёмки; время выпуска записано отдельно. Ожидающие результаты не участвуют в метриках.</p>'
    temporary=target/'latest.html.tmp';temporary.write_text(document,encoding='utf-8');temporary.replace(target/'latest.html')
    return {k:report[k] for k in ('status','releases','finalised','pending','slots_each_issued')}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('register','issue','evaluate','report','cycle'))
    parser.add_argument('--directory',type=Path,default=study.DIRECTORY)
    parser.add_argument('--refresh',action='store_true',help='For cycle: collect and publish new sector observations first')
    args=parser.parse_args();directory=args.directory.resolve()
    directory.mkdir(parents=True,exist_ok=True)
    logfile=directory/'logs'/(study.now().strftime('%Y%m%dT%H%M%S')+'-'+args.action+'.log')
    logfile.parent.mkdir(parents=True,exist_ok=True)
    with update_lock(directory):
        with logfile.open('a',encoding='utf-8') as log,redirect_stdout(log):
            try:
                if args.action=='register':
                    p,h=study.register(directory);result=dict(status='registered',protocol_sha256=h,training=p['training'])
                elif args.action=='issue':result=study.issue(directory)
                elif args.action=='evaluate':
                    v=study.evaluate(directory);result=dict(finalised=len(v['finalised']),pending=v['pending'])
                elif args.action=='report':result=save_report(directory)
                else:
                    study.configuration(directory)  # Never refit implicitly from a daily job.
                    refresh_status=None
                    if args.refresh:refresh_status=refresh()
                    issued=study.issue(directory);evaluated=study.evaluate(directory)
                    result=dict(refresh=refresh_status,issue_status=issued['status'],
                                finalised=len(evaluated['finalised']),report=save_report(directory))
                print(json.dumps(result,ensure_ascii=False,indent=2))
            except Exception:
                traceback.print_exc(file=log)
                raise
    # Keep logs/receipts without printing complete feature vectors or models to the console.
    if args.action=='issue':result=dict(status=result['status'],anchor=(result.get('release') or {}).get('payload',{}).get('anchor_acquisition_date'))
    print(json.dumps(result,ensure_ascii=False,indent=2));print('Log:',logfile)


if __name__=='__main__':main()
