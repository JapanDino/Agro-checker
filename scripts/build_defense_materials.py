"""Presentation assets from frozen reports and untouched UI captures. No fitting."""
import csv
import hashlib
import html
import json
from pathlib import Path
import sys
import zipfile
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from build_r11_defense_story import make_story, story_sources
OUT=ROOT/'outputs/defense-materials-2026-10-22'


def read(path):return json.loads(path.read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def build_gallery(records):
    source=ROOT/'web/defense-materials'
    screens={r['file'].split('/')[-1]:r for r in records if r.get('title')}
    order=['01-farm-map.jpg','02-satellite-change-ndmi.jpg','03-inspection-task.jpg',
           '05-result-form-preview.jpg','04-place-history-empty.jpg']
    captions=[
        ('Выбрать площадь','27 приблизительных фрагментов со скриншота хозяйства. На карте можно перейти к выбранному месту и его снимкам. Границы требуют подтверждения агрономом.'),
        ('Сравнить съёмки','R11, 21 сентября — 14 октября 2026. NDMI изменился на −0,119; общая пригодная часть — 92%. Это наблюдаемое изменение за 23 дня, а не прогноз или диагноз.'),
        ('Поставить задачу','Место и основание проверки переходят в форму задачи. Даты снимков и качество источников остаются рядом с вопросом агроному. На экране пустая форма, задача не создавалась.'),
        ('Записать результат','Дата посещения, заметка и фотографии. Показан просмотр пустой формы: фактических результатов здесь пока нет.'),
        ('Вернуться к истории','Повторные посещения будут видны по датам рядом со спутниковой динамикой. Сейчас история пуста — реальные осмотры ещё не внесены.')]
    figures=[]
    for i,(name,(title,note)) in enumerate(zip(order,captions),1):
        r=screens[name];path=html.escape(r['file']);title=html.escape(title)
        figures.append(f'''<figure class="screen">
          <a class="screen-image" href="{path}" aria-label="Открыть экран: {title}"><img loading="lazy" src="{path}" width="{r['width']}" height="{r['height']}" alt="{html.escape(r['title'])}"></a>
          <figcaption><span class="screen-number">{i:02d}</span><h3>{title}</h3><p>{html.escape(note)}</p><a class="original" download href="{path}">Скачать оригинал · {r['width']} × {r['height']}</a></figcaption>
        </figure>''')
    template=(source/'index.html').read_text(encoding='utf-8')
    gallery=template.replace('{{PRIMARY_SCREENS}}','\n'.join(figures[:3])).replace('{{SECONDARY_SCREENS}}','\n'.join(figures[3:]))
    assert '{{' not in gallery
    (OUT/'index.html').write_text(gallery,encoding='utf-8')
    (OUT/'styles.css').write_bytes((source/'styles.css').read_bytes())
    (OUT/'appendix/index.html').write_bytes((source/'appendix.html').read_bytes())


if __name__=='__main__':
    charts=OUT/'appendix/charts';charts.mkdir(parents=True,exist_ok=True)
    for name in ('01-detector-2026','02-ranking-development-2024'):
        for suffix in ('.png','.svg'):
            previous=OUT/'charts'/(name+suffix)
            if previous.exists():previous.replace(charts/(name+suffix))
    if (OUT/'metrics.csv').exists():(OUT/'metrics.csv').replace(OUT/'appendix/metrics.csv')
    sources=[ROOT/'data/farm-research/continuation-shape/report.json',ROOT/'data/farm-research/continuation-shape/frozen-selection.json',
        ROOT/'data/farm-research/ndmi-turn-guard-2024/selection-table.json',ROOT/'data/farm-research/ndmi-turn-guard-2024/forward-check.json',
        ROOT/'data/farm-research/ndmi-turn-guard-2024/frozen-selection.json',
        *[ROOT/'web/scouting'/name for name in ['index.html','styles.css','app.js']],*story_sources()]
    hashes={p.relative_to(ROOT).as_posix():sha(p) for p in sources}
    forecast=read(sources[0]);detectors={n:forecast['results']['all2026'][n]['new']['metrics'] for n in ['ndvi','ndmi']}
    assert [(m['tp'],m['fp'],m['fn']) for m in detectors.values()]==[(9,10,41),(3,3,39)]
    ranking=read(sources[2]);f=read(sources[4]);forward=read(sources[3])
    old=next(e for e in ranking if e['variant']['id']=='old_hybrid')['metrics']
    new=next(e for e in ranking if e['variant']==f['selected_variant'])['metrics'];ff=forward['result']['metrics']
    assert [m['slots'] for m in [old,new,ff]]==[133]*3
    green='#35634B';muted='#DDE0DD';red='#AD7864';ink='#252A27';bg='#FFFFFF'
    fonts=Path('C:/Windows/Fonts')
    for n in ['segoeui.ttf','segoeuib.ttf']:
        if (fonts/n).exists():font_manager.fontManager.addfont(fonts/n)
    plt.rcParams.update({'font.family':'Segoe UI','font.size':17,'axes.edgecolor':'#D4D7D4','text.color':ink,
                         'axes.labelcolor':ink,'xtick.color':ink,'ytick.color':ink,'svg.fonttype':'none'})
    def header(fig,number,title,sub):
        fig.text(.065,.945,'onlyfarms',fontsize=14,fontweight='bold')
        fig.text(.935,.945,number,fontsize=12,color='#69706B',ha='right')
        fig.add_artist(plt.Line2D([.065,.935],[.918,.918],transform=fig.transFigure,color='#DDE0DD',lw=.7))
        fig.text(.065,.852,title,fontsize=28,fontweight='bold')
        fig.text(.065,.798,sub,fontsize=15,color='#626A64')
    def save(fig,name):
        fig.savefig(charts/(name+'.png'),dpi=200,facecolor=bg)
        fig.savefig(charts/(name+'.svg'),facecolor=bg)
        plt.close(fig)
    fig=plt.figure(figsize=(16,9),facecolor=bg)
    header(fig,'А1 / ИССЛЕДОВАНИЕ','Обнаружение резких снижений',
        'Проверка на архиве 2026. Настройки выбраны по 2024; архив проверки ранее изучался.')
    ax=fig.add_axes([.145,.34,.39,.33],facecolor=bg)
    yy=np.array([.75,.25]);found=[m['tp'] for m in detectors.values()];missed=[m['fn'] for m in detectors.values()]
    ax.barh(yy,found,height=.23,color=green,label='Найдено',zorder=3)
    ax.barh(yy,missed,left=found,height=.23,color=muted,label='Пропущено',zorder=3)
    for y,tp,fn in zip(yy,found,missed):
        ax.text(tp/2,y,str(tp),ha='center',va='center',color='white',fontsize=18,fontweight='bold')
        ax.text(tp+fn/2,y,str(fn),ha='center',va='center',fontsize=18)
    ax.set(xlim=(0,50),ylim=(0,1),xlabel='Наблюдаемых снижений')
    ax.set_xticks([0,10,20,30,40,50]);ax.set_yticks([]);ax.grid(axis='x',color='#ECEEEC',zorder=0)
    ax.spines[['top','right','left']].set_visible(False);ax.tick_params(axis='x',length=0,pad=10,labelsize=14)
    ax.xaxis.label.set_size(13);ax.xaxis.labelpad=13
    for name,y in [('NDVI',.5875),('NDMI',.4225)]:fig.text(.065,y,name,fontsize=19,fontweight='bold',va='center')
    for x,label in [(.65,'Полнота'),(.78,'Верных\nсреди сигналов'),(.925,'Ложных\nсигналов')]:
        fig.text(x,.702,label,fontsize=13,color='#626A64',ha='center')
    for y,values in [(.5875,['18%','47,4%','10']),(.4225,['7,1%','50%','3'])]:
        for x,value in zip([.65,.78,.925],values):fig.text(x,y,value,fontsize=22,ha='center',va='center')
    fig.legend(*ax.get_legend_handles_labels(),loc='lower left',bbox_to_anchor=(.137,.185),ncol=2,frameon=False,fontsize=14)
    fig.add_artist(plt.Line2D([.065,.935],[.165,.165],transform=fig.transFigure,color='#DDE0DD',lw=.7))
    fig.text(.065,.122,'499 пар «площадь × дата» · 27 дат · 26 пригодных фрагментов',fontsize=14)
    fig.text(.065,.080,'Событие: снижение индекса ≥ 0,10 через 6–8 дней. Большинство событий пока пропускается.',fontsize=13,color='#626A64')
    fig.text(.065,.043,'Исследовательский детектор. Не независимый слепой тест и не оценка пользы осмотров.',fontsize=13,color='#626A64')
    save(fig,'01-detector-2026')
    fig=plt.figure(figsize=(16,9),facecolor=bg)
    header(fig,'А2 / РАЗРАБОТКА','Проверка защитного правила',
        'Архив 2024. Лимит до трёх мест на дату; во всех вариантах 133 позиции на 46 датах.')
    ax=fig.add_axes([.27,.30,.66,.40],facecolor=bg);totals=np.zeros(3);yy=np.arange(3)
    for key,label,color in [('tp','Найдено снижение',green),('fp','Снижения не было',red),('unknown','Исход неизвестен',muted)]:
        vals=np.array([m[key] for m in [old,new,ff]]);ax.barh(yy,vals,left=totals,height=.58,color=color,label=label)
        for i,(v,left) in enumerate(zip(vals,totals)):ax.text(left+v/2,i,str(v),ha='center',va='center',fontsize=23,fontweight='bold',color='white' if key=='tp' else ink)
        totals+=vals
    ax.set_yticks(yy,['Прежний гибрид','Новая защита','Выбор по прошлым\nмесяцам'],fontsize=18);ax.invert_yaxis();ax.set_xlim(0,133)
    ax.set_xticks([0,30,60,90,120]);ax.spines[['top','right','left']].set_visible(False);ax.tick_params(axis='y',length=0,pad=14)
    ax.tick_params(axis='x',length=0,labelsize=14);ax.set_xlabel('Выбранных позиций',labelpad=10,fontsize=13)
    fig.legend(*ax.get_legend_handles_labels(),loc='lower center',bbox_to_anchor=(.5,.16),ncol=3,frameon=False,fontsize=16)
    fig.add_artist(plt.Line2D([.065,.935],[.145,.145],transform=fig.transFigure,color='#DDE0DD',lw=.7))
    fig.text(.065,.105,'Правило подобрано по этому же 2024. Проверка по прошлым месяцам улучшения не подтвердила.',fontsize=14)
    fig.text(.065,.063,'Результат разработки, не независимая проверка. В рабочую выдачу не подключено.',fontsize=13,color='#626A64')
    fig.text(.065,.028,'Цель и единица оценки отличаются от детектора 2026; показатели нельзя объединять.',fontsize=13,color='#626A64')
    save(fig,'02-ranking-development-2024')
    story=make_story(OUT)
    with (OUT/'appendix/metrics.csv').open('w',encoding='utf-8-sig',newline='') as out:
        writer=csv.writer(out);writer.writerow(['набор','метод','найдено','ложных','пропущено','неизвестно','полнота','точность_сигналов'])
        for n,m in detectors.items():writer.writerow(['2026_после_фиксации',n,m['tp'],m['fp'],m['fn'],'',m['recall'],m['precision']])
        for n,m in zip(['прежний_гибрид','защита_подобрана_2024','выбор_по_прошлым_месяцам'],[old,new,ff]):writer.writerow(['2024_разработка',n,m['tp'],m['fp'],'',m['unknown'],'',''])
    captions='''ONLYFARMS — МАТЕРИАЛЫ ДЛЯ ЗАЩИТЫ
Подготовлено 22 октября 2026. Изображения графиков: PNG 3200×1800 и редактируемый SVG.

КАК ВСТАВИТЬ
Основные материалы: screenshots/01-farm-map.jpg и screenshots/02-satellite-change-ndmi.jpg.
Оба графика модели предназначены только для приложения: charts/01-detector-2026.png и charts/02-ranking-development-2024.png. Они не подтверждают готовность прогнозного продукта. Основные материалы R11 находятся в ../main/.
PNG/SVG графиков уже имеют формат 16:9. Скриншоты вставлять с сохранением пропорций. Не обрезать даты, ограничения источников и пометку режима просмотра. Если весь длинный экран не читается, использовать его на отдельном слайде либо увеличить важный фрагмент с подписью «фрагмент интерфейса».

ГОТОВЫЕ ПОДПИСИ
01. Карта хозяйства. Прототип объединяет 27 приблизительных фрагментов со скриншота и позволяет перейти к выбранной площади. Контуры требуют подтверждения агрономом. Это не 27 подтверждённых полей.
02. Сравнение реальных съёмок Sentinel-2. На R11 между 21.09 и 14.10.2026 наблюдается снижение NDMI −0,119 на общей пригодной части, покрытие 92%. Пользователь видит облачные пропуски и место проверки. Это наблюдаемое изменение за 23 дня, а не недельный предикт модели и не диагноз.
03. Постановка задачи. Вместе с вопросом агроному сохраняются место, даты снимков, версия расчёта и качество источников. На скриншоте пустая форма, задача не создавалась.
04. История места. Фактических посещений пока нет. Прототип готов связывать будущие результаты осмотров с исходным вопросом и спутниковой динамикой.
05. Форма результата в режиме просмотра. Предусмотрены дата, заметка, результат и фотографии реального посещения. Пустые поля не являются проведённым осмотром.

ГРАФИК 1 — ПРОВЕРКА 2026
Исследовательский детектор предсказывает часть резких снижений NDVI/NDMI на следующей пригодной съёмке через 6–8 дней. NDVI: 9 из 50 событий, 10 ложных сигналов; полнота 18%, верных среди сигналов 47,4%. NDMI: 3 из 42, 3 ложных; полнота 7,1%, верных среди сигналов 50%.
Проверка: одинаковые 499 пригодных пар на 27 датах по 26 фрагментам, настройки выбраны по 2024. Обучение NDVI использовало также ранние сезоны. Архив 2026 раньше изучался, поэтому это не полностью слепой независимый тест. Пары и соседние площади зависимы.
Означает: модель уже находит отдельные будущие спектральные события. Не означает: 50% точности состояния хозяйства, обнаружение всех проблем или проверенную пользу выезда. Общая accuracy здесь не используется: она скрыла бы пропуски редких событий.

ГРАФИК 2 — РАЗРАБОТКА 2024
Защита ранжирования вернула три разобранных случая NDMI. По всему проверочному набору результат изменился с 24 найденных / 44 отрицательных / 65 неизвестных до 26 / 42 / 65 при тех же 133 выбранных местах. На конкретной дате заменены три места: возвращены три события, потеряно одно другое верное, исключены два отрицательных.
Правило придумано и выбрано после изучения этого же 2024. Выбор настроек по более ранним месяцам сохранил прежний гибрид и результат 24 / 44 / 65. Этот график показывает ход исследования, а не доказанную точность продукта. Его показатели нельзя смешивать с детектором 2026: другая цель и единица оценки.

ТЕКСТ ДЛЯ УСТНОГО ПОКАЗА — ОКОЛО 40 СЕКУНД
«Мы сделали рабочий прототип на реальных спутниковых данных. Агроном выбирает площадь, сравнивает съёмки с учётом облачных пропусков и формулирует задачу на осмотр. Будущий результат можно сохранить с заметками и фото в истории места. Отдельно мы проверяем прогнозную модель: она уже находит часть резких снижений, но пока пропускает большинство событий. Поэтому сейчас продукт помогает работать с наблюдаемыми изменениями, а пользу приоритизации осмотров мы будем проверять с нашим агрономом».

ЧТО МОЖНО ЗАЯВЛЯТЬ
Рабочий прототип; реальные архивные данные; проверяемый путь от снимка к задаче и результату; исследовательские прогнозы с известными ограничениями; сотрудничество с агрономом — по вашему сообщению.

ЧЕГО ЦИФРЫ НЕ ДОКАЗЫВАЮТ
Экономию средств/времени, предотвращённый ущерб, урожайность, диагноз болезни, фактическую влажность почвы или полезность реальных осмотров. Реальных результатов осмотров в этом комплекте нет. Исследовательские модели не подменяют рабочую выдачу.

ПРОИСХОЖДЕНИЕ
График 1: data/farm-research/continuation-shape/report.json, results.all2026.*.new.metrics — сохранённый исследовательский детектор после проверки фильтров. Это не производственный Ridge и не рейтинг top-3.
График 2: data/farm-research/ndmi-turn-guard-2024/selection-table.json и forward-check.json. Консервативные 368 известных / 331 неизвестный исход на 699 кандидатах, без трёх спорных целей соседних плиток.
Скриншоты сняты с настоящего /scouting 22 октября 2026, без ретуши и изменения данных. Дата архива в интерфейсе — 19.10.2026, сравнение — 21.09–14.10.2026. Дата подложки неизвестна. Формы и история намеренно пустые.
Дизайн рабочего интерфейса /scouting обновлён; скриншоты пересняты с него без ретуши. Модель, пороги, архив и пользовательский журнал сохранены. Исходные хеши и размеры файлов — в manifest.json.
'''
    (OUT/'appendix/MODEL_NOTES.txt').write_text(captions.replace('screenshots/','../screenshots/'),encoding='utf-8')
    (OUT/'CAPTIONS_AND_NOTES.txt').write_bytes((ROOT/'web/defense-materials/CAPTIONS_AND_NOTES.txt').read_bytes())
    records=[]
    descriptions={'01-farm-map.jpg':('Карта хозяйства','Реальная карта, приблизительные контуры и переход к изменению.'),
        '02-satellite-change-ndmi.jpg':('Сравнение снимков','Две реальные даты, карта разности и облачные пропуски.'),
        '03-inspection-task.jpg':('Задача на осмотр','Пустая форма с сохранением основания. Задача не создана.'),
        '04-place-history-empty.jpg':('История места','Настоящее пустое состояние. Осмотров пока нет.'),
        '05-result-form-preview.jpg':('Запись результата','Режим просмотра пустой формы, не фактическое посещение.')}
    for path in sorted((OUT/'screenshots').glob('*.jpg')):
        size=Image.open(path).size;title,note=descriptions[path.name]
        records.append(dict(file=path.relative_to(OUT).as_posix(),title=title,description=note,width=size[0],height=size[1],sha256=sha(path),
            source='http://127.0.0.1:8770/scouting',capture_date_client='2026-10-22',retouched=False,real_visits_created=0))
    for path in sorted(charts.glob('*')):
        records.append(dict(file=path.relative_to(OUT).as_posix(),sha256=sha(path),kind='chart',dimensions=[3200,1800] if path.suffix=='.png' else None))
    for path in sorted((OUT/'main').glob('*')):
        records.append(dict(file=path.relative_to(OUT).as_posix(),sha256=sha(path),kind='main_figure' if path.suffix in ('.png','.svg') else 'evidence',
            dimensions=[3200,1800] if path.suffix=='.png' else None))
    build_gallery(records)
    for name in ('index.html','styles.css','CAPTIONS_AND_NOTES.txt','appendix/index.html','appendix/MODEL_NOTES.txt','appendix/metrics.csv'):
        records.append(dict(file=name,sha256=sha(OUT/name),kind='document'))
    manifest=dict(prepared_client_date='2026-10-22',source_hashes=hashes,files=records,model_fit_performed=False,
        ui_code_modified=False,ui_design_revision=3,gallery_design_revision=3,real_visits_created=0,
        r11_pair=story['pair'],boundary_scenarios=11,story='Observed R11 change, boundary sensitivity and future human inspection workflow',
        notes='Main figures are descriptive, not forecasts or agronomic diagnoses. Research metrics moved to appendix. No model, archive or journal changes in this build.')
    (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    assert all(sha(ROOT/n)==value for n,value in hashes.items())
    for r in records:assert sha(OUT/r['file'])==r['sha256']
    archive=OUT.with_suffix('.zip')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for path in sorted(OUT.rglob('*')):
            if path.is_file():z.write(path,path.relative_to(OUT))
    with zipfile.ZipFile(archive) as z:assert z.testzip() is None
    print(OUT/'index.html');print(archive);print('Source hashes unchanged; charts and screenshots verified.')
