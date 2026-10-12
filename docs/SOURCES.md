# Источники исследования

Дата исследования: 2026-10-12. Источники используются для конкретных утверждений, а не как гарантия качества будущего продукта. Документация сервисов, покрытие архивов и условия могут изменяться.

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
- **S12. Meteostat — Krasnodar 34929; Time Series documentation.** Метаданные станции и доступ к годовым CSV. https://meteostat.net/en/station/34929 и https://dev.meteostat.net/data/timeseries
- **S13. NASA — SRTM Quick Guide.** Рельеф с шагом около 30 м. https://www.earthdata.nasa.gov/s3fs-public/2025-05/SRTM_Quick_Guide.pdf?VersionId=ZDrZprebSzTuJdC57vPsRXhuXU3S1OZK
- **S14. Copernicus — Sentinel-2.** Назначение и спектральные характеристики миссии. https://dataspace.copernicus.eu/data-collections/copernicus-sentinel-missions/sentinel-2
- **S20. Open-Meteo — Pricing.** Некоммерческий free API и коммерческие условия. https://open-meteo.com/en/pricing
- **S21. Open-Meteo — Historical Forecast API.** Сшитый ряд первых часов выпусков; не полный отдельный выпуск. https://open-meteo.com/en/docs/historical-forecast-api

## Болезни, вредители и водный режим

- **S15. UC Statewide IPM — Apple Scab.** Температура, увлажнение листа и условия заражения. https://ipm.ucanr.edu/agriculture/apple/apple-scab/
- **S16. UC Statewide IPM — Codling Moth phenology models.** Суммы температур и biofix по ловушкам. https://ipm.ucanr.edu/weather/phenology-models-description/codling-moth/
- **S17. FAO-56 — Chapter 5: Introduction to crop evapotranspiration.** Испарение и коэффициенты культуры. https://www.fao.org/4/X0490E/x0490e0a.htm
- **S18. FAO-56 — Chapter 8: ETc under soil water stress conditions.** Водный баланс и дефицит влаги. https://www.fao.org/4/X0490E/x0490e0e.htm
- **S19. University of Minnesota Extension — How agricultural drainage works.** Переувлажнение, кислород и дренаж. https://extension.umn.edu/natural-resources/conservation/agricultural-soil-and-water/how-agricultural-drainage-works

## Дополнительные материалы и ограничения доступа

- FAO — Recommended Methods of Frost Protection: управление почвой и другие меры. https://www.fao.org/4/y7223e/y7223e08.htm
- Penn State — Frost, Critical Temperatures, and Frost Protection. Поисковый результат доступен, прямое открытие не удалось. https://extension.psu.edu/frost-critical-temperatures-and-frost-protection
- Frost management in agriculture with advanced sensing, modeling, and artificial intelligent technologies: A review (2025). Обнаружено по аннотации/поисковым материалам; полный текст не загрузился. Не использовать как источник неподтверждённых численных выводов. https://www.sciencedirect.com/science/article/pii/S0168169925001334
- ВНИИГМИ-МЦД — температура почвы: возможный источник для дополнительной проверки; страница не загрузилась. https://meteo.ru/data/164-soil-temperature

Исходная презентация хранится отдельно: [agro-case.pdf](../assets/agro-case.pdf). Её экономические цифры и ссылки не прошли независимую проверку в этом исследовании.
