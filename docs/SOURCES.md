# Источники исследования

Дата исследования: 2026-10-12. Источники используются для конкретных утверждений, а не как гарантия качества будущего продукта. Документация сервисов, покрытие архивов и условия могут изменяться.

## Дополнение 18 октября: реально извлечённые данные земли и поверхности

[Отчёт с картами](OPEN_LAND_REPORT.md), [JSON-сводка](OPEN_LAND_SUMMARY.json). Источники: [Copernicus DEM, публичный GLO30](https://registry.opendata.aws/copernicus-dem/) и [описание зеркала/лицензии](https://copernicus-dem-30m.s3.amazonaws.com/readme.html); [SoilGrids WCS](https://docs.isric.org/globaldata/soilgrids/WCS_from_Python.html), [единицы и квантили](https://docs.isric.org/globaldata/soilgrids/SoilGrids_faqs_01.html); [Earth Search Collection1 и радиометрия](https://github.com/Element84/earth-search/blob/main/docs/collections/sentinel-2-l2a.md), [Sentinel-2 SCL/каналы](https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Data/S2L2A.html). Скачан DSM и30 WCS TIFF; получены144 вырезки36 сцен2024 с нативными DN, масками и проверками источника. Пагинация завершена,13 пригодных дат у ориентира и23 непригодных сохранены. Source/crop SHA и remoteobjectversion в кэше/сводке; полный спутниковый checksum явно не выдан за проверенный. Диапазоны почв широкие; точность на поле не проверялась. Модельных fits/оперативных выдач не было,2025–2026 для подбора не использовались.

## Дополнение 17 октября: full-ISD пяти станций и холодный перенос

Загружены полные годовые NOAA ISD 2022–2024 для кодов 345600 (Гумрак), 348610 (Элиста), 372120 (Нальчик), 349490 (Шпаковское), 370540 (Минеральные Воды): 15 gzip, исходные UTC/QC/позиции сохранены. Примеры первичных файлов: [Гумрак2022](https://www.ncei.noaa.gov/pub/data/noaa/2022/345600-99999-2022.gz), [Элиста2023](https://www.ncei.noaa.gov/pub/data/noaa/2023/348610-99999-2023.gz), [Нальчик2024](https://www.ncei.noaa.gov/pub/data/noaa/2024/372120-99999-2024.gz), [Шпаковское2024](https://www.ncei.noaa.gov/pub/data/noaa/2024/349490-99999-2024.gz), [МинеральныеВоды2024](https://www.ncei.noaa.gov/pub/data/noaa/2024/370540-99999-2024.gz). Все URL/SHA/время получения в [JSON-сводке](COLD_STATION_TRANSFER_SUMMARY.json); правила формата — в ранее сохранённой [спецификации NOAA](https://www.ncei.noaa.gov/pub/data/noaa/isd-format-document.pdf). Другие физические позиции из тех же файлов исключены из целей, сохранены отдельно. [Эксперимент](COLD_STATION_TRANSFER_REPORT.md) не использовал 2025–2026 и не подтвердил перенос без роста ложных температурных пересечений. Это станционные наблюдения, не измерения сада.

## Дополнение 17 октября: диагностика и перенос между позициями

[Новый опыт](STATION_TRANSFER_REPORT.md) использует только уже сохранённые полные NOAA ISD 2022–2024, разделённые по фактическим координатам/высоте, и исходный GFS из [предыдущего опыта](VITYAZEVO_SPLIT_REPORT.md). Новых внешних источников, наблюдений фермы или данных 2025–2026 не добавлялось. Различия площадок подтверждены по одинаковым срокам; причинные эффекты моря/рельефа/высоты не установлены. Новый выбор по 2023–2024 предпочёл GFS за пределами проверенной физической позиции; локальная калибровка пока не доказала достаточного выигрыша. Это не независимая проверка на неизвестной станции и не доказательство качества заморозков Анапы.

## Дополнение 17 октября: полный архив Витязево и разделение позиций

- [NOAA full ISD2022](https://www.ncei.noaa.gov/pub/data/noaa/2022/370010-99999-2022.gz), [2023](https://www.ncei.noaa.gov/pub/data/noaa/2023/370010-99999-2023.gz), [2024](https://www.ncei.noaa.gov/pub/data/noaa/2024/370010-99999-2024.gz):52618реальноскачанныхгодовыхстрок;двепозицииразделеныпокоординатам/высоте,UTC/QCсохранены,unknown0. [Результат](VITYAZEVO_SPLIT_REPORT.md) включаетпроверкуполноты,прямоеподтверждениесмешениястарогоISD-Lite,выборRidgeWeather100наairport23/24иплохойпереноснаАнапу. Модельноекачествоэтогодокументанеравнокачествунаферме;контроли25/26нетронуты.

## Дополнение 17 октября: данные вокруг хозяйства с агрономом

- [NOAA ISD, описание](https://www.ncei.noaa.gov/products/land-based-station/integrated-surface-database), [полная спецификация](https://www.ncei.noaa.gov/pub/data/noaa/isd-format-document.pdf), [full2024Витязево/Анапа](https://www.ncei.noaa.gov/pub/data/noaa/2024/370010-99999-2024.gz), [Славянск](https://www.ncei.noaa.gov/pub/data/noaa/2024/349240-99999-2024.gz), [Темрюк](https://www.ncei.noaa.gov/pub/data/noaa/2024/349150-99999-2024.gz). Все3файла реально скачаны, провереныgzip/обязательнаясекция/станция/год/T/QC. Нативные координаты подтверждают2позициивнутри370010; их нельзя объединять. [Аудит доступности](REGIONAL_DATA_FOR_AGRONOM.md).
- [Краснодарский ЦГМС в официальном справочнике Росгидромета](https://www.meteorf.gov.ru/about/structure/cgms/3149/): подтверждён местный официальный контактный канал, без утверждения о наличии готовой цифровойвыгрузкиконкретногоучастка. Сообщения не отправлялись.

## Дополнение 17 октября: официальный маршрут Крымска

- [ВНИИГМИ-МЦД, действующая страница срочных параметров](http://meteo.ru/data/basic-parameters/): официальный формат, UTC и QC0–7. Старый адрес с`163-` возвращает404. QC1/2 прямо названы восстановленными; в основной сверке измеренныхT используетсяQC0.
- [Каталог этого массива, PDF](http://meteo.ru/wp-content/uploads/2024/02/Каталог_Основные_метеорологические_параметры.pdf):521станция,19страниц;37002/Крымск отсутствует в опубликованном списке, но наличие в более полном Госфонде не исключено. Извлечён весь текст, просмотрены страницы18–19.
- [Переход АИСОРИ из официальной страницы](http://aisori-m.meteo.ru/waisori/index.xhtml?idata=2): публичная навигация приводит к логину/паролю. У пользователя пока нет доступа/выгрузки; новые аккаунты и сообщения не создавались.
- [ЕИП4025000218-kod1](https://eip.meteo.ru/opendata/4025000218-kod1): паспорт ссылается наWord, реально скачан пустойXLSX, не наблюдения. Структура содержитQC, но самих37002T нет.
- [Официальная услуга ВНИИГМИ-МЦД](http://meteo.ru/services/data-providing/) и [региональный Отдел фонда данных](https://yugmeteo.donpac.ru/ofd/): маршрут уточнения и запроса архива, без подтверждения конкретногоэкспорта37002. [Отчёт](KRYMSK_OFFICIAL_AUDIT_REPORT.md), [готовый проект запроса](KRYMSK_OFFICIAL_DATA_REQUEST.md). Фактической попарной сверки ещё нет; модельная достоверность не повышается по наличию описания источника.

## Дополнение 17 октября: происхождение Крымска

- [О поставщике](https://www.pogodaiklimat.ru/about.php): для архивов указаны международныеSYNOP и американскиеMETAR; индивидуальная цепочка источника37002 не представлена.
- [FAQ поставщика](https://www.pogodaiklimat.ru/faq.php): заявленыКН-01, центр данных вВашингтоне, декодирование и контрольT с автоматическими/ручными исправлениями; описан преобразованный суточный агрегат, не цель нашей ночи. Это заявление провайдера, исходных сообщений иQC каждой строки у нас нет.
- [Отдельный отчёт](KRYMSK_OBSERVED_TIME_REPORT.md): ссылки на официальный паспорт станции, ограничения доступа, сохранённыеSHA справки/соглашения, точный joinUTC, последовательная проверка2023–2024. Старый детектор сохранён; контрольные цели2025–2026 не использованы.

## Дополнение 17 октября: данные рабочей зоны

- [Полный отчёт и фактические пробы](FARM_DATA_RESEARCH.md), `FARM_DATA_RESEARCH_SUMMARY.json`: исходные URL/SHA/время получения и проверки периода/схемы.
- [Крымск37002, паспорт у держателя архива](https://www.pogodaiklimat.ru/station/37002.htm) и [архив сроков](https://www.pogodaiklimat.ru/weather.php?id=37002). Выгружен исследовательский срез2022–2024; частота3ч, окнаTmin не подтверждены. [Условия держателя](https://www.pogodaiklimat.ru/agreement.php),п3.1/3.5/3.7: личное некоммерческое использование, коммерческое с письменным разрешением, атрибуция при размещении данных. Разрешение для продукта не получено.
- [NOAA GHCN-Daily readme](https://www.ncei.noaa.gov/pub/data/ghcn/daily/readme.txt): fixed-width, шкала0.1°C, QC/source/measurement flags; наши sparseTMIN имеют sourceS(GSOD). [GHCN-Daily каталог](https://www.ncei.noaa.gov/pub/data/ghcn/daily/): inventory не означает непрерывность ряда.
- [IEM METAR download](https://mesonet.agron.iastate.edu/request/download.phtml) и [RU station metadata](https://mesonet.agron.iastate.edu/geojson/network.py?network=RU__ASOS): найдено расхождение географииURKA сNOAAISD/GHCN; не объединено автоматически.
- [NOAA GFS on AWS](https://registry.opendata.aws/noaa-gfs-bdp-pds/): из существующих исходных глобальных GRIB извлечены forecast-only данные фермы2022–2024.
- [Open-Meteo Elevation API](https://open-meteo.com/en/docs/elevation-api), [Copernicus DEM DOI](https://doi.org/10.5270/ESA-c5d3d65): GLO90, обязательная атрибуцияCopernicus/Open-Meteo;25 выборочных высот в районе, не съёмка сада.
- [Copernicus Data Space STAC](https://documentation.dataspace.copernicus.eu/APIs/STAC.html) и [S3 access](https://documentation.dataspace.copernicus.eu/APIs/S3.html):36метаданныхсценS2L2A весны2024; облачность плитки не является pixelcloudmask, пиксели ещё не скачаны; переобработка2026 не заявлена доступной в2024.
- [Open-Meteo Historical Weather](https://open-meteo.com/en/docs/historical-weather-api): пробыERA5Land иERA5 раздельны, null не заменены. [Single Runs](https://open-meteo.com/en/docs/single-runs-api): раннийECMWF2024 указан какIFS49R1hindcast, транспортная проба не делает его историческиоперативным прогнозом.
- [SoilGrids250m](https://docs.isric.org/globaldata/soilgrids/SoilGrids_faqs.html) и [статус доступа/REST](https://docs.isric.org/globaldata/soilgrids/SoilGrids_faqs_02.html): REST приостановлен, локальных значений не получено; растровые данныеCC BY4.0 рассматриваются отдельно.
- [Гидрометцентр: Крымск](https://meteoinfo.ru/pogoda/russia/krasnodar-territory/krymsk) и [каталогРосгидромета](https://eip.meteo.ru/): пути к первичному источнику. Доступ к непрерывной выгрузке/QC/временным окнам ещё не подтверждён.

## Дополнение 13 октября 2026: сценарии и первичные измерения

- **Delle Monache et al., 2013 — Probabilistic Weather Prediction with an Analog Ensemble.** Первичный метод ансамбля аналогов; в проекте собственная адаптация остатками совместных целых ночей, не буквальное повторение статьи. https://journals.ametsoc.org/doi/pdf/10.1175/MWR-D-12-00281.1
- **ECMWF — Reliability.** Разграничение точности и соответствия прогнозных вероятностей наблюдаемым частотам; наше покрытие не подтверждает калибровку. https://www.ecmwf.int/en/forecasts/datasets/reliability
- **Meteostat — Parameters.** Метрические единицы, включая wind km/h; преобразование требует сверки фактического источника. https://dev.meteostat.net/parameters
- **Iowa Environmental Mesonet — ASOS/METAR archive, API.** Исходные тексты URKK, где MPS/KT/KMH указаны явно; ограниченный QC самого архива. Использованы для выявления смешанного масштаба агрегированного ветра2026 и отдельной первичной оценки без изменения модели. https://mesonet.agron.iastate.edu/request/download.phtml и https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?help

## Заморозки и защита

- **S01. Met Office — How we measure temperature.** Различия температуры воздуха, поверхности и почвы. https://weather.metoffice.gov.uk/guides/observations/how-we-measure-temperature
- **S02. Penn State Extension — Orchard Frost: Critical Temperatures for Various Fruits.** Фазы, доли повреждения, 30 минут экспозиции. Поисковый результат был доступен; прямое открытие возвращало 403. https://extension.psu.edu/orchard-frost-critical-temperatures-for-various-fruits
- **S03. FAO — Frost Protection, Volume 1, Chapter 1.** Радиационные и адвективные события, физика и определения. https://www.fao.org/4/y7223e/y7223e07.htm
- **S04. FAO — Chapter 4: Frost Damage, Physiology and Critical Temperatures.** Зависимость повреждения от культуры и стадии, ограничения справочных порогов. https://www.fao.org/4/y7223e/y7223e0a.htm
- **S05. FAO — Chapter 5: Frost Forecasting and Monitoring.** Локальный прогноз, калибровка, связь прогноза с решениями. https://www.fao.org/4/y7223e/y7223e0b.htm
- **S06. FAO — Chapter 7: Active Protection Methods.** Ветрогенераторы, инверсия, дождевание, влажный термометр, ограничения и эффект дыма. https://www.fao.org/4/y7223e/y7223e0d.htm

## ML и погодные данные

- **S07. Intelligent Spatial Interpolation-based Frost Prediction Methodology using Artificial Neural Networks with Limited Local Data (2022).** Первичное исследование: 75 станций в Австралии, рельеф, NDVI, прогноз следующего часа. https://arxiv.org/abs/2204.08465
- **S08. Copernicus Climate Data Store — ERA5-Land hourly data from 1950 to present.** Покрытие, шаг, почвенные слои, лицензия. https://cds.climate.copernicus.eu/datasets/reanalysis-era5-land?tab=overview
- **S09. Open-Meteo — Historical Weather API.** Реанализы, параметры, доступ и задержки. https://open-meteo.com/en/docs/historical-weather-api
- **S10. Open-Meteo — Previous Runs API.** Фиксированная заблаговременность 1–7 дней и покрытие архива. https://open-meteo.com/en/docs/previous-runs-api
- **S11. Open-Meteo — Single Runs API.** Выпуски по моменту инициализации; архив для воспроизведения одного предупреждения. https://open-meteo.com/en/docs/single-runs-api
  - Уточнение при реализации 2026-10-12: ранний архив ECMWF с 2024-03-14 описан как IFS Cycle 49R1 hindcasts. Перед исторической оперативной проверкой необходимо установить происхождение прогноза; наличие `run` не подтверждает фактическую доступность в прошлом.
- **S12. Meteostat — Krasnodar 34929; Time Series documentation.** Метаданные станции и доступ к годовым CSV. https://meteostat.net/en/station/34929 и https://dev.meteostat.net/data/timeseries
- **S13. NASA — SRTM Quick Guide.** Рельеф с шагом около 30 м. https://www.earthdata.nasa.gov/s3fs-public/2025-05/SRTM_Quick_Guide.pdf?VersionId=ZDrZprebSzTuJdC57vPsRXhuXU3S1OZK
- **S14. Copernicus — Sentinel-2.** Назначение и спектральные характеристики миссии. https://dataspace.copernicus.eu/data-collections/copernicus-sentinel-missions/sentinel-2
- **S20. Open-Meteo — Pricing.** Некоммерческий free API и коммерческие условия. https://open-meteo.com/en/pricing
- **S21. Open-Meteo — Historical Forecast API.** Сшитый ряд первых часов выпусков; не полный отдельный выпуск. https://open-meteo.com/en/docs/historical-forecast-api
- **S22. NOAA Global Forecast System — AWS Open Data Registry.** Исходные оперативные выпуски NOAA GFS; публичный bucket `noaa-gfs-bdp-pds`, выпуск каждые 6 часов, условия использования и атрибуция. Архив 2022–2025 проверен прямыми запросами в реализации. https://registry.opendata.aws/noaa-gfs-bdp-pds/
- **S23. AWS S3 — Working with object metadata.** Значение Last-Modified текущего объекта; это метаданные записи, не независимый журнал первой публичной доступности или истории ACL. https://docs.aws.amazon.com/AmazonS3/latest/userguide/UsingMetadata.html
- **S24. ECMWF — ecCodes Python.** Декодирование/проверка GRIB2, чтение метаданных и ближайшей точки сетки; в окружении пилота используется 2.49.0. https://github.com/ecmwf/eccodes-python
- **S25. NOAA/NCEP — GFS Products Inventory.** Названия файлов, циклы, горизонты и каталог параметров исходного GFS 0.25°. В расширенном эксперименте индексы и реальные GRIB подтверждают UGRD/VGRD на 10 м, DPT на 2 м и мгновенный TCDC всей атмосферы. https://www.nco.ncep.noaa.gov/pmb/products/gfs/

## Болезни, вредители и водный режим

- **S15. UC Statewide IPM — Apple Scab.** Температура, увлажнение листа и условия заражения. https://ipm.ucanr.edu/agriculture/apple/apple-scab/
- **S16. UC Statewide IPM — Codling Moth phenology models.** Суммы температур и biofix по ловушкам. https://ipm.ucanr.edu/weather/phenology-models-description/codling-moth/
- **S17. FAO-56 — Chapter 5: Introduction to crop evapotranspiration.** Испарение и коэффициенты культуры. https://www.fao.org/4/X0490E/x0490e0a.htm
- **S18. FAO-56 — Chapter 8: ETc under soil water stress conditions.** Водный баланс и дефицит влаги. https://www.fao.org/4/X0490E/x0490e0e.htm
- **S19. University of Minnesota Extension — How agricultural drainage works.** Переувлажнение, кислород и дренаж. https://extension.umn.edu/natural-resources/conservation/agricultural-soil-and-water/how-agricultural-drainage-works

## Дополнительные материалы и ограничения доступа

- **S25. Utah State University Extension — Critical Temperatures for Frost Damage on Fruit Trees, IPM-012-11, March 2020.** Таблица WSU для стадий яблони, страница 1; исходные °F и 30-минутная экспозиция. Использована для справочных сценариев интерфейса, не для предсказания ущерба сада. https://extension.usu.edu/productionhort/files/CriticalTemperaturesFrostDamageFruitTrees.pdf

- FAO — Recommended Methods of Frost Protection: управление почвой и другие меры. https://www.fao.org/4/y7223e/y7223e08.htm
- Penn State — Frost, Critical Temperatures, and Frost Protection. Поисковый результат доступен, прямое открытие не удалось. https://extension.psu.edu/frost-critical-temperatures-and-frost-protection
- Frost management in agriculture with advanced sensing, modeling, and artificial intelligent technologies: A review (2025). Обнаружено по аннотации/поисковым материалам; полный текст не загрузился. Не использовать как источник неподтверждённых численных выводов. https://www.sciencedirect.com/science/article/pii/S0168169925001334
- ВНИИГМИ-МЦД — температура почвы: возможный источник для дополнительной проверки; страница не загрузилась. https://meteo.ru/data/164-soil-temperature

Исходная презентация хранится отдельно: [agro-case.pdf](../assets/agro-case.pdf). Её экономические цифры и ссылки не прошли независимую проверку в этом исследовании.

## Неопределённость модели

- **Xu C., Xie Y. Sequential Predictive Conformal Inference for Time Series, ICML2023, PMLR202.** [Первичная статья](https://proceedings.mlr.press/v202/xu23r/xu23r.pdf). Введение и раздел1.1 объясняют ограничение обычной split-conformal гарантии обменностью и зависимость ошибок во временных рядах. В пилоте используется простой split residual envelope на отдельном сезоне, а не SPCI из статьи; её результаты не являются гарантией для станции или сада.
## Условный масштаб и хвосты ошибки

- Romano Y., Patterson E., Candès E. Conformalized Quantile Regression, NeurIPS2019. [Первичная статья](https://papers.neurips.cc/paper_files/paper/2019/file/5103c3584b063c431bd1268e9b5e76fb-Paper.pdf). Использован принцип квантильных границ с калибровочной поправкой; гарантии обменяемой выборки не заявлены для зависимых погодных ночей и настройки по калибровочному году.
- scikit-learn: [QuantileRegressor](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.QuantileRegressor.html). Первичная документация pinball loss, L1-регуляризации и solver highs. Численное качество пилота установлено собственным сравнением, не заимствовано из документации.

## Расширение обучающего архива до2021

- [NOAA GFS на AWS](https://registry.opendata.aws/noaa-gfs-bdp-pds/): источник исходных GFSGRIB; конкретные доступность/поля подтверждены собственными индексами и range-download, не общей страницей. [Локальный аудит](ARCHIVE_EXTENSION_AUDIT.json).
- [NSF NCAR d084001](https://gdex.ucar.edu/datasets/d084001/): оригинальный исторический0.25° GFS с2015, описание указывает3-hourly forecasts. В наш почасовой эксперимент не включён.
- [NOAA/EMC: реализации GFS](https://www.emc.ncep.noaa.gov/emc/pages/numerical_forecast_systems/gfs/implementations.php): GFSv16 введён22марта2021 в12UTC. Проверено расхождение instantaneous/averaged cloud в оригинальных индексах; в новом семействе облачность исключена, а не заменена.

## Реестр крупных переходов GFS для весов и поправок

- [NOAA/EMC: GFSv16](https://www.emc.ncep.noaa.gov/emc/pages/numerical_forecast_systems/gfs/implementations.php): переход с выпуска22марта2021 12UTC.
- [NWS SCN22-104Updated](https://www.weather.gov/media/notification/pdf2/scn22-104_gfs.v16.3.0_aaa.pdf): переход16.2.2→16.3.0 с выпуска29ноября2022 12UTC; обновления включают land-surface/snow иpostprocessing. В пилоте используется укрупнённый режим после этого обновления, а не точный реестр всехpatches.
- [Официальная верификацияGFS](https://www.emc.ncep.noaa.gov/users/verification/global/gfs/ops/main.php): справка об operationalv16.3 с29ноября2022. Для будущих выпусков за пределамипроверенногоокна не используется предположение о прежнейверсии.

