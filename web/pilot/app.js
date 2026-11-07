(function () {
  "use strict";
  const $ = id => document.getElementById(id);
  const esc = value => String(value).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const fmt = value => Number.isFinite(value) ? (value < 0 ? "−" : value > 0 ? "+" : "") + Math.abs(value).toLocaleString("ru-RU", {minimumFractionDigits:1,maximumFractionDigits:1}) : "—";
  const temp = value => fmt(value) + (Number.isFinite(value) ? " °C" : "");
  const offsetText = value => Number.isFinite(value) ? value.toLocaleString("ru-RU",{minimumFractionDigits:2,maximumFractionDigits:2}).replace("-","−") + " °C" : "по часам горизонта";
  const shortDate = date => new Date(date + "T12:00:00Z").toLocaleDateString("ru-RU", {day:"numeric",month:"long"});
  const labels = {development:"Обучение · внутри выборки", validation:"Выбор метода · 2024", test:"Отложенная проверка · 2025"};
  let data;
  try { data = JSON.parse($("pilot-data").textContent); }
  catch (_) { $("input-error").hidden = false; $("input-error").textContent = "Нет встроенных данных. Соберите интерфейс командой python -m agro_checker ui."; $("night-dashboard").hidden = true; return; }
  const core = globalThis.AgroRisk;
  const initial = data.nights.find(n => n.date === "2025-03-03") || data.nights[0];
  const state = {year:initial.year,date:initial.date,filter:"all",phase:"",observed:true,hover:null};
  data.config.years.forEach(year => $("year").add(new Option(String(year), String(year))));
  data.thresholds.stages.forEach(stage => $("phase").add(new Option(stage.label, stage.id)));
  $("year").value = state.year;
  $("threshold-source").href = data.thresholds.source_url;
  $("demo-cases").innerHTML = (data.demo_cases || []).map((item,index) => `<button class="demo-case" data-demo="${esc(item.id)}" aria-pressed="false" ${item.available ? "" : "disabled"}><span class="demo-number">0${index+1}</span><span class="demo-case-copy"><strong>${esc(item.title)}</strong><span>${shortDate(item.date)}${item.morning_date ? " → " + shortDate(item.morning_date) : ""} · 2025</span><small>${item.available ? esc(item.scope) : "Нет полных данных в этом архиве"}</small></span></button>`).join("");
  document.querySelectorAll("[data-demo]").forEach(button => button.addEventListener("click", () => {
    const item = data.demo_cases.find(c => c.id === button.dataset.demo);
    if (!item?.available) return;
    state.year = Number(item.date.slice(0,4)); state.date = item.date; state.filter = "all";
    $("year").value = state.year; $("filter").value = "all";
    error(""); render();
  }));

  function available() { return core.filterNights(data.nights, state.year, state.filter); }
  function selected() { return available().find(n => n.date === state.date); }
  function error(message) { $("input-error").hidden = !message; $("input-error").textContent = message || ""; }
  function chooseFromFilter() {
    const nights = available();
    if (!nights.some(n => n.date === state.date)) state.date = nights[0]?.date || "";
    render();
  }
  $("year").addEventListener("change", () => {
    const monthDay = state.date.slice(5) || data.config.season_start;
    state.year = Number($("year").value); state.date = state.year + "-" + monthDay;
    error(""); chooseFromFilter();
  });
  $("filter").addEventListener("change", () => {state.filter = $("filter").value; error(""); chooseFromFilter();});
  $("phase").addEventListener("change", () => {state.phase = $("phase").value; render();});
  $("date").addEventListener("change", () => {
    const date = $("date").value;
    if (!data.nights.some(n => n.date === date && n.year === state.year)) {
      error("Этой даты нет в выбранном весеннем сезоне. Выберите вечер с 1 марта по 31 мая."); $("date").value = state.date; return;
    }
    state.date = date; state.filter = "all"; $("filter").value = "all"; error(""); render();
  });
  $("show-observed").addEventListener("change", () => {state.observed = $("show-observed").checked; render();});
  function navigate(delta) {
    const nights = available(), index = nights.findIndex(n => n.date === state.date);
    if (nights[index + delta]) {state.date = nights[index + delta].date; error(""); render();}
  }
  $("prev").addEventListener("click", () => navigate(-1));
  $("next").addEventListener("click", () => navigate(1));

  function render() {
    const nights = available(), night = selected();
    const demo = (data.demo_cases || []).find(item => item.available && item.date === night?.date);
    document.querySelectorAll("[data-demo]").forEach(button => {
      const active = button.dataset.demo === demo?.id;
      button.classList.toggle("active",active); button.setAttribute("aria-pressed",String(active));
    });
    $("demo-note").hidden = !demo;
    if (demo) {
      $("demo-note-title").textContent = "Пример: " + demo.title;
      $("demo-note-text").textContent = demo.teaching_note;
      $("demo-note-reveal").textContent = state.observed ? "Наблюдения после ночи показаны; исход не влияет на карточку прогнозного риска." : "Фактические значения скрыты. Включите наблюдения на графике для проверки; название примера уже раскрывает его архивный исход.";
    }
    $("date").min = state.year + "-" + data.config.season_start;
    $("date").max = state.year + "-" + data.config.season_end;
    $("date").value = state.date;
    $("empty-state").hidden = Boolean(night); $("night-dashboard").hidden = !night;
    if (!night) return;
    const index = nights.indexOf(night);
    $("prev").disabled = index <= 0; $("next").disabled = index >= nights.length - 1;
    $("split-badge").textContent = labels[night.split];
    $("night-title").textContent = shortDate(night.date) + " → " + shortDate(night.morning_date) + " " + night.year;
    $("night-context").textContent = "Краснодар · станция 34929 · выпуск 09:00 МСК · сценарная выдача 18:00 МСК";
    const stage = data.thresholds.stages.find(s => s.id === state.phase);
    const risk = core.risk(night, stage), observed = core.stats(night.points, "observed");
    $("base-min").textContent = temp(risk.base.min);
    $("base-detail").textContent = risk.base.count + " из " + risk.base.total + " прогнозных отметок · горизонт 10–23 ч";
    $("corrected-min").textContent = temp(risk.corrected.min);
    $("corrected-min").nextElementSibling.textContent = Number.isFinite(data.model.offset_c) ? "Сдвиг минимума " + offsetText(data.model.offset_c) + " · эксперимент" : "Поправки по часовому горизонту · эксперимент";
    $("observed-min").textContent = state.observed ? temp(observed.min) : "Скрыто";
    $("observed-detail").textContent = state.observed ? observed.complete ? "Наблюдения после ночи · ISD / METAR" : observed.count + " из " + observed.total + " наблюдений · минимум не установлен" : "Включите наблюдения на графике для проверки";
    renderRisk(risk, stage);
    renderChart(night, stage);
    renderRetrospective(night);
    $("hourly-body").innerHTML = night.points.map(p => `<tr><td>${p.local_time}</td><td>${fmt(p.base)}</td><td>${fmt(p.corrected)}</td><td>${state.observed ? fmt(p.observed) : "скрыто"}</td><td>${state.observed ? esc(p.source || "нет источника") + (p.observed === null ? " · не допущено" : "") : "скрыто"}</td><td>${p.lead_hours} ч</td></tr>`).join("");
  }

  function renderRisk(risk, stage) {
    const texts = {
      phase_required: ["Нужна фаза", "Уточните фазу яблони", "Погодный прогноз есть. Чтобы сравнить его со справочным порогом для почек и цветков, выберите сценарную фазу выше."],
      incomplete: ["Неполный прогноз", "Оценка ограничена", "Не все часовые значения доступны. Пропуски не заполняются; отрицательные отметки учитываются, но отсутствие отметок не подтверждает отсутствие риска."],
      threshold_reached: ["Порог в прогнозе", "Возможен риск повреждения", "Хотя бы один прогноз достигает справочного порога для выбранной фазы. Это основание проверить условия в саду, а не доказательство повреждения."],
      threshold_not_reached: ["Порог не достигнут", "Ни один прогноз не достигает порога", "По доступным прогнозам справочный порог не достигнут. Это не исключает охлаждение цветков и уязвимых зон сада."],
    };
    const [badge, title, explanation] = texts[risk.status];
    $("risk-status").textContent = badge; $("risk-title").textContent = title; $("risk-explanation").textContent = explanation;
    $("risk-card").className = "card risk-card " + (risk.status === "threshold_reached" || risk.status === "incomplete" ? "alert" : "neutral");
    $("risk-icon").textContent = risk.status === "threshold_reached" ? "!" : "i";
    $("threshold").textContent = stage ? "≈ " + temp(stage.threshold_c) : "Фаза не выбрана";
    $("threshold-stage").textContent = stage ? stage.label + " · сценарий, не журнал сада" : "Порог 0 °C относится только к погоде";
    function hitLine(label, hits) {
      return `<div class="risk-points-row"><strong>${label}: ${hits.length ? "отметок у порога или ниже — " + hits.length : "нет отметок у порога"}</strong>${hits.length ? hits.map(p => p.local_time).join(" · ") + " МСК" : "Почасовой прогноз, не температура цветков"}</div>`;
    }
    $("risk-points").innerHTML = stage ? hitLine("GFS", risk.baseHits) + hitLine("Коррекция", risk.correctedHits) : "";
    const weatherCount = Math.max(risk.weather.base.length, risk.weather.corrected.length);
    $("weather-note").textContent = !risk.complete ? "Прогноз неполный. Нельзя оценить всю ночь." : weatherCount ? "В одном из прогнозов есть температура воздуха ниже 0 °C. Это погодное охлаждение, не универсальный порог для яблони." : "В обоих прогнозах нет часовых значений воздуха ниже 0 °C.";
  }

  function renderRetrospective(night) {
    if (!state.observed) {
      $("retro-title").textContent = "Наблюдения скрыты";
      $("retro-explanation").textContent = "Карточка риска использует только прогнозы. Включите наблюдения после ночи, чтобы проверить результат.";
      $("retro-results").innerHTML = ""; return;
    }
    const result = core.retrospective(night);
    if (!result.available) {
      $("retro-title").textContent = "Полная проверка недоступна";
      $("retro-explanation").textContent = "Для сравнения минимума нужны все 14 прогнозных и наблюдаемых значений. Пропуски видны на графике и в таблице.";
      $("retro-results").innerHTML = ""; return;
    }
    $("retro-title").textContent = result.event ? "Станция зафиксировала воздух ниже 0 °C" : "По полному ряду станции минимум не ниже 0 °C";
    $("retro-explanation").textContent = "Эти наблюдения появились после ночи и не меняют карточку прогнозного риска. Сравнение ниже проверяет погодный порог 0 °C, а не повреждение яблони.";
    const descriptions = {hit:"Охлаждение предсказано",miss:"Охлаждение пропущено",false_alarm:"Ложная тревога по 0 °C",correct_negative:"Верно: порог 0 °C не пересечён"};
    $("retro-results").innerHTML = [["GFS",result.base],["Коррекция",result.corrected]].map(([name,key]) => `<span class="result-badge ${key}">${name} · ${descriptions[key]}</span>`).join("");
  }

  function renderChart(night, stage) {
    const svg = $("chart"), keys = ["base","corrected",...(state.observed ? ["observed"] : [])];
    const values = night.points.flatMap(p => keys.map(k => p[k]).filter(Number.isFinite));
    values.push(0); if (stage) values.push(stage.threshold_c);
    const minimum = Math.min(...values) - 1.5, maximum = Math.max(...values) + 1.5;
    const step = [1,2,5,10,20,50].find(s => s >= (maximum - minimum) / 6) || 50;
    const low = Math.floor(minimum / step) * step, high = Math.ceil(maximum / step) * step;
    const renderedWidth = svg.getBoundingClientRect().width;
    const chartWidth = Math.max(340,Math.round(renderedWidth));
    svg.setAttribute("viewBox",`0 0 ${chartWidth} 340`);
    const box = {left:46,right:chartWidth-20,top:25,bottom:292};
    const scale = chartWidth / Math.max(280,renderedWidth);
    const tickFont = 12 * scale, noteFont = 10 * scale;
    const x = i => box.left + i * (box.right - box.left) / (night.points.length - 1);
    const y = v => box.bottom - (v - low) / (high - low) * (box.bottom - box.top);
    let content = `<title>Температура ночи ${night.date}. Прогноз GFS, коррекция${state.observed ? " и последующие наблюдения станции" : ""}.</title><desc>Пропуски не соединяются. Используйте стрелки для выбора часовой отметки; таблица значений находится под графиком.</desc>`;
    for (let value = low; value <= high; value += step) content += `<line x1="${box.left}" x2="${box.right}" y1="${y(value)}" y2="${y(value)}" stroke="${value === 0 ? "#bac8b1" : "#e9eee4"}" ${value === 0 ? 'stroke-dasharray="5 4"' : ""}/><text x="${box.left-14}" y="${y(value)+4}" text-anchor="end" fill="#8c9b84" font-size="${tickFont}">${value < 0 ? "−"+Math.abs(value) : value}</text>`;
    night.points.forEach((point, index) => {
      if (index % 2 === 0 && index < night.points.length - 2 || index === night.points.length - 1) content += `<text x="${x(index)}" y="322" text-anchor="middle" fill="#8c9b84" font-size="${tickFont}">${point.local_time}</text>`;
      if (point.local_time === "00:00") content += `<line x1="${x(index)}" x2="${x(index)}" y1="${box.top}" y2="${box.bottom}" stroke="#d9e2d2" stroke-dasharray="3 6"/><text x="${x(index)+7}" y="${box.top-7}" fill="#9aaa93" font-size="${noteFont}">${shortDate(night.morning_date)}</text>`;
    });
    if (stage) content += `<line x1="${box.left}" x2="${box.right}" y1="${y(stage.threshold_c)}" y2="${y(stage.threshold_c)}" stroke="#b5673f" stroke-width="1.5" stroke-dasharray="3 5"/><text x="${box.right}" y="${y(stage.threshold_c)-7}" text-anchor="end" fill="#a56a47" font-size="${noteFont}">Справочный порог ≈ ${esc(temp(stage.threshold_c))}</text>`;
    const colors = {base:"#2d6544",corrected:"#c18625",observed:"#467db5"};
    keys.forEach(key => core.segments(night.points,key).forEach(segment => {
      if (segment.length > 1) content += `<path d="${segment.map((p,i) => (i ? "L" : "M") + x(p.index).toFixed(2) + "," + y(p.value).toFixed(2)).join(" ")}" fill="none" stroke="${colors[key]}" stroke-width="2.8" ${key === "corrected" ? 'stroke-dasharray="7 5"' : ""} stroke-linecap="round" stroke-linejoin="round"/>`;
      segment.forEach(p => {content += `<circle cx="${x(p.index)}" cy="${y(p.value)}" r="2.8" fill="white" stroke="${colors[key]}" stroke-width="1.7"/>`;});
    }));
    content += '<g id="chart-cursor" visibility="hidden"><line id="cursor-line" stroke="#9aac91" stroke-dasharray="3 3"/><g id="cursor-dots"></g></g>';
    svg.innerHTML = content; state.hover = null; $("chart-tooltip").hidden = true;
    function inspect(index) {
      state.hover = Math.max(0,Math.min(night.points.length-1,index));
      const point = night.points[state.hover], xx = x(state.hover);
      $("chart-cursor").setAttribute("visibility","visible");
      const line = $("cursor-line"); line.setAttribute("x1",xx); line.setAttribute("x2",xx); line.setAttribute("y1",box.top); line.setAttribute("y2",box.bottom);
      $("cursor-dots").innerHTML = keys.filter(k => Number.isFinite(point[k])).map(k => `<circle cx="${xx}" cy="${y(point[k])}" r="4.5" fill="${colors[k]}" stroke="white" stroke-width="2"/>`).join("");
      const names = {base:"GFS",corrected:"Коррекция",observed:"Станция после ночи"};
      $("chart-tooltip").innerHTML = `<strong>${point.local_time} МСК · ${point.lead_hours} ч от выпуска</strong>` + keys.map(k => `<div class="tooltip-row"><span>${names[k]}</span><b>${Number.isFinite(point[k]) ? esc(temp(point[k])) : "нет данных"}</b></div>`).join("");
      $("chart-tooltip").hidden = false;
      const width = svg.getBoundingClientRect().width;
      $("chart-tooltip").style.left = Math.max(4,Math.min(width - $("chart-tooltip").offsetWidth - 4,xx / chartWidth * width + 12)) + "px";
    }
    svg.onpointermove = event => {
      const coordinate = svg.createSVGPoint(); coordinate.x = event.clientX; coordinate.y = event.clientY;
      const local = coordinate.matrixTransform(svg.getScreenCTM().inverse());
      inspect(Math.round((local.x - box.left) / (box.right - box.left) * (night.points.length - 1)));
    };
    svg.onpointerleave = () => {$("chart-tooltip").hidden = true; $("chart-cursor").setAttribute("visibility","hidden");};
    svg.onkeydown = event => {
      if (event.key === "ArrowRight" || event.key === "ArrowLeft") {event.preventDefault(); inspect((state.hover ?? 0) + (event.key === "ArrowRight" ? 1 : -1));}
      if (event.key === "Escape") {$("chart-tooltip").hidden = true; $("chart-cursor").setAttribute("visibility","hidden");}
    };
  }

  function renderSupportingPages() {
    const baseline = data.scores.identity.test, corrected = data.scores[data.model.name].test;
    const qualityRows = [
      ["MAE ночного минимума",baseline.night_min_mae_c,corrected.night_min_mae_c," °C"],
      ["MAE в холодные ночи",baseline.cold_night_min_mae_c,corrected.cold_night_min_mae_c," °C"],
      ["MAE почасовой температуры",baseline.hourly_mae_c,corrected.hourly_mae_c," °C"],
      ["Пропущенные холодные ночи",baseline.misses,corrected.misses," из "+baseline.paired_cold_nights],
      ["Ложные тревоги",baseline.false_alarms,corrected.false_alarms,""],
    ];
    function plain(v) {return Number(v).toLocaleString("ru-RU",{maximumFractionDigits:2});}
    $("quality-table").innerHTML = '<table><thead><tr><th>Показатель</th><th>Исходный GFS</th><th>Сдвиг минимума</th></tr></thead><tbody>' + qualityRows.map(([name,b,c,suffix]) => `<tr><td>${name}</td><td>${plain(b)}${suffix}</td><td>${plain(c)}${suffix}</td></tr>`).join("") + '</tbody></table>';
    $("source-details").innerHTML = `<div class="source-grid"><div class="source-block"><strong>NOAA GFS · исходный прогноз</strong><p>Выпуск 06:00 UTC. Сетка 0,25°. Ближайшая точка ${data.provenance.grids[0][0]}, ${data.provenance.grids[0][1]} — около ${plain(data.provenance.grids[0][2])} км от станции. Высота не корректируется.</p><p><a href="https://registry.opendata.aws/noaa-gfs-bdp-pds/" target="_blank" rel="noopener noreferrer">NOAA / AWS ↗</a></p></div><div class="source-block"><strong>Meteostat · станция Краснодар 34929</strong><p>45,0333° N, 39,15° E · высота 33 м. В цели допускаются только ISD Lite и METAR; модельное заполнение исключено. Это не измерения в саду.</p><p><a href="https://dev.meteostat.net/data/timeseries/hourly" target="_blank" rel="noopener noreferrer">Описание станционных рядов ↗</a></p></div></div><h3>Время и доступность</h3><p>Ночь 19:00–09:00 МСК, конец исключён. Все 14 часов одной ночи — один выпуск; сценарная выдача в 18:00 МСК. Историческая доступность проверяется по S3 Last-Modified с буфером 10 минут при предположении о публичном доступе в тот момент. Точная первая публикация неизвестна.</p><h3>Коррекция минимума</h3><p>Сдвиг ${esc(temp(data.model.offset_c))}, обучен на ${data.model.trained_nights} полных ночах 2022–2023. Выбран на 2024, проверен на 2025. Сдвиг всей кривой — диагностическая иллюстрация; он ухудшил почасовую ошибку. Наблюдения после ночи не используются для оценки прогнозного риска.</p>`;
    $("provenance").textContent = "Сводка SHA-256:\n" + data.provenance.summary_sha256 + "\n\nПочасовая таблица SHA-256:\n" + data.provenance.hourly_sha256;
  }
  function switchView(view) {
    if (!["archive","quality","sources"].includes(view)) view = "archive";
    ["archive","quality","sources"].forEach(id => {$(id+"-view").hidden = id !== view;});
    document.querySelectorAll(".nav-item").forEach(button => {
      const active = button.dataset.view === view; button.classList.toggle("active",active);
      if (active) button.setAttribute("aria-current","page"); else button.removeAttribute("aria-current");
    });
    $("page-title").textContent = {archive:"Архив ночных заморозков",quality:"Качество прогноза",sources:"Данные и источники"}[view];
    if (view === "archive" && selected()) renderChart(selected(),data.thresholds.stages.find(s => s.id === state.phase));
  }
  document.querySelectorAll(".nav-item").forEach(button => button.addEventListener("click", () => {location.hash = button.dataset.view; switchView(button.dataset.view);}));
  window.addEventListener("hashchange", () => switchView(location.hash.slice(1)));
  window.addEventListener("resize", () => {if (!$("archive-view").hidden && selected()) renderChart(selected(),data.thresholds.stages.find(s => s.id === state.phase));});
  renderSupportingPages(); render(); switchView(location.hash.slice(1));
})();
