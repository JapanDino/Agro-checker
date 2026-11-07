"""Publish all preregistered ablations; cannot change the primary after test."""
from pathlib import Path
from collections import Counter
import sys
import zipfile
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tmp/georef-deps')]
from agro_checker.farm_context_data import read,dump,digest
from agro_checker.early_season_decline import D,ARCHIVE,NAMES


def fmt(v):return '—' if v is None else f'{v*100:.1f}%'


if __name__=='__main__':
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    p=read(D/'experiment-protocol.json');f=read(D/'frozen-selection.json');r=read(D/'report.json')
    archive=read(D/'archive-summary.json');audit=read(ARCHIVE/'catalogue-audit.json')
    snapshot=read(ARCHIVE/'satellite.json');verify=read(D/'verification.json')
    names={'2024_only':'Только 2024','nearest_early_plus_2024':'Ближайший ранний + 2024',
        'all_early_plus_2024':'Все ранние + 2024','all_early_decay2_plus_2024':'Все ранние, вес по давности + 2024'}
    archive_table=[]
    for y,summary in archive['by_year'].items():
        scenes=[v for v in snapshot['scenes'] if v['acquired_at_utc'][:4]==y]
        period='—' if summary['first_pair'] is None else summary['first_pair']+' → '+summary['last_target']
        archive_table.append(f"| {y} | {sum(v['status']=='ready' for v in scenes)} | {period} | {summary['pairs']} | {summary['fit_pairs']} | {summary['events'][0]} / {summary['events'][1]} |")
    fit_table=[]
    for regime,summary in f['fitting'].items():
        fit_table.append(f"| {names[regime]} | {summary['pairs']} | {summary['dates']} | {summary['events'][0]} / {summary['events'][1]} |")
    support_table=[]
    for index,summary in archive['common_support_audit'].items():
        support_table.append(f"| {index.upper()} | {summary['events']} | {summary['events_with_common_support']} | {summary['events_retained_on_common_support']} |")
    selection=[]
    ablation=[]
    for j,index in enumerate(NAMES):
        for choice in f['choices'][j]:
            m=choice.get('metrics');regime=choice['regime']
            if choice['enabled']:
                selection.append(f"| {index.upper()} | {names[regime]} | {choice['threshold']} / {choice['gate']} | {m['tp']} / {m['tp']+m['fn']} | {m['fp']} | {m['tp_dates']} |")
            else:
                selection.append(f"| {index.upper()} | {names[regime]} | Отказ по 2024 | — | — | — |")
            test=r['results']['all2026'][regime][index]['new']
            title=names[regime]+(' (отказ; прежний выход)' if not choice['enabled'] else '')
            ablation.append(f"| {index.upper()} | {title} | {test['tp']} / {test['tp']+test['fn']} | {test['fp']} | {test['fp_dates']} | {fmt(test['recall'])} | {fmt(test['precision'])} |")
    primary=[]
    for j,index in enumerate(NAMES):
        c=f['primary'][j]
        primary.append(f"- **{index.upper()}**: {names.get(c['regime'],'прежний детектор')}; условие по всему сектору и резерву: **{'выполнено' if r['test_constraints'][index] else 'не выполнено'}**.")
    comparison=[]
    for index in NAMES:
        for title,version in [('Сохранить индекс','persistence'),('Прежний Ridge','legacy'),('Выбранный по 2024','new')]:
            m=r['results']['all2026']['primary'][index][version]
            comparison.append(f"| {index.upper()} | {title} | {m['tp']} / {m['tp']+m['fn']} | {m['fp']} | {m['fp_dates']} | {fmt(m['recall'])} | {fmt(m['precision'])} |")
    spatial=[]
    for split,title in [('spatial2024','Отложенные площади 2024'),('spatial2026','Отложенные площади 2026'),('temporal2026','Остальные площади 2026')]:
        for index in NAMES:
            m=r['results'][split]['primary'][index]
            a,b=m['legacy'],m['new']
            spatial.append(f"| {title} | {index.upper()} | {b['pairs']} | {a['tp']} → {b['tp']} из {b['tp']+b['fn']} | {a['fp']} → {b['fp']} |")
    asset=ROOT/'docs/assets/farm-reference/early-season-decline.png'
    fig,axes=plt.subplots(2,2,figsize=(13,7.5),layout='constrained')
    colors=['#acbcb1','#93aeb7','#d3a86e','#b66a4c']
    for j,index in enumerate(NAMES):
        for col,(metric,label) in enumerate([('tp','Найденные снижения'),('fp','Ложные сигналы')]):
            ax=axes[j,col]
            values=[r['results']['all2026'][regime][index]['new'][metric] for regime in p['regimes']]
            bars=ax.bar(np.arange(4),values,color=colors)
            ax.bar_label(bars,padding=3)
            ax.set_xticks(np.arange(4),['2024','Ближайший\n+ 2024','Все ранние\n+ 2024','Все ранние\nс давностью'],fontsize=9)
            ax.set_title(index.upper()+' · '+label,fontsize=12)
            ax.set_ylim(0,max(values)+5)
            ax.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))
            ax.spines[['top','right']].set_visible(False);ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True)
    fig.suptitle('Дополнительные сезоны: все варианты зафиксированы по 2024 до проверки 2026',fontsize=14,fontweight='bold')
    fig.supxlabel('499 зависимых пар, 27 дат. Отказ по 2024 = прежний детектор. Основной вариант нельзя выбирать по этому графику.',fontsize=9)
    fig.savefig(asset,dpi=160,facecolor='white');plt.close(fig)
    passed=any(r['test_constraints'].values())
    conclusion='Есть ограниченный результат по условиям проверки; производственного подключения нет.' if passed else 'Улучшить обнаружение без роста ложных тревог на всём секторе и резерве не удалось. Производственного подключения нет.'
    text=f'''# Ранние сезоны Sentinel-2 и обнаружение резких снижений

Дата: 20 октября 2026. **{conclusion}** Алгоритмы, варианты обучения и правила выбора зафиксированы до проверки. Прежние точечный прогноз, диапазоны, детектор, интерфейс и журнал сохранены.

## Что собрано

Проверен Earth Search Sentinel-2 Collection-1 L2A за январь–октябрь 2019–2023 для той же сетки 20 м и приблизительных контуров. В каталоге {audit['items']} записей; допущено {audit['accepted']} сцен, итоговые статусы: {snapshot['counts']}. Вырезки четырёх слоёв (SCL, red, NIR, SWIR), индексы, метаданные, версии удалённых объектов и SHA256 сохранены локально. Ясные пиксели не интерполируются. Полнота относится к возвращённому каталогу, а не к доказанной полноте всех съёмок ESA.

Официальное описание [Collection-1](https://sentiwiki.copernicus.eu/web/s2-processing) различает PB05.00 для ранних лет и PB05.10/05.11 для последующего переобработанного архива. Поэтому допущены только эти три версии. PB05.09 в возвращённом каталоге исключён из этого эксперимента: это не утверждение, что любой такой снимок непригоден. Проверяются согласованность scale/offset между TIFF и STAC, отсутствие двусмысленного уже применённого offset, покрытие всей сетки и высота Солнца≥20°. Причины исключения: {audit['exclusion_counts']}.

| Год | Готовых сцен | Первая выдача → последний факт | Пар по всем фрагментам | Пар для обучения | Снижений NDVI / NDMI для обучения |
|---|---:|---|---:|---:|---:|
{chr(10).join(archive_table)}

Количество снимков не равно количеству независимых примеров. Пары требуют предыдущего наблюдения за 2–21 день и цели через 6–8 дней, одной версии обработки, ≥10 пригодных ячеек и ≥80% покрытия фиксированного ядра. Январь–февраль — только прошлая история; целевые пары март–октябрь. История ограничена текущим годом, прошлым временем и той же версией обработки. Пять резервных фрагментов R23–R27 не обучают и не выбирают настройки ни в одном году.

Смена облачной поддержки дополнительно проверена на общих пригодных пикселях, без удаления неудобных меток. Аудит исходных ранних событий по всем фрагментам:

| Индекс | Исходные события | Достаточно общих пикселей | Событие сохраняется на общих пикселях |
|---|---:|---:|---:|
{chr(10).join(support_table)}

Контуры приблизительные; новый архив не подтверждает фактические границы, культуры, операции или причину снижения. Даты начала пар и конца целевых наблюдений по годам записаны в `archive-summary.json`; отсутствие снимка в каталоге не заменялось интерполяцией.

## Как изолирован вклад старых данных

Взяты два алгоритма, выбранные по 2024 в предыдущем эксперименте: NDVI ExtraTrees с историей/сезонными признаками, NDMI логистическая регрессия с семью временными признаками. Их параметры и признаки не менялись. Сравнены четыре заранее заданных режима: только2024; ближайший доступный ранний год+2024; все ранние годы+2024; те же годы с весом давности и периодом полураспада2года. В каждом случае даты имеют равный суммарный вес, затем веса нормированы; в последнем режиме добавлен множитель 2^((год−2024)/2).

| Обучение | Пар | Дат | Снижений NDVI / NDMI |
|---|---:|---:|---:|
{chr(10).join(fit_table)}

Старые годы используются только для обучения. Выбор по тем же 274 последовательным парам/25датам 2024, июнь–октябрь. Обучение каждого месяца содержит только целевые даты строго до его начала. Будущий результат, будущая облачность, полеID и предполагаемая культура не входят в признаки. Жёсткое условие:0ложных пар и0дат с ложными тревогами, >=2попадания на>=2датах. Выбирается максимальная полнота с равным весом дат; при равенстве предпочтён более простой режим. Отбор по многократно использованному2024 оптимистичен.

## Результаты выбора только по2024

| Индекс | Обучение | Порог / условие | Найдено / событий | Ложные | Дат попаданий |
|---|---|---|---:|---:|---:|
{chr(10).join(selection)}

Все четыре режима, их пороги, веса и основной выбор заморожены до2026. Диагностическое сравнение режимов ниже заранее предусмотрено и не даёт права заменить основной вариант после просмотра теста.

{chr(10).join(primary)}

## Основной результат на2026

Неизменные499пар/27дат, выдачи с28июля, факты до19октября. Этот архив ранее изучался: настройки по нему не выбирались, но новой слепой проверкой его называть нельзя. Данных2025в существующем тестовом наборе нет; они не добавлялись и не выдумывались.

| Индекс | Вариант | Найдено / событий | Ложные пары | Дат с ложными | Полнота | Доля верных тревог |
|---|---|---:|---:|---:|---:|---:|
{chr(10).join(comparison)}

Сохранение индекса всегда выдаёт нулевое изменение:0ложных сопровождаются0попаданий. Новое обнаружение должно оцениваться вместе с ложными тревогами. Проверяются и пары, и даты ложных сигналов. Исходный точечный прогноз и его MAE не менялись; улучшение детектора не означает улучшения среднего прогноза.

## Все заранее зафиксированные режимы на2026

| Индекс | Обучение | Найдено / событий | Ложные пары | Дат с ложными | Полнота | Доля верных тревог |
|---|---|---:|---:|---:|---:|---:|
{chr(10).join(ablation)}

![Сравнение режимов обучения](assets/farm-reference/early-season-decline.png)

## Пространственная проверка основного выбора

| Поднабор | Индекс | Пар | Попадания: прежний → новый | Ложные: прежний → новый |
|---|---|---:|---:|---:|
{chr(10).join(spatial)}

Нельзя выбирать успешные площади или режим по2026. Близкие фрагменты и перекрывающиеся окна зависимы; пять южных фрагментов не являются внешним хозяйством. Срабатывания по совместному отрицательному прошлому темпу прежде всего прогнозируют продолжение снижения; предупреждение до его начала не доказано. Спектральные события не заменяют результаты осмотров.

## Контроль и воспроизведение

`scripts/collect_early_seasons.py protocol|collect|extract`; `scripts/train_early_season_decline.py protocol|fit|evaluate`; `scripts/verify_early_season_decline.py`. Проверены {verify['source_crop_hashes_checked']} хешей исходных вырезок, хронология, резерв, воспроизведение выбора, все499пар пяти вариантов, неизменность точки/старого детектора и {verify['protected_files']} прежних файлов. Заморозка: `{r['frozen_sha256']}`. После оценки повторное обучение запрещено. Данные переобработаны ретроспективно: доступность этих же байтов и задержка получения в исторический день выдачи не доказаны.

У контроля только2024 совпадают алгоритм, входы, выбранные пороги, все сигналы на отборе2024 и все499сигналов2026 с предыдущим экспериментом. Численное тождество промежуточных оценок не подтвердилось: максимальная разница NDVI {verify['control_oof_score_max_abs_difference']['ndvi']:.8f}, NDMI {verify['control_oof_score_max_abs_difference']['ndmi']:.3g}. В реализациях отличается порядок операций при нормировании весов; разница самих весов на проверенном блоке около1,1×10⁻¹⁶. Не заявляется побайтовое совпадение моделей/скоров. Все режимы нового эксперимента используют одну реализацию, а сохранённые выбор и сигналы воспроизведены. Это расхождение документировано; модели или пороги после2026не менялись.

## Интерпретация и решение

Дополнительные годы увеличили доступное обучение с489до1679пар и число событий NDVI66→203, NDMI63→189. Однако при строгом бюджете ложных тревог они не повысили полноту на2024, поэтому основной выбор остался только2024. На2026 обученные NDVI-варианты со всеми ранними годами дают0попаданий и1ложную тревогу: осторожность выросла, полезное обнаружение не появилось. Для NDMI подходящих ранних вариантов не было уже на2024; их тестовый выход в таблице — сохранённый старый детектор, а не результат нового NDMI-классификатора. Следовательно, цель не достигнута.

Из этой проверки нельзя заключить, что старые данные вообще бесполезны. Проверены конкретные алгоритмы, веса и ограниченный архив с различиями сезонов и обработки. Причинный вклад культуры, операций и версии обработки не установлен. Следующий эксперимент должен заранее отделять нормальное сезонное снижение/уборку от агрономически значимой проблемы по фактическим данным и иметь новую проверку. Подбор нового порога по уже увиденным2026ошибкам здесь не выполнялся.
'''
    for before,after in [('только2024','только 2024'),('до2026','до 2026'),('по2024','по 2024'),
        ('на2026','на 2026'),('2025в','2025 в'),('499пар/27дат','499 пар / 27 дат'),
        ('274 последовательным парам/25датам','274 последовательным парам / 25 датам'),
        ('0ложных','0 ложных'),('0дат','0 дат'),('>=2попадания','≥2 попадания'),('>=2датах','≥2 датах'),
        ('28июля','28 июля'),('19октября','19 октября'),('полураспада2года','полураспада 2 года'),
        ('0попаданий','0 попаданий'),('все499пар','все 499 пар'),('по2026','по 2026'),
        ('условие:0','условие: 0'),(' и0 ',' и 0 '),('на≥2','на ≥2'),
        ('использованному2024','использованному 2024'),('Неизменные499','Неизменные 499'),
        ('с28 июля','с 28 июля'),('до19 октября','до 19 октября'),('Данных2025','Данных 2025'),
        ('сопровождаются0','сопровождаются 0'),('отборе2024','отборе 2024'),
        ('все499сигналов2026','все 499 сигналов 2026'),('около1,1','около 1,1'),
        ('после2026не','после 2026 не'),('с489до1679пар','с 489 до 1 679 пар'),
        ('NDVI66→203','NDVI 66→203'),('NDMI63→189','NDMI 63→189'),('на2024','на 2024'),
        ('На2026','На 2026'),('дают0','дают 0'),('и1ложную','и 1 ложную'),
        ('увиденным2026ошибкам','увиденным ошибкам 2026'),('полеID','ID поля')]:
        text=text.replace(before,after)
    doc=ROOT/'docs/EARLY_SEASON_DECLINE_REPORT.md';doc.write_text(text,encoding='utf-8')
    files=[doc,asset,ROOT/'agro_checker/early_season_data.py',ROOT/'agro_checker/early_season_decline.py',
        ROOT/'scripts/collect_early_seasons.py',ROOT/'scripts/train_early_season_decline.py',Path(__file__),
        ROOT/'scripts/verify_early_season_decline.py',ROOT/'tests/test_early_season_decline.py',
        *[q for q in D.iterdir() if q.is_file()],ARCHIVE/'protocol.json',ARCHIVE/'satellite.json',ARCHIVE/'catalogue-audit.json']
    bundle=ROOT/'outputs/early-season-decline-2026-10-20.zip'
    with zipfile.ZipFile(bundle,'w',zipfile.ZIP_DEFLATED) as z:
        for q in files:z.write(q,q.relative_to(ROOT).as_posix())
    dump(ROOT/'outputs/early-season-decline-2026-10-20/manifest.json',dict(archive=bundle.relative_to(ROOT).as_posix(),sha256=digest(bundle),
        files=[dict(path=q.relative_to(ROOT).as_posix(),sha256=digest(q)) for q in files],production_promoted=False))
    print(doc);print(bundle)
