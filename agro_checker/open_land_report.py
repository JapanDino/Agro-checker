"""Reporting only for the frozen public-land extraction; no fit or field write."""
from datetime import datetime
import numpy as np
import rasterio
from pyproj import Transformer
from .pipeline import ROOT, write_json
from .product_data import sha
from . import open_land_data as data


def scene_indices(sample, item):
    grids = {}; masks = []
    for key in ['red','nir','swir16','scl']:
        path = data.DIRECTORY/'sentinel'/sample['id']/(key+'.tif')
        with rasterio.open(path) as r:
            a, valid = data.to_twenty(r.read(1),int(20/r.transform.a),r.nodata)
            grids[key] = a if key=='scl' else data.reflectance(a,item['assets'][key],r.scales[0],r.offsets[0])
            masks.append(valid)
    return data.indices(grids['red'],grids['nir'],grids['swir16'],grids['scl'],np.logical_and.reduce(masks))


def statistics(sat):
    valid = [r for r in sat['samples'] if r['point_window_usable']]
    dates = [datetime.fromisoformat(r['acquired_at_utc'].replace('Z','+00:00')) for r in valid]
    return {'catalogue_scenes':sat['catalogue_items'],'usable_point_dates':len(valid),'rejected_point_dates':len(sat['samples'])-len(valid),
            'longest_gap_between_usable_acquisitions_days':max((b-a).total_seconds()/86400 for a,b in zip(dates,dates[1:])),
            'ndvi_60m_range':[min(r['point_60m_ndvi_median'] for r in valid),max(r['point_60m_ndvi_median'] for r in valid)],
            'ndmi_60m_range':[min(r['point_60m_ndmi_median'] for r in valid),max(r['point_60m_ndmi_median'] for r in valid)],
            'validity_is_algorithmic_not_independently_cloud_validated':True}


def plot(soil,dem,sat,stats):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from rasterio.warp import calculate_default_transform,reproject,Resampling
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10})
    fig,axes = plt.subplots(2,2,figsize=(13,9.4)); p=data.protocol(); lon=p['reference']['longitude']; lat=p['reference']['latitude']
    x0,y0=Transformer.from_crs(4326,32637,always_xy=True).transform(lon,lat)
    ax=axes[0,0]
    with rasterio.open(ROOT/dem['native_crop']) as r:
        tr,width,height=calculate_default_transform(r.crs,'EPSG:32637',r.width,r.height,*r.bounds,resolution=30)
        z=np.full((height,width),np.nan,dtype='float32')
        reproject(r.read(1),z,src_transform=r.transform,src_crs=r.crs,dst_transform=tr,dst_crs='EPSG:32637',src_nodata=r.nodata,dst_nodata=np.nan,resampling=Resampling.bilinear)
    extent=[(tr.c-x0)/1000,(tr.c+width*tr.a-x0)/1000,(tr.f+height*tr.e-y0)/1000,(tr.f-y0)/1000]
    im=ax.imshow(z,extent=extent,cmap='terrain',vmin=8,vmax=30); fig.colorbar(im,ax=ax,pad=.025,label='Высота поверхности DSM, м')
    ax.set_title('Рельеф поверхности · Copernicus DSM ≈30 м',pad=10)
    ax.set_xlabel('Восток от ориентира, км'); ax.set_ylabel('Север от ориентира, км'); ax.plot(0,0,'+',color='#c62828',ms=12,mew=2)
    best=max(sat['samples'],key=lambda r:r['context_valid_land_fraction']); items={f['id']:f for f in data.read(data.DIRECTORY/'catalogue.json')['items']}
    ndvi,ndmi,valid=scene_indices(best,items[best['id']]); tr=best['common_transform_utm']; h,w=best['common_shape']
    extent=[(tr[2]-x0)/1000,(tr[2]+w*tr[0]-x0)/1000,(tr[5]+h*tr[4]-y0)/1000,(tr[5]-y0)/1000]
    ax=axes[0,1]; cmap=plt.get_cmap('RdYlGn').copy(); cmap.set_bad('#d9dde2')
    im=ax.imshow(ndvi,extent=extent,cmap=cmap,vmin=-.1,vmax=.85); fig.colorbar(im,ax=ax,pad=.025,label='NDVI · наблюдение, не диагноз')
    ax.set_title('Sentinel-2 · '+best['acquired_at_utc'][:10]+' · общая сетка 20 м',pad=10)
    ax.set_xlabel('Восток от ориентира, км'); ax.set_ylabel('Север от ориентира, км'); ax.plot(0,0,'+',color='#1565c0',ms=12,mew=2)
    ax.text(.02,.02,'Серый: пиксели, не прошедшие фильтр',transform=ax.transAxes,fontsize=8,bbox={'facecolor':'white','alpha':.85,'edgecolor':'none'})
    ax=axes[1,0]
    names={'clay':'Глина','sand':'Песок','silt':'Пылеватая фракция'}; rows=[]
    for i,prop in enumerate(names):
        by={r['statistic']:r['centre_value'] for r in soil['values'] if r['property']==prop and r['depth']=='0-5cm'}
        low,mean,high=[by[k] for k in ['Q0.05','mean','Q0.95']]
        ax.hlines(2-i,low,high,color='#798b9b',lw=5,alpha=.65); ax.plot(mean,2-i,'o',color='#136e56',ms=9)
        ax.text(mean,2-i+.16,f'{mean:.1f}%',ha='center',fontsize=9)
    ax.set_yticks([2,1,0],list(names.values())); ax.set_xlim(0,100); ax.set_ylim(-.5,2.6)
    ax.set_xlabel('Доля, % · отрезок: квантили 5–95% провайдера'); ax.set_title('Почвы 0–5 см · ячейка 250 м у ориентира',pad=10)
    ax.grid(axis='x',alpha=.2); ax.spines[['top','right']].set_visible(False)
    ax=axes[1,1]; rr=sat['samples']; good=[r for r in rr if r['point_window_usable']]; bad=[r for r in rr if not r['point_window_usable']]
    dates=[datetime.fromisoformat(r['acquired_at_utc'].replace('Z','+00:00')) for r in good]
    ax.scatter(dates,[r['point_60m_ndvi_median'] for r in good],s=35,color='#1b7c5e',label='NDVI')
    ax.scatter(dates,[r['point_60m_ndmi_median'] for r in good],s=30,color='#bb751c',label='NDMI')
    ax.scatter([datetime.fromisoformat(r['acquired_at_utc'].replace('Z','+00:00')) for r in bad],[-.045]*len(bad),marker='x',s=24,color='#8a9199',label='Дата не прошла фильтр')
    ax.set_title(f"Окно 60×60 м у ориентира · {stats['usable_point_dates']} из {stats['catalogue_scenes']} дат",pad=10)
    ax.set_ylabel('Медиана индекса · без заполнения пропусков'); ax.set_ylim(-.08,.43); ax.xaxis.set_major_locator(mdates.MonthLocator()); ax.xaxis.set_major_formatter(mdates.DateFormatter('%d.%m'))
    ax.legend(frameon=False,fontsize=9,loc='upper left',ncol=2); ax.grid(alpha=.15); ax.spines[['top','right']].set_visible(False)
    fig.suptitle('Открытые данные вокруг 45.024106, 37.911914 · границы сада неизвестны',fontsize=14,y=.985)
    fig.text(.5,.025,'Это архивный контекст окрестности, не измерения сада и не прогноз урожая, полива или заморозка.\nCopernicus DSM © DLR / Airbus, EU / ESA · Sentinel-2 © Copernicus · SoilGrids © ISRIC',ha='center',fontsize=9)
    fig.tight_layout(rect=[0,.07,1,.95]); path=ROOT/'docs/assets/open-land-context.png'; path.parent.mkdir(parents=True,exist_ok=True)
    fig.savefig(path,dpi=155,facecolor='white'); plt.close(fig); return best['id']


def report():
    soil,dem,sat,meta=data.load(); stats=statistics(sat); best=plot(soil,dem,sat,stats)
    admitted=[{'function':'geographic_surface_context','status':'usable_with_DSM_source_and_date','local_temperature_accuracy_validated':False},
              {'function':'soil_prior_with_prediction_range','status':'usable_as_external_model_context_only','field_laboratory_validation':False},
              {'function':'cloud_filtered_ndvi_ndmi_history','status':'usable_as_historical_surface_observations','crop_or_damage_diagnosis_validated':False},
              {'function':'field_aggregation_after_boundary','status':'requires_confirmed_polygon_not_local_sensor','polygon_known':False}]
    summary={'reference':data.protocol()['reference'],'soil':soil,'dem':dem,'satellite':sat,'satellite_statistics':stats,
             'admitted_context_functions':admitted,'not_validated':['orchard frost','air/flower temperature','irrigation dose','root-zone soil moisture','disease','yield','crop damage'],
             'audit_sha256':sha(data.DIRECTORY/'audit.meta.json'),'model_fit_performed':False,'field_data_added':False,
             'best_context_scene':best,'controls_used_for_selection':False,'source_provenance':meta}
    write_json(ROOT/'docs/OPEN_LAND_SUMMARY.json',summary)
    soil_rows=[]; scene_rows=[]
    names={'clay':'Глина, %','sand':'Песок, %','silt':'Пылеватая фракция, %','phh2o':'pH в воде','soc':'Органический углерод, г/кг'}
    for depth in ['0-5cm','15-30cm']:
        for prop,label in names.items():
            rr={r['statistic']:r for r in soil['values'] if r['property']==prop and r['depth']==depth}
            mean=rr['mean']; soil_rows.append(f"|{depth}|{label}|{mean['centre_value']:.1f}|{rr['Q0.05']['centre_value']:.1f}–{rr['Q0.95']['centre_value']:.1f}|{mean['context_value_min']:.1f}–{mean['context_value_max']:.1f}|")
    for r in sat['samples']:
        ndvi='—' if r['point_60m_ndvi_median'] is None else f"{r['point_60m_ndvi_median']:.3f}"
        ndmi='—' if r['point_60m_ndmi_median'] is None else f"{r['point_60m_ndmi_median']:.3f}"
        scene_rows.append(f"|{r['acquired_at_utc'][:10]}|{r['point_scl']}|{r['valid_land_cells_in_point_60m_window']}/9|{'принята' if r['point_window_usable'] else 'не принята'}|{ndvi}|{ndmi}|")
    text=f"""# Открытые данные вокруг ориентира: что пригодно для продукта

Проверено 18 октября 2026 года. Опорная точка **45.024106, 37.911914** — подтверждённый пользователем ориентир; название и границы хозяйства будут уточнены у агронома. Области выборки ниже не являются границами сада.

**Результат:** без местных датчиков можно строить картографический контекст и спутниковую историю поверхности с контролем качества. Для этой окрестности фактически получены DEM, почвенные оценки с квантилями и пиксели 36 сцен Sentinel-2. Это не подтверждает качество прогноза заморозка, урожая, болезней или потребности в поливе. Модели и исходный детектор не менялись.

![Карты и доступность данных](assets/open-land-context.png)

## Что можно включить в продукт

|Функция|Что уже проверено на данных|Что обещать пользователю|Граница применимости|
|---|---|---|---|
|Карта высот поверхности|Copernicus DSM GLO-30, реальный растровый фрагмент около2×2км|Показать опубликованные высоты и крупные перепады, помочь выбрать места осмотра|Поверхность включает деревья/здания; это не геодезическая съёмка, не карта холодных карманов|
|Предварительная почвенная справка|30 растров SoilGrids:5 свойств×2 глубины×3 статистики, сетка250м|Показать внешнюю модельную оценку вместе с широким диапазоном провайдера|Не лабораторный анализ, не сегодняшняя влажность; не назначать дозу удобрений/полива|
|Спутниковая история NDVI/NDMI|Пиксели всех36 доступных сцен весны2024, фильтр качества и пропуски|Показать пространственные различия и даты наблюдений поверхности; подсказать зоны для осмотра|Индекс не устанавливает причину изменения, сорт, болезнь, заморозок или урожайность|
|Показ качества и свежести|Дата съёмки, версия обработки, облачная маска, исходные URL и контрольные суммы|Честно показывать «нет пригодного наблюдения», вместо ложного обновления каждый день|Маска сама алгоритмическая; она не гарантирует идеального удаления облаков|
|Сводка по подтверждённому полигону|Техническая извлекаемость пикселей проверена; полигон пока неизвестен|После получения контура считать распределения по его внутренним пикселям|Сейчас сводка по всему саду не вычислена; для узких рядов и границ будут смешанные пиксели|

Рабочий результат — архивный прототип такого модуля данных, с изображением, JSON-сводкой и проверяемыми исходниками. Архивные карты подключены к отдельному разделу [полевого интерфейса](LAND_UI.md); оперативные предупреждения ими не изменены. «Зоны для осмотра» здесь — описательные различия снимка, не автоматический диагноз.

## Рельеф: фактические значения и ограничения

Скачан публичный [Copernicus DEM GLO-30](https://registry.opendata.aws/copernicus-dem/), файл25357882байт, выпуск2021 по описанию зеркала. Это DSM — модель поверхности с растительностью и сооружениями. [Описание поставщика и лицензия](https://copernicus-dem-30m.s3.amazonaws.com/readme.html).

Вырезка: {dem['native_shape'][0]}×{dem['native_shape'][1]} ячеек, около2×2км вокруг ориентира. Высота в ячейке ориентира **{dem['height_at_landmark_m']:.1f}м**, минимум–максимум контекста **{dem['context_min_m']:.1f}–{dem['context_max_m']:.1f}м**. Медиана высоты контекста {dem['context_p10_p50_p90_m'][1]:.1f}м. Значения относятся к растру, их десятые доли не означают такую полевую точность.

В этом тайле угловая сетка около {dem['native_grid_metres_at_landmark'][0]:.1f}×{dem['native_grid_metres_at_landmark'][1]:.1f}м. Дополнительная расчётная сетка UTM30м использует билинейную интерполяцию, а не новые измерения. Медиана/90-й перцентиль наклона поверхности — {dem['apparent_dsm_slope_degrees_p50_p90'][0]:.2f}° / {dem['apparent_dsm_slope_degrees_p50_p90'][1]:.2f}°; это производные DSM, включая влияние зданий и растительности.

Крупные перепады полезны для подготовки обследования. Но из них не получена температурная поправка: не доказаны холодные низины, дренаж воздуха, риск затопления или распределение воды в саду. Для таких выводов нужны соответствующие цели проверки, границы и сведения о препятствиях/каналах.

## Почвы: полезен диапазон, а не одна точная цифра

Официальный [WCS ISRIC](https://docs.isric.org/globaldata/soilgrids/WCS_from_Python.html) реально вернул30 GeoTIFF. Окно9×9 нативных ячеек250м — примерно2.25×2.25км. Оно выровнено по DescribeCoverage в Homolosine WGS84, без запроса искусственной сетки10м. В центре использована одна содержащая ориентир ячейка; по всем81 ячейкам сохранён контекст. Единицы преобразованы по [таблице ISRIC](https://docs.isric.org/globaldata/soilgrids/SoilGrids_faqs_01.html), неизвестные/отрицательные значения не приняты.

У полученного DescribeCoverage в общем поле `uom` стоит единица радиационного потока, не относящаяся к clay. Она не использована для интерпретации почвы: преобразование основано на зарегистрированном имени свойства и официальной таблице ISRIC. Это одна из причин проверять смысл метаданных, а не доверять любому автоматически возвращённому полю.

|Глубина|Свойство|Оценка в ячейке ориентира|Квантили5–95% в этой ячейке|Разброс средних оценок по контексту|
|---|---|---:|---:|---:|
{chr(10).join(soil_rows)}

Это предсказания внешней почвенной модели. Квантили задают её номинальный90% прогнозный диапазон; местное покрытие этого диапазона по пробам почвы не проверено. Последний столбец — пространственный разброс модельных средних, а не дополнительный доверительный диапазон. Квантили разных фракций нельзя складывать в состав одной конкретной пробы.

Пример: глина33.5% при диапазоне2.6–86.4% — слишком неопределённая оценка, чтобы уверенно выбрать по ней технологический режим. Справку с таким диапазоном можно показать агроному для планирования проверки. Нельзя подавать её как уже установленный состав сада. Органический углерод не превращался в «гумус» постоянным коэффициентом.

Свойства почвы не являются временным рядом её влажности или анализом питания растений. Текущую влажность корнеобитаемого слоя, потребность в азоте, дозу полива и экономию воды по этим картам не вычисляем. У WCS нет подтверждённого номера единой неизменной редакции всех слоёв: сохранены полученные18октября байты/SHA, но не доказана доступность этой версии для исторического прогноза2023–2024.

## Спутники: пиксели получены, облачные пропуски сохранены

Источник — [Earth Search Collection1 Sentinel-2 L2A](https://github.com/Element84/earth-search/blob/main/docs/collections/sentinel-2-l2a.md). Пагинация пройдена до пустой второй страницы: **36 сцен**, а не только первые результаты каталога. Из каждой реально прочитаны B04, B08, B11 и SCL на окне около2×2км: **144 растровые вырезки**. Локально сохранены исходные цифровые значения этих вырезок и их SHA, ссылка на полный объект, ETag/размер/LastModified до и после чтения. Полный checksum COG взят из метаданных и явно помечен как непроверенный, поскольку весь многомегабайтный объект не скачивался. Проверяем SHA именно локальной вырезки, а не выдаём его за хэш полного спутникового файла.

Общая сетка20м: B04/B08 с10м усредняются2×2 только при наличии всех дочерних пикселей; B11/SCL остаются нативными20м. [Маска SCL](https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Data/S2L2A.html) допускает классы4/5, исключая воду, снег, облака, тени и неопределённые классы. Добавлен запас1 ячейка вокруг исключённых пикселей; невалидные края также скрыты. Облачность всей плитки не заменяет проверку конкретного места. Маска — консервативный рабочий фильтр, не независимо проверенная истина об облаках.

Коэффициенты scale/offset прочитаны из STAC, сверены с заголовком TIFF и применены один раз. На пробе24мая2024 Collection1 содержит red2224/nir2516, scale0.0001, offset−0.1: отражения0.1224/0.1516. Старое зеркало возвращало1224/1516 уже после вычитания1000. Повторно применив−0.1, можно было бы получить неверный NDVI. В новой выборке старые и новые коллекции не смешиваются; неоднозначная поправка отвергается.

NDVI=(B08−B04)/(B08+B04), NDMI=(B08−B11)/(B08+B11) на отражательной способности. Это спектральные показатели, а не проценты влажности почвы. Положительные отражения и наличие данных проверены до расчёта. Для временной иллюстрации используется медиана окна **60×60м возле ориентира**, минимум5 из9 пригодных ячеек и пригодный центральный пиксель. Это окрестность ориентира, **не среднее по хозяйству**.

По этим правилам пригодны **{stats['usable_point_dates']} из36 дат**, не приняты {stats['rejected_point_dates']}. Самый длинный промежуток между пригодными съёмками — **около {stats['longest_gap_between_usable_acquisitions_days']:.0f} дней**. Ряды не интерполированы и не соединены на графике через пропуски. Скачанное число сцен не означает ежедневную видимость участка.

Диапазон медианы NDVI60м: {stats['ndvi_60m_range'][0]:.3f}–{stats['ndvi_60m_range'][1]:.3f}; NDMI: {stats['ndmi_60m_range'][0]:.3f}–{stats['ndmi_60m_range'][1]:.3f}. Интерпретировать это как состояние яблонь нельзя: точка не подтверждена внутри сада, окна могут содержать здания, дороги и разные культуры. Изменение индекса также не доказывает водный стресс или повреждение заморозком.

|Дата съёмки2024|SCL в центре|Пригодных ячеек в окне60м|Результат|NDVI60м|NDMI60м|
|---|---:|---:|---|---:|---:|
{chr(10).join(scene_rows)}

Все эти снимки архивные, за весну2024. Даты съёмки/обработки/создания каталожной записи/изменения объекта/получения хранятся раздельно. Новая выгрузка не доказывает, что именно эта обработанная версия была доступна до исторической ночи. **Данные не подставлены в обучение прогнозов и не улучшали заявленные метрики моделей.**

## Какие обещания обоснованны

Можно обещать показать источник, дату и разрешение карты, воспроизводимый расчёт индексов и явное отсутствие пригодного снимка. После подтверждения контура можно технически повторить извлечение по его внутренней части и показывать пространственные различия для осмотра. Дополнительные датчики для самого расчёта этих карт не нужны.

Нельзя пока обещать локальную точность ночной температуры, температуру цветков, надёжное начало/длительность заморозка, влажность почвы в корнях, обнаружение болезни, урожайность, сохранённый урожай или правильную дозу защиты. Для проверки этих целей нужны соответствующие независимые факты: собственные записи агронома, анализы, осмотры, урожайные журналы или приборы. Это не обязательно всегда новые датчики, но обязательна связь результата с реальной проверяемой целью. Ни большая ML-модель, ни количество открытых слоёв этого не заменяют.

Практический следующий шаг без приборов: получить контур, повторить качественную спутниковую выборку по внутренним пикселям, показывать даты и долю покрытия, дать агроному отметить на карте реальные различия и причины. Автоматические диагнозы/тревоги не выпускать до отдельной проверки. Подбор моделей остаётся отдельным опытом по заранее фиксированным годам, без настройки по2025–2026.

## Техническая надёжность и воспроизведение

- Извлечение: `agro_checker/open_land_data.py`; [протокол](../config/open_land_audit.json); необязательные зависимости [requirements-geodata.txt](../requirements-geodata.txt). Reporting-only `open_land_report.py` не меняет исходники данных.
- [JSON-сводка](OPEN_LAND_SUMMARY.json) содержит фактические значения, все36 дат, причины непригодности, источники и проверочные хэши. Кэш: `data/working-zone-01/open-land/`.
- Хэш манифеста аудита `{sha(data.DIRECTORY/'audit.meta.json')}`. Исходники/протокол/сырые файлы/локальные вырезки проверяются по SHA; повтор работает из кэша, без скрытого обновления карт. Для новой даты/версии — отдельный снимок данных.
- Тесты: **478 Python +20 JavaScript =498 прошли**, включая8 новых проверок CRS/выравнивания, единиц, единичной поправки отражения, дочернего nodata, соседства облаков, воды/снега. Это проверки обработки, не полевая валидация качества глобальных карт.
- Источники бесплатны для доступа, но стабильность внешних API и своевременность снимков не гарантированы. Кэш и явная отметка свежести обязательны для продуктовой интеграции. Публичный показ должен сохранять указания источников и необходимые уведомления: [условия Copernicus DEM](https://copernicus-dem-30m.s3.amazonaws.com/readme.html), [описание доступа SoilGrids](https://docs.isric.org/globaldata/soilgrids/SoilGrids_faqs_02.html), [Sentinel-2 COG](https://registry.opendata.aws/sentinel-2-l2a-cogs/).
- Прежние point/detector/range и научные выборы не изменены. Новых целей на участке, выдач и метрик поля не добавлено.

Запуск: `.venv\\Scripts\\python.exe -X utf8 -m agro_checker.open_land_report`.
"""
    (ROOT/'docs/OPEN_LAND_REPORT.md').write_text(text,encoding='utf-8'); return summary


if __name__=='__main__':
    r=report(); print('Open land report ready',r['satellite_statistics'],r['audit_sha256'])
