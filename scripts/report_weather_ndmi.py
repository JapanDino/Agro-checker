"""Package actual2024 model weather and report frozen ablations without tuning."""
from pathlib import Path
import sys,json,zipfile,re
from datetime import datetime
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agro_checker.farm_context_data import read,dump,digest
from agro_checker.sector_weather_data import D
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

summary=read(D/'weather-summary-2024.json');loc=read(D/'weather-locations.json');report=read(D/'report.json')
selection=read(D/'selection-table.json');frozen=read(D/'frozen-selection.json');diag=read(D/'diagnostic-report.json');verify=read(D/'verification.json')
out=ROOT/'outputs/01a12f84-40ae-7493-8aee-01bfa7e3ff6e';out.mkdir(parents=True,exist_ok=True)
package=out/'sector-weather-2024.zip'
def readable(text):
    text=re.sub(r'(?<=[А-Яа-яЁё])(?=[0-9A-Za-z])',' ',text)
    text=re.sub(r'(?<=[0-9A-Za-z])(?=[А-Яа-яЁё])',' ',text)
    text=re.sub(r'(?<=%)(?=[А-Яа-яЁё])',' ',text)
    return text.replace('Дата:20','Дата: 20').replace('CatBoost:depth2','CatBoost: depth 2')
files=[D/'weather-locations.json',D/'weather-summary-2024.json']+sorted((D/'sources/2024').glob('*.json'))
manifest=dict(year=2024,model='gfs_global',source='Open-Meteo Historical Forecast API',
    variables=dict(precipitation='mm per hourly API timestamp',soil_moisture_0_to_10cm='m3/m3, modeled layer0-10cm'),
    observed_on_farm=False,confirmed_boundaries=False,historical_delivery_proven=False,
    files=[dict(path=str(p.relative_to(D)).replace('\\','/'),sha256=digest(p)) for p in files])
dump(D/'archive-2024-manifest.json',manifest)
intro='''Погодный фон выбранного сектора,2024. Источник: Open-Meteo Historical Forecast API, явная модель gfs_global.
Три возвращённые расчётные точки, по8784часа, осадки и модельная объёмная влажность слоя0–10см.
Это последовательность первых часов разных выпусков, не один выпуск и не измерения датчика.
Связь с27приблизительными обводками дана вweather-locations.json. Границы хозяйства не подтверждены.
Осадки:mm, почасовая сумма с временной привязкой API. Влажность:m3/m3, мгновенное значение.
UTC, elevation=nan, cell_selection=nearest, автоматическая высотная поправка отключена.
Для ML использовались только метки времени строго раньше issue00UTC−48ч.
Эта задержка—консервативный proxy, не доказательство исторической первой доставки данных.
Исходные запросы, время получения иSHA256 находятся в*.meta.json; контрольные суммы—вMANIFEST.json.
Не подменять этими расчётами полевые измерения влажности/осадков и агрономические диагнозы.
Документация:https://open-meteo.com/en/docs/historical-forecast-api
Улучшение диапазона NDMI в исследовании не подтверждено. Модели/измерения в пакет не добавлены.
'''
with zipfile.ZipFile(package,'w',compression=zipfile.ZIP_DEFLATED) as z:
    for p in files:z.write(p,str(p.relative_to(D)).replace('\\','/'))
    z.write(D/'archive-2024-manifest.json','MANIFEST.json');z.writestr('README.txt',readable(intro).encode('utf-8'))
with zipfile.ZipFile(package) as z:
    for item in manifest['files']:
        import hashlib
        assert hashlib.sha256(z.read(item['path'])).hexdigest()==item['sha256']

fig,axes=plt.subplots(1,2,figsize=(13.5,4.8),layout='constrained')
fig.suptitle('Открытый погодный архив сектора · GFS,2024',fontsize=15,fontweight='bold')
for g in loc['grids']:
    raw=read(D/f'sources/2024/{g["id"]}.json');raw=raw[0] if isinstance(raw,list) else raw;h=raw['hourly']
    dates=[datetime.fromisoformat(d) for d in h['time'][::24]]
    if g['id']=='G02':axes[0].bar(dates,np.array(h['precipitation']).reshape(366,24).sum(axis=1),width=1,color='#639bb5',label='G02: R02–R26')
    axes[1].plot(dates,np.array(h['soil_moisture_0_to_10cm']).reshape(366,24).mean(axis=1),linewidth=1.1,label=g['id'])
axes[0].set_title('Осадки: сумма почасовых значений по меткам дняUTC');axes[0].set_ylabel('мм');axes[0].legend(fontsize=8)
axes[1].set_title('Модельная влажность почвы0–10см: среднее по дню');axes[1].set_ylabel('м³/м³');axes[1].legend(fontsize=8)
for ax in axes:
    ax.grid(axis='y',alpha=.18);ax.spines[['top','right']].set_visible(False)
    ax.set_xlim(datetime(2024,1,1),datetime(2024,12,31))
fig.supxlabel('Три ячейки,8784часа каждая, пропусков нет. Численная модель; локальные осадки и почвенная влажность не измерены.',fontsize=9)
weather_asset=ROOT/'docs/assets/farm-reference/sector-weather-2024.png';fig.savefig(weather_asset,dpi=150,facecolor='white');plt.close(fig)

fig,axes=plt.subplots(1,3,figsize=(13.5,5.5),layout='constrained')
fig.suptitle('Погодные признаки · покрытие резких снижений NDMI не улучшилось',fontsize=14,fontweight='bold')
names=['Без погоды','Осадки','Влажность\nпочвы','Оба фактора'];x=np.arange(4)
for ax,metric,title,scale in [(axes[0],'decline_coverage','Резкие снижения,2026',100),(axes[1],'coverage','Общее покрытие,2026',100),(axes[2],'mean_width','Полная ширина,2026',1)]:
    for offset,group,color,label in [(-.17,'all2026','#2c7b70','Весь сектор'),(.17,'spatial2026','#93a5b1','5 резервных площадей')]:
        values=[diag['results'][group][family+'__quantile']['metrics'][metric]*scale for family in ['none','rain','soil','both']]
        bars=ax.bar(x+offset,values,.34,color=color,label=label);ax.bar_label(bars,fmt='%.1f' if scale==100 else '%.3f',fontsize=8 if scale==100 else 7,padding=3)
    ax.set_xticks(x,names,fontsize=8);ax.set_title(title,fontsize=11);ax.set_ylim(0,110 if scale==100 else .38);ax.legend(fontsize=7,loc='lower left')
    ax.grid(axis='y',alpha=.18);ax.spines[['top','right']].set_visible(False)
fig.supxlabel('Одинаковая квантильная архитектура; все четыре варианта отклонены по2024. Диагностика зафиксирована до загрузки2026, без выбора по тесту.',fontsize=9)
asset=ROOT/'docs/assets/farm-reference/weather-ndmi.png';fig.savefig(asset,dpi=150,facecolor='white');plt.close(fig)

grids=[]
for g in loc['grids']:
    ids=[k for k,value in loc['mapping'].items() if value['grid_id']==g['id']]
    grids.append(f"| {g['id']} | {g['returned'][0]:.6f}, {g['returned'][1]:.6f} | {', '.join(ids)} |")
training=[]
for name,q in selection.items():
    a=q['pooled'];training.append(f"| {name} | {a['decline_coverage']*100:.1f}% | {a['coverage']*100:.1f}% | {a['mean_width']:.3f} | {'прошёл' if not q['rejection_reasons'] else '; '.join(q['rejection_reasons'])} |")
diagnostics=[]
for name,q in diag['results']['all2026'].items():
    a=q['metrics'];b=diag['results']['spatial2026'][name]['metrics']
    diagnostics.append(f"| {name} | {a['decline_coverage']*100:.1f}% / {b['decline_coverage']*100:.1f}% | {a['coverage']*100:.1f}% | {a['mean_width']:.3f} | не прошёл |")
text=f'''# Погодный архив сектора и диапазон NDMI

Дата:20октября2026. **Архив за2024 собран; повышение покрытия резких снижений NDMI от осадков и модельной влажности почвы не подтверждено.** При выборе только по2024 погодные модели не прошли критерий покрытия90%, поэтому основным остался вариант без погоды. Рабочие точечный прогноз, детекторы и диапазоны сохранены.

## Собранные данные

Источник—[Open-Meteo Historical Forecast API](https://open-meteo.com/en/docs/historical-forecast-api), явная модель NOAA GFS (`gfs_global`). Это непрерывный ряд первых часов последовательных выпусков, не единый выпуск, не ERA5 и не измерения метеостанции хозяйства. Данные со скриншотов АгроМон/ECMWF не оцифровывались и не подменяются этим источником.

За1января–31декабря2024 получены **366дней,8784часа в каждой из3возвращённых ячеек**, две переменные без пропусков: почасовые осадки,мм; модельная объёмная влажность почвы0–10см,м³/м³. Всего52704числовыхзначения. UTC, `elevation=nan`, `cell_selection=nearest`: без автоматической высотной поправки и без автоматического выбора смеси моделей. Сохранены точные запросы, исходныеJSON, единицы, время получения иSHA256.

[Готовый архив2024](../outputs/01a12f84-40ae-7493-8aee-01bfa7e3ff6e/sector-weather-2024.zip) содержит три исходных ряда, их метаданные, карту соответствия обводок ячейкам и манифест хешей. [Сводка](../data/farm-research/weather-ndmi/weather-summary-2024.json), [точки выборки и соответствие](../data/farm-research/weather-ndmi/weather-locations.json). Координаты запросов—середины ограничивающих прямоугольников приблизительных обводок; это не координаты датчиков или подтверждение границ.

| ЯчейкаAPI | Возвращённая широта,долгота | Обводки |
|---|---|---|
{chr(10).join(grids)}

**25из27обводок получают одну погодную ячейку.** По [описанию провайдера](https://open-meteo.com/en/docs/historical-forecast-api) GFS имеет примерно13км атмосферной сетки, для ряда переменных используются иные сетки. Возвращённая точкаAPI не удостоверяет точную пространственную поддержку каждой переменной. Погода—фон сектора, а не различие влажности между соседними полями. R18 также имеет погодный ряд, но в спектральный тест не включён из-за недостаточного внутреннего ядра.

![Архив погоды2024](assets/farm-reference/sector-weather-2024.png)

В проверке возможностей за1–3апреля2024 архив GFS с фиксированным упреждением1день дал72/72значения осадков, но0/72значения влажности. Исторический непрерывныйGFS дал72/72обоих. ERA5 также вернул оба поля, ERA5-Land—только влажность; они не смешивались с выбраннымGFS и не использовались для заполнения пропусков. [Single Runs API](https://open-meteo.com/en/docs/single-runs-api) описывает сохранение отдельных GFS-выпусков лишь с апреля2026, поэтому этот ряд не объявлен архивом одного исторического выпуска2024.

## Время доступности и признаки

Для каждого прогноза индекса использованы только погодные значения с timestamp **строго раньше00UTCдатывыдачи минус48часов**. Это консервативный proxy доставки, не доказательство первой исторической публикации. Исходный ряд скачан сейчас, точное время его доступности пользователю в2024 не восстановлено. Такой эксперимент не является доказанным воспроизведением оперативного сервиса.

Добавлены восемь признаков: суммы осадков за3/7/14суток, число дней с суммой≥1мм за14суток; средняя влажность за24часа и7суток, её изменение за7суток и минимум за14суток. Порог1мм—определение входного признака, не агрономическая рекомендация. Почасовые суммы используют временные меткиAPI; график показывает сумму24записей по меткамUTCдня, а не независимый архив суточных измерений.

Погода после даты выбора и погода целевого окна6–8дней не входят в признаки. При отсутствующем часу окно помечается недоступным; осадкиnull не заменяются0. Такой случай оставался бы в проверке с глобальным диапазоном вместо условной модели. В фактических обучающих и тестовых окнах погода полная, исключённых строк и подстановок не было.

После замораживания выбора и диагностических вариантов отдельно получен тот же источник за1января–19октября2026:7008часов в каждой ячейке, обе переменные без пропусков. Эти данные использованы только в тестовых входах, не в выборе. За2025 данные не собирались.

## Обучение и выбор по2024

Средний прогноз—прежняя temporal_ridge_100,217пар/21дата до21июля2024. Для модели ошибок использован прежний подход:182причинныеOOF-ошибки ранних моделей и236ошибок замороженного среднего после завершения его обучения. Ошибки на собственных обучающих примерах не использовались. R23–R27 вне обучения, выбора и калибровки; будущий target не входит в погодные признаки.

Внешние временные блоки—август/сентябрь/октябрь2024; каждый хвост обучается и калибруется только на прошлом, пересекающие границы target исключены. Последние8известныхдат—калибровка; targetобучения хвостов строго раньше её начала. Всего194пары/14дат проверки, лишь11сниженийNDMIна7датах. Эти прогнозы используются для выбора, поэтому их качество не является независимым итоговым тестом. РанниеOOF-ошибки переносятся на фиксированную точку приблизительно; точнойусловнойгарантии нет.

Четыре семейства: без погоды/осадки/влажность/оба фактора. В каждом одинаковые три варианта: глобальная асимметричная оболочка; условные квантильные хвосты с тройным весом снижений; сценарный нижний хвост, обученный на снижениях, с общим верхним. Оболочка не использует погодные признаки и доступна каждому семейству как контроль. Всего12кандидатов плюс прежний диапазон. Фиксированные CatBoost:depth2,120итераций,seed202410; никаких новых параметров по2026.

Требования выбора: покрытие всех случаев и резких снижений≥90% в сумме и в каждом блоке с событиями; средняя ширина≤110%прежней. Среди прошедших—минимальный90%intervalscore с тройным весом снижений. Верхний и нижний хвосты калибруются раздельно; при малом числе событийных дат используется максимум наблюдаемого дефицита. Прежний радиус сам калибровался по2024, его показатели на2024 оптимистичны.

| Вариант | Покрытие снижений | Общее покрытие | Ширина | Допуск по2024 |
|---|---:|---:|---:|---|
{chr(10).join(training)}

Осадки и оба погодных фактора подняли покрытие квантильной модели с78,6%до85,7%, но90%не достигли. Все погодные модели отклонены ещё по2024. Зафиксированный основной выбор **{frozen['primary']}** не использует погоду; во всех семействах победила одинаковая глобальная оболочка. Финальные обучение/калибровка диапазона используют270ошибок/24даты и132ошибки/8дат соответственно; в калибровке только6снижений на3датах.

## Отдельная диагностика до загрузки2026

Поскольку все семейства выбрали один контроль, их четыре тестовых результата не показали бы влияние погоды. До загрузки любого погодного входа2026 сохранено [дополнение к протоколу](../data/farm-research/weather-ndmi/diagnostic-protocol-before-2026.json): проверить все8уже определённых обучаемых вариантов с совпадающими архитектурами и признаками погоды. Новых гиперпараметров или отбора лучшего по2026 нет. Основной выбор не менялся, диагностическим моделям запрещена замена рабочего варианта. Даты замораживания проверены относительно времени получения исходных2026файлов.

Все восемь диагностических вариантов не прошли критерии2024. Их результаты ниже нужны для сравнения факторов, не для выбора по тесту. На тех же499парах/27датах2026,42снижениях на16датах; на резерве102пары/24даты и15снижений на9датах:

| Диагностический вариант | Покрытие снижений:сектор/резерв | Общее покрытие сектора | Ширина сектора | Допуск2024 |
|---|---:|---:|---:|---|
{chr(10).join(diagnostics)}

В квантильной архитектуре осадки и влажность вместе улучшили общее покрытие с91,4%до94,0%, при ширине0,299→0,304. Покрытие резких снижений осталось77,4%на секторе и63,0%на резерве во всех четырёх вариантах. В сценарной архитектуре вариант без погоды имеет80,5%покрытия снижений, погодные варианты—75,8–77,4%; это также не свидетельствует об улучшении от новых факторов. Эти сравнения не устанавливают статистическую значимость и не отменяют отклонения по2024.

![Сравнение погодных факторов](assets/farm-reference/weather-ndmi.png)

Основной выбор остаётся прежней исследовательской оболочкой без погоды: покрытие снижений77,4%/63,0%,ширина0,366против рабочего0,472. Это результат предыдущего эксперимента, а не улучшение от погоды. Рабочий диапазон не заменён. Погодные признаки на этих данных помогают некоторым обычным случаям, но не устраняют нижние ошибки на резких снижениях. Из-за малого числа событий, зависимых дат и общей ячейки нельзя заключить, что погода бесполезна вообще.

## Что сохранено и что можно использовать

Исходные погодные ряды пригодны как воспроизводимый модельный фон и для новых заранее спланированных исследований. Они не подтверждают влажность конкретного поля, ущерб, болезнь, фазу или полезность осмотра. Нельзя показывать модели почвенной влаги как локальные датчики или объяснять любое падениеNDMI погодой без проверки операций и культуры.

Точечные прогнозы обеих величин и сигналы совпадают с прежними на каждой из499пар, границыNDVI также совпадают. Ложные сигналы остаются3NDVI/2NDMI, новых попаданий нет. Нижняя граница не стала сигналом. Прежние модели и рабочий интерфейс сохранены, автоматического допуска нет.

Проверены источник/хеши/единицы/временная ось/пропуски,2024-only,резервныеID,временное разделение,48-часовая отсечка, замораживание до2026, полная когорта, неизменность точки/детекторов/NDVI. Сохранены{verify['protected_files']}прежнихфайлов;FieldStore{verify['field_store_files']}файлов, реальных наблюдений не добавлено. Пройдены68целевыхтестов, графики проверены визуально.

## Воспроизведение

[Протокол](../data/farm-research/weather-ndmi/protocol.json), [таблица выбора](../data/farm-research/weather-ndmi/selection-table.json), [замороженные модели](../data/farm-research/weather-ndmi/frozen-selection.json), [основная проверка](../data/farm-research/weather-ndmi/report.json), [диагностика](../data/farm-research/weather-ndmi/diagnostic-report.json), [верификация](../data/farm-research/weather-ndmi/verification.json).

```text
.venv/Scripts/python.exe -X utf8 scripts/collect_sector_weather.py2024
.venv/Scripts/python.exe -X utf8 scripts/train_weather_ndmi.py protocol
.venv/Scripts/python.exe -X utf8 scripts/train_weather_ndmi.py fit
.venv/Scripts/python.exe -X utf8 scripts/weather_ndmi_diagnostics.py freeze
.venv/Scripts/python.exe -X utf8 scripts/train_weather_ndmi.py evaluate
.venv/Scripts/python.exe -X utf8 scripts/weather_ndmi_diagnostics.py evaluate
.venv/Scripts/python.exe -X utf8 scripts/verify_weather_ndmi.py
.venv/Scripts/python.exe -X utf8 scripts/report_weather_ndmi.py
```

В первой команде требуется пробел между `.py` и `2024`. Fitиdiagnosticfreezeпосле замораживания/получения теста заблокированы; для повторного чтения результатов достаточно verifyиreport. Старый архив не перезаписывать и новый порог по2026не выбирать.
'''
# Avoid a wrapped visual example producing an invalid executable command.
text=text.replace('collect_sector_weather.py2024','collect_sector_weather.py 2024').replace('В первой команде требуется пробел между `.py` и `2024`. ','')
(ROOT/'docs/WEATHER_NDMI_REPORT.md').write_text(readable(text),encoding='utf-8')
print('Weather package:',package,package.stat().st_size,'bytes');print('Report and plots saved')
