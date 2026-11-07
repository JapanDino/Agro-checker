'use strict';
(async () => {
  const $ = id => document.getElementById(id), NS = 'http://www.w3.org/2000/svg';
  const S = {step:1, fid:'R11', metric:'ndmi', detail:null, data:null, geometry:null, relative:null, selectedLocation:null, placeComparison:null, task:null, series:null, busy:false, locationPending:false, visitPreview:false, comparison:null, commonBusy:false};
  const drafts = new Map();let detailRun = 0, historyRun = 0, locationRun = 0, routeRun = 0, draftKey = '', taskRequest = '', visitRequest = '', commonRun = 0;
  const date = v => new Date(v).toLocaleDateString('ru-RU',{timeZone:'Europe/Moscow'});
  const datetime = v => new Date(v).toLocaleString('ru-RU',{timeZone:'Europe/Moscow',dateStyle:'medium',timeStyle:'short'});
  const num = (v,d=3) => typeof v === 'number' && Number.isFinite(v) ? v.toFixed(d).replace('.',',') : '—';
  const signed = v => typeof v === 'number' ? (v>0?'+':'')+num(v) : '—';
  const daySpan = value => {const n=Math.round(value),r=n%100;return `${n} ${r>=11&&r<=14?'дней':n%10===1?'день':n%10>=2&&n%10<=4?'дня':'дней'}`;};
  const uid = () => crypto.randomUUID().replaceAll('-','');
  const el = (tag,text,parent,cls) => {const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;if(parent)parent.appendChild(n);return n;};
  const svgEl = (tag,attrs,parent,text) => {const n=document.createElementNS(NS,tag);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,v);if(text!==undefined)n.textContent=text;if(parent)parent.appendChild(n);return n;};
  const message = (text,error=false) => {$('message').textContent=text;$('message').classList.toggle('error',error);$('message').setAttribute('role',error?'alert':'status');$('message').hidden=!text;};
  const routes=['','map','changes','task','history','plan'];
  function writeRoute(replace=false){
    if(!S.data)return;
    const query=new URLSearchParams({area:S.fid,period:$('period').value,metric:S.metric});
    if(S.comparison?.pair){query.set('before',S.comparison.pair.before.id);query.set('after',S.comparison.pair.after.id);}
    if(S.step===4&&S.task)query.set('task',S.task.id);
    const hash='#'+routes[S.step]+'?'+query;
    if(location.hash!==hash)history[replace?'replaceState':'pushState'](null,'',hash);
  }
  function context(){
    if($('period-mobile'))$('period-mobile').value=$('period').value;
    $('context-area').textContent=S.fid;$('focus-label').textContent=S.fid;
    $('context-period').textContent=S.data?.periods.find(p=>p.id===$('period').value)?.label.replace('Последние 90 дней 2026','Июль–октябрь 2026')||'Архив';
    $('mobile-action').hidden=!S.data;$('start-demo').disabled=!S.data||S.busy||S.commonBusy;
    const copy={1:[`Площадь ${S.fid}`,'Архивное сравнение','Смотреть снимки →'],2:[`${S.fid} · окно 60 м`,S.detail?.pair?`Архив · ${daySpan(S.detail.pair.days)} между датами`:'Проверяем снимки','Создать задачу →'],3:[`Задача · ${S.fid}`,'Сохраняем вопрос и основание','Сохранить →'],4:[`История · ${S.fid}`,S.task?'Выбранное место':'Посещений пока нет',S.task?'Записать результат':'Выбрать место →'],5:['План обследования','Задачи и места для выезда','Сохранить план →']}[S.step];
    $('mobile-action-title').textContent=copy[0];$('mobile-action-note').textContent=copy[1];$('mobile-primary').textContent=copy[2];
    $('mobile-primary').disabled=S.busy||S.commonBusy||(S.step===5&&!S.planner?.canSave())||((S.step===1||S.step===2||S.step===3)&&(!S.detail?.pair||S.locationPending));
  }
  async function api(path,body){
    const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Field-Token':S.data.token},body:JSON.stringify(body)});
    const data=await response.json();if(!response.ok)throw Error(data.error||'Не удалось выполнить запрос');return data;
  }
  function saveDraft(){if(S.step===3&&draftKey)drafts.set(draftKey,{actor:$('task-actor').value,date:$('task-date').value,objective:$('task-objective').value,request:taskRequest});}
  function go(step,recordRoute=true){
    if(S.busy)return;
    if(!S.data){message('Загружаем архив. Разделы откроются через несколько секунд.');return;}
    if((step===2||step===3)&&S.commonBusy){message('Рассчитываем общую пару снимков. Дождитесь результата.');return;}
    if(step===3&&S.locationPending){message('Проверяем выбранное место. Дождитесь привязки к карте.');return;}
    if((step===2||step===3)&&!S.detail?.pair){message('Для этой площади пока нет пригодной пары снимков выбранного периода. Можно выбрать другую площадь или период.',true);return;}
    if(recordRoute)++routeRun;
    message('');
    saveDraft();
    if(step===3){prepareTask();taskReadiness();}
    S.step=step;
    document.body.dataset.stage=String(step);
    for(const n of document.querySelectorAll('[data-panel]'))n.hidden=Number(n.dataset.panel)!==step;
    for(const n of document.querySelectorAll('[data-step]')){if(Number(n.dataset.step)===step)n.setAttribute('aria-current','step');else n.removeAttribute('aria-current');}
    if(step===1){renderAreaList();highlightMap();if(['ndvi','ndmi'].includes($('map-layer').value))S.explorer?.setLayer(S.metric);}
    if(step===4)renderHistory();if(step===5)S.planner?.render();
    $('page-title').textContent=['','Карта хозяйства','Сравнение снимков','Задача на осмотр','История места','План обследования'][step];
    document.title=`${S.fid} · ${$('page-title').textContent} · AgroPulse`;
    $('page-subtitle').textContent=['','От спутникового изменения к вопросу агроному и проверке на месте.','Сравните две даты и выберите место, которое хотите проверить.','Сохраните место, ответственного и вопрос для проверки на месте.','Заметки и фото реальных посещений рядом со спутниковыми наблюдениями.','Объедините вопросы и места в проверяемый план выезда.'][step];
    context();if(step===1)S.explorer?.resize();if(recordRoute)writeRoute();
    window.scrollTo({top:0,behavior:'instant'});
    if(recordRoute){const heading=$(step===1?'farm-heading':'page-title');heading.setAttribute('tabindex','-1');heading.focus({preventScroll:true});}
  }
  $('mobile-primary').addEventListener('click',()=>{
    if(S.step===5){$('plan-form').requestSubmit($('save-plan'));return;}if(S.step===1)go(2);else if(S.step===2)go(3);else if(S.step===3)$('task-form').requestSubmit($('task-form').querySelector('button[type="submit"]'));else if(S.task)openVisit();else go(2);
  });
  for(const n of document.querySelectorAll('[data-step]'))n.addEventListener('click',()=>go(Number(n.dataset.step)));
  for(const n of document.querySelectorAll('[data-go]'))n.addEventListener('click',()=>go(Number(n.dataset.go)));
  $('open-change').addEventListener('click',()=>go(2));$('prepare-task').addEventListener('click',()=>go(3));
  $('open-area-history').addEventListener('click',()=>{S.task=null;go(4);});
  $('repeat-task').addEventListener('click',async()=>{
    if(S.task){const t=S.task.payload,crop=S.detail?.pair?.crop;if(crop&&!await chooseLocation([(t.place.utm_x-crop.west)/(crop.width*20),(crop.north-t.place.utm_y)/(crop.height*20)]))return;}
    draftKey='';go(3);
  });
  function journalCounts(){
    $('area-journal-count').textContent=`${S.data.tasks.filter(t=>t.payload.place.fragment===S.fid).length} / ${S.data.visits.filter(t=>t.payload.place.fragment===S.fid).length}`;
  }
  function renderAreaList(){
    $('area-list').replaceChildren();const search=$('area-search').value.toUpperCase().trim(),rows=S.comparison?.rows||[],lookup=new Map(rows.map(r=>[r.id,r]));
    const items=S.data.items.filter(v=>v.id.includes(search)).slice(),order=$('area-sort').value;
    if(order==='change')items.sort((a,b)=>(lookup.get(a.id)?.[S.metric]?.base??Infinity)-(lookup.get(b.id)?.[S.metric]?.base??Infinity)||a.id.localeCompare(b.id));
    if(order==='quality')items.sort((a,b)=>(lookup.get(b.id)?.coverage??-1)-(lookup.get(a.id)?.coverage??-1)||a.id.localeCompare(b.id));
    for(const item of items){
      const button=el('button',undefined,$('area-list'),'area-row');button.type='button';button.dataset.fid=item.id;button.setAttribute('aria-pressed',String(item.id===S.fid));
      const snapshot=lookup.get(item.id),value=snapshot?.[S.metric]?.base;
      const span=el('span',undefined,button);el('i',undefined,span,'area-dot'+(snapshot?.status!=='ready'?' empty':''));el('b',item.id,span);
      const hint=el('small',S.commonBusy?'…':typeof value==='number'?'Δ '+signed(value):'Нет сравнения',button);
      const pair=S.comparison?.pair;if(pair)hint.title=`${date(pair.before.at)} → ${date(pair.after.at)} · ${snapshot?.reason||S.metric.toUpperCase()}${snapshot?.coverage!=null?' · '+Math.round(snapshot.coverage*100)+'%':''}`;
      button.addEventListener('click',()=>{if(!S.commonBusy)selectArea(item.id,true);});
    }
    if(!$('area-list').children.length)el('p','Не нашли площадь. Попробуйте код R01–R27.',$('area-list'),'search-empty');
    const active=$('area-list').querySelector('[aria-pressed="true"]');
    if(active){const list=$('area-list'),r=active.getBoundingClientRect(),b=list.getBoundingClientRect();list.scrollTop+=r.top-b.top-list.clientHeight/2+r.height/2;}
  }
  $('area-sort').addEventListener('change',renderAreaList);
  function commonControls(){
    const c=S.comparison;if(!c)return;
    for(const [id,side]of [['common-before','before'],['common-after','after']]){
      const target=$(id);target.replaceChildren();for(const scene of c.scenes){const option=el('option',`${date(scene.at)} · ${scene.usable_areas}/27`,target);option.value=scene.id;option.disabled=scene.usable_areas===0;}
      if(c.pair)target.value=c.pair[side].id;
    }
    $('common-quality').textContent=c.pair?`Данные: ${c.available_areas} из ${c.total_areas} площадей · ${daySpan(c.pair.days)}`:'Нет общей пары';
    $('common-quality').title='Пригодные сравнения на одинаковых датах. Это полнота данных, не точность модели. Даты по умолчанию выбираются по покрытию, версии и интервалу, без подбора величины изменения.';
  }
  async function loadCommon(before,after,route=true){
    const run=++commonRun;++detailRun;++locationRun;S.commonBusy=true;S.detail=null;S.relative=null;S.task=null;S.series=null;
    $('apply-common').disabled=true;$('open-change').disabled=true;$('prepare-task').disabled=true;$('selected-dates').textContent='Проверяем общие даты…';$('selected-ndvi').textContent='—';$('selected-ndmi').textContent='—';$('selection-summary').textContent='';$('selection-quality').textContent='';$('common-quality').textContent='Проверяем…';context();
    S.explorer?.setComparison({rows:[]});
    try{
      const q=new URLSearchParams({period:$('period').value});if(before&&after){q.set('before',before);q.set('after',after);}
      const data=await api('/api/scouting/workspace?'+q);if(run!==commonRun)return;
      S.comparison=data;commonControls();S.explorer?.setComparison(data);renderAreaList();
      await loadDetail();if(run!==commonRun)return;if(route)writeRoute(true);
    }catch(error){if(run!==commonRun)return;S.comparison=null;renderAreaList();if(S.step===2||S.step===3)go(1,false);message(error.message,true);$('common-quality').textContent='Сравнение недоступно';$('selected-dates').textContent='Нет сравнения';}
    finally{if(run===commonRun){S.commonBusy=false;$('apply-common').disabled=false;context();}}
  }
  $('apply-common').addEventListener('click',()=>loadCommon($('common-before').value,$('common-after').value));
  function showAside(name){
    for(const n of document.querySelectorAll('[data-aside-panel]'))n.hidden=n.dataset.asidePanel!==name;
    for(const n of document.querySelectorAll('[data-aside]'))n.setAttribute('aria-pressed',String(n.dataset.aside===name));
  }
  for(const n of document.querySelectorAll('[data-aside]'))n.addEventListener('click',()=>{showAside(n.dataset.aside);if(n.dataset.aside==='sensors')S.explorer?.setLayer('sensors');if(n.dataset.aside==='change')S.explorer?.setLayer(S.metric);});
  $('toggle-details').addEventListener('click',()=>{const closed=$('toggle-details').closest('.selection-card').classList.toggle('mobile-collapsed');document.body.classList.toggle('inspector-open',!closed);$('toggle-details').setAttribute('aria-expanded',String(!closed));$('toggle-details').textContent=closed?'Подробнее ↑':'Свернуть ↓';});
  $('toggle-area-list').addEventListener('click',()=>{const open=document.body.classList.toggle('map-list-open');$('toggle-area-list').setAttribute('aria-expanded',String(open));});
  document.addEventListener('keydown',e=>{if(e.key==='Escape'){document.body.classList.remove('map-list-open');$('toggle-area-list').setAttribute('aria-expanded','false');}});
  async function selectCandidate(site){
    if(!S.detail?.pair||S.busy)return;
    const c=S.detail.pair.crop;const ok=await chooseLocation([(site.utm_x-c.west)/(c.width*20),(c.north-site.utm_y)/(c.height*20)]);
    if(ok){S.explorer?.showSite(S.selectedLocation);go(2);}
  }
  function renderPublicContext(){
    const v=S.public?.features[S.fid],target=$('public-conditions');target.replaceChildren();$('candidate-sites').replaceChildren();if(!v)return;
    const row=(parent,label,value)=>{const n=el('div',undefined,parent,'public-stat');el('span',label,n);el('strong',value,n);};
    el('h4','Поверхность и почва',target);
    row(target,'Высота DSM',num(v.dsm.height.base,1)+' м');
    row(target,'Наклон, 90-й перцентиль',num(v.dsm.slope.base,1)+'°');
    for(const[prop,label,unit]of [['clay','Глина, 0–5 см','%'],['phh2o','pH, 0–5 см','']]){
      const soil=v.soil.find(q=>q.property===prop&&q.depth==='0-5cm');
      row(target,label,soil?num(soil.sensitivity.base,1)+' '+unit:'Нет данных');
      if(soil){const note=el('p',`Размах квантилей Q05–Q95 ячеек: ${soil.provider_cell_quantile_span.map(z=>num(z,1)).join('…')} ${unit}. Не интервал среднего поля.`,target,'data-footnote');}
    }
    el('h4','Погодный фон архива',target);const weather=S.public.weather[$('period').value],end=S.detail?.pair?.after?.at?.slice(0,10);
    if(weather?.status==='available'&&end){
      const through=Date.parse(end+'T00:00:00Z'),days=n=>weather.daily.filter(r=>Date.parse(r.date+'T00:00:00Z')<=through&&Date.parse(r.date+'T00:00:00Z')>through-n*86400000);
      const seven=days(7),thirty=days(30),sumRain=rows=>rows.every(r=>r.precipitation!==null)?rows.reduce((a,r)=>a+r.precipitation,0):null;
      row(target,'Осадки за 7 дней',seven.length===7&&sumRain(seven)!==null?num(sumRain(seven),1)+' мм':'Нет полного окна');
      row(target,'Осадки за 30 дней',thirty.length===30&&sumRain(thirty)!==null?num(sumRain(thirty),1)+' мм':'Нет полного окна');
      const last=weather.daily.find(r=>r.date===end);
      row(target,'Минимум воздуха за дату',last?.temperature_2m!==null&&last?.temperature_2m!==undefined?num(last.temperature_2m,1)+' °C':'Нет данных');
      row(target,'Почвенная влага, модель 0–7 см',last?.soil_moisture_0_to_7cm!==null&&last?.soil_moisture_0_to_7cm!==undefined?num(last.soil_moisture_0_to_7cm*100,1)+' об.%':'Нет данных');
      const humid=seven.every(r=>r.relative_humidity_2m!==null)?seven.reduce((a,r)=>a+r.relative_humidity_2m,0):null;
      row(target,'Часы RH ≥90% за 7 дней',seven.length===7&&humid!==null?String(humid)+' ч':'Нет полного окна');
      el('p',`Окно по ${date(end+'T00:00:00Z')} UTC. Последняя полная дата источника: ${weather.last_complete_date?date(weather.last_complete_date+'T00:00:00Z'):'нет'}; дней с пропусками: ${weather.missing_days}. ERA5, сетка 0,25°, общая модельная ячейка ${weather.grid_latitude}, ${weather.grid_longitude}. Это ретроспективная модель, не датчик и не прогноз. Не разрешает различия соседних полей. Высокая влажность воздуха не равна мокрому листу.`,target,'data-footnote');
    }else el('p','Для выбранной пары нет погодной сводки. Значения не подставлены из другого года. Построение новых локальных прогнозов по этим данным не подтверждено.',target,'data-footnote');
    if(!v.candidate_sites.length)el('p','Недостаточно ядра или контраста высот для двух разных мест. Точки не сгенерированы.', $('candidate-sites'),'data-footnote');
    for(const site of v.candidate_sites){
      const b=el('button',undefined,$('candidate-sites'),'candidate-site');b.type='button';b.disabled=!S.detail?.pair;
      el('span',site.kind==='low'?'Нижняя часть поверхности':'Верхняя часть поверхности',b);
      el('strong',num(site.surface_height_m,1)+' м DSM',b);el('small','Выбрать место на снимках →',b);b.addEventListener('click',()=>selectCandidate(site));
    }
  }
  function renderMap(){
    S.explorer=window.ScoutingMap.create({bundle:S.public,onSelect:id=>{if(!S.busy&&!S.commonBusy)selectArea(id,true);},onSite:selectCandidate,onLayer:layer=>{
      if(['terrain','clay','ph'].includes(layer))showAside('context');
      if(layer==='sensors')showAside('sensors');
      if(['ndvi','ndmi'].includes(layer)){showAside('change');S.metric=layer;if(S.detail?.pair)renderChange();renderAreaList();}
    }});
  }
  function highlightMap(){S.explorer?.select(S.fid,$('period').value);}
  function collapseInspector(){if(matchMedia('(max-width:1000px)').matches){document.querySelector('.selection-card').classList.add('mobile-collapsed');document.body.classList.remove('inspector-open');$('toggle-details').setAttribute('aria-expanded','false');$('toggle-details').textContent='Подробнее ↑';}}
  function focusMap(){collapseInspector();S.explorer?.focus(S.fid);}
  $('focus-map').addEventListener('click',focusMap);
  $('zoom-in').addEventListener('click',()=>S.explorer?.zoom(1));
  $('zoom-out').addEventListener('click',()=>S.explorer?.zoom(-1));
  $('reset-map').addEventListener('click',()=>{collapseInspector();S.explorer?.fitAll();});
  window.addEventListener('resize',()=>{if(S.step===1&&S.data)renderAreaList();});
  function selectArea(fid,focus=false){document.body.classList.remove('map-list-open');$('toggle-area-list').setAttribute('aria-expanded','false');++routeRun;saveDraft();S.fid=fid;S.relative=null;S.selectedLocation=null;S.task=null;S.series=null;draftKey='';highlightMap();renderAreaList();journalCounts();context();if(focus)focusMap();writeRoute(true);loadDetail();}
  $('area-search').addEventListener('input',renderAreaList);
  $('period').addEventListener('change',()=>{saveDraft();loadCommon();});
  $('period-mobile').addEventListener('change',()=>{$('period').value=$('period-mobile').value;saveDraft();loadCommon();});
  async function loadDetail(before,after){
    const run=++detailRun;++locationRun;S.locationPending=false;S.selectedLocation=null;S.explorer?.clearSite();S.detail=null;$('open-change').disabled=true;$('prepare-task').disabled=true;
    context();
    $('selected-area').textContent=S.fid;$('selected-dates').textContent='Проверяем архив…';$('selected-ndvi').textContent='—';$('selected-ndmi').textContent='—';$('selection-summary').textContent='';$('selection-quality').textContent='';
    const pair=S.comparison?.pair;if(!pair){context();return;}
    const query=new URLSearchParams({fid:S.fid,period:$('period').value,before:pair.before.id,after:pair.after.id});
    try{
      const d=await api('/api/scouting/area?'+query);if(run!==detailRun)return;S.detail=d;S.placeComparison=d.default_place_comparison;renderPublicContext();highlightMap();
      if(!d.pair){$('selected-dates').textContent='Нет пригодной пары';$('selection-summary').textContent=d.unavailable_reason||'Недостаточно общих пригодных данных выбранной пары. Другие даты не подставлены.';if(S.step===2||S.step===3)go(1,false);context();return;}
      $('selected-dates').textContent=`${date(d.pair.before.at)} → ${date(d.pair.after.at)}`;
      for(const key of ['ndvi','ndmi'])$('selected-'+key).textContent=signed(d.pair.metrics[key].base);
      const mi=d.pair.metrics.ndmi,nd=d.pair.metrics.ndvi;
      $('selection-summary').textContent=mi.base===null?'Общего пригодного покрытия не хватает для сводки.':mi.base<-.05&&nd.base<-.05?'Снизились оба индекса. Причину и необходимость осмотра уточняет агроном.':'Архив показывает изменение поверхности. Значимость и причину оценивает агроном.';
      $('selection-quality').textContent=`Общее покрытие ${Math.round(d.pair.coverage*100)}% · ${daySpan(d.pair.days)} между съёмками.`;
      $('open-change').disabled=false;$('prepare-task').disabled=false;renderChange();context();
    }catch(e){if(run!==detailRun)return;$('selected-dates').textContent='Архив недоступен';message(e.message,true);}
  }
  function datesSelect(id,selected){const node=$(id);node.replaceChildren();for(const v of S.detail.dates){const option=el('option',`${date(v.at)} · ${Math.round(v.coverage*100)}%${v.usable?'':' · пропуск'}`,node);option.value=v.id;option.disabled=!v.usable;}node.value=selected;}
  $('apply-dates').addEventListener('click',()=>{message('');saveDraft();loadCommon($('before-date').value,$('after-date').value);});
  for(const n of document.querySelectorAll('[data-metric]'))n.addEventListener('click',()=>{S.metric=n.dataset.metric;renderChange();writeRoute(true);});
  function renderChange(){
    const d=S.detail;if(!d?.pair)return;const p=d.pair,m=p.metrics[S.metric];
    $('change-area').textContent=d.fragment;datesSelect('before-date',p.before.id);datesSelect('after-date',p.after.id);
    for(const n of document.querySelectorAll('[data-metric]'))n.setAttribute('aria-pressed',String(n.dataset.metric===S.metric));
    for(const side of ['before','after','delta']){
      const q=new URLSearchParams({fid:d.fragment,period:d.period,before:p.before.id,after:p.after.id,key:S.metric,side});
      const img=$(side+'-image');img.hidden=true;img.onload=()=>{img.hidden=false;};img.onerror=()=>{img.hidden=false;img.alt='Карта не загрузилась. Повторите сравнение.';message('Не удалось открыть карту индекса. Числа относятся к сохранённой паре съёмок; изображение не заменено другим.',true);};img.src='/api/scouting/map?'+q;
    }
    $('before-label').textContent=date(p.before.at);$('after-label').textContent=date(p.after.at);
    $('index-legend-label').textContent=`${S.metric.toUpperCase()} · единая шкала −1…+1`;$('index-gradient').classList.toggle('ndmi',S.metric==='ndmi');
    $('index-explainer').textContent=S.metric==='ndmi'?'NDMI · индекс, чувствительный к содержанию влаги в растительности. Это не измерение влажности почвы.':'NDVI · индекс зелёной растительности. Снижение может быть связано с сезоном, уборкой и другими изменениями.';
    $('evidence-title').textContent=`Наблюдаемое изменение ${S.metric.toUpperCase()}`;$('evidence-delta').textContent=signed(m.base);
    $('evidence-values').textContent=m.base===null?'Недостаточно общего пригодного покрытия.':`${num(m.before)} раньше → ${num(m.after)} позже · на общей пригодной части`;
    $('evidence-method').textContent='Изменение — медиана попиксельных разностей. Она может отличаться от разности медиан, показанных выше. Карты обеих дат используют одну шкалу; разность вычисляется только на общих пригодных пикселях.';
    const quality=$('quality-list');quality.replaceChildren();
    for(const[text,ok]of [[`Общее пригодное покрытие: ${Math.round(p.coverage*100)}%`,p.coverage>=.8],[`${p.same_baseline?'Одна версия обработки':'Разные версии обработки'}: ${p.before.baseline||'неизвестна'} / ${p.after.baseline||'неизвестна'}`,p.same_baseline],[`${daySpan(p.days)} между съёмками${p.days>14?' · возможны сезонные изменения':''}`,p.days<=14],[m.all_scenario_min!==null?`Сценарии границ: ${signed(m.all_scenario_min)}…${signed(m.all_scenario_max)}`:'Для сценариев границ недостаточно данных',m.robust]]){
      const n=el('div',undefined,quality,'quality-item'+(ok?'':' caution'));el('span',ok?'✓':'·',n);el('span',text,n);
    }
    const lo=m.all_scenario_min,hi=m.all_scenario_max;
    $('boundary-verdict').textContent=Number.isFinite(lo)&&Number.isFinite(hi)?(hi<0?'Направление устойчиво: во всех рассчитанных смещениях индекс снизился.':lo>0?'Направление устойчиво: во всех рассчитанных смещениях индекс вырос.':'Направление зависит от обводки: в рассчитанных смещениях есть разные знаки или нулевое изменение.'):'Устойчивость направления пока оценить нельзя.';
    $('boundary-note').textContent=`Условные смещения обводки на ${d.working_band_m} м, сужение и расширение. ${m.all_scenario_min===null?'Полной сводки сценариев нет.':`Разность в сценариях: ${signed(m.all_scenario_min)}…${signed(m.all_scenario_max)}.`}`;
    $('pair-source').textContent=`${p.before.id} → ${p.after.id}. Сравнение по архиву; даты выбираются из пригодных съёмок. Версия набора ${p.version.slice(0,12)}. Край обводки исключается из сводок.`;
    renderLocation();chart($('area-chart'),d.dates,S.data.periods.find(v=>v.id===d.period),[],[p.before.at,p.after.at]);
  }
  function currentLocation(){
    if(!S.detail?.pair)return null;const crop=S.detail.pair.crop;
    if(!S.relative)return S.detail.default_location;
    const x=crop.west+S.relative[0]*crop.width*20,y=crop.north-S.relative[1]*crop.height*20;
    return {utm_x:x,utm_y:y};
  }
  function renderLocation(){
    const loc=currentLocation(),crop=S.detail?.pair?.crop;if(!loc||!crop)return;
    const relative=S.relative||[(loc.utm_x-crop.west)/(crop.width*20),(crop.north-loc.utm_y)/(crop.height*20)];
    for(const side of ['before','after']){const pin=$(side+'-wrap').querySelector('.map-pin');pin.hidden=false;pin.style.left=relative[0]*100+'%';pin.style.top=relative[1]*100+'%';}
    const geo=S.selectedLocation||(!S.relative?loc:null);
    $('chosen-location').textContent=geo?`${geo.latitude.toFixed(5)}, ${geo.longitude.toFixed(5)}`:'Выбрано на карте · окно внутри обводки';
    $('location-source').textContent=S.relative?'Фиксируем окно 60 м внутри обводки; не GPS датчика':'Опорная точка внутри обводки; не измеренный GPS';
    const m=S.placeComparison;
    $('location-indices').textContent=m?`В выбранном окне: ΔNDVI ${signed(m.ndvi)} · ΔNDMI ${signed(m.ndmi)}. Общие ячейки: ${m.paired_cells}/${m.total_cells}.${m.ndvi===null?' Недостаточно данных; сводка не вычислена.':''}`:'Сводка выбранного окна недоступна.';
  }
  async function chooseLocation(relative){
    const d=S.detail,run=++locationRun;
    if(!d?.pair)return;S.locationPending=true;$('prepare-task').disabled=true;context();
    const q=new URLSearchParams({fid:d.fragment,period:d.period,before:d.pair.before.id,after:d.pair.after.id,relative:JSON.stringify(relative)});
    try{const result=await api('/api/scouting/location?'+q);if(run!==locationRun)return false;const loc=result.location,c=d.pair.crop;S.selectedLocation=loc;S.placeComparison=result.comparison;S.relative=[(loc.utm_x-c.west)/(c.width*20),(c.north-loc.utm_y)/(c.height*20)];renderLocation();S.explorer?.showSite(loc);message('Место выбрано внутри обводки. Повторные посещения будут связаны с этим же окном 60 м.');return true;}
    catch(error){if(run===locationRun)message(error.message,true);return false;}finally{if(run===locationRun){S.locationPending=false;$('prepare-task').disabled=false;context();}}
  }
  for(const side of ['before','after'])$(side+'-image').addEventListener('click',e=>{const rect=e.currentTarget.getBoundingClientRect();chooseLocation([(e.clientX-rect.left)/rect.width,(e.clientY-rect.top)/rect.height]);});
  $('reset-location').addEventListener('click',()=>{++locationRun;S.locationPending=false;$('prepare-task').disabled=false;S.relative=null;S.selectedLocation=null;S.placeComparison=S.detail.default_place_comparison;renderLocation();});
  function chart(parent,rows,period,visits=[],selected=[]){
    parent.replaceChildren();if(!period){el('p','Нет периода для отображения',parent,'muted');return;}
    const svg=svgEl('svg',{viewBox:'0 0 850 215',role:'img','aria-label':'Наблюдаемые NDVI и NDMI по датам. Крестики — пропуски; вертикальные отметки — реальные посещения.',class:'chart-svg'},parent);
    const start=Date.parse(period.start),end=Date.parse(period.end),x=t=>46+775*(Date.parse(t)-start)/(end-start),y=v=>167-(v+1)*68;
    for(const v of [-1,0,1]){svgEl('line',{x1:46,y1:y(v),x2:821,y2:y(v),stroke:'#e3e8dc'},svg);svgEl('text',{x:31,y:y(v)+4,'text-anchor':'end','font-size':10,fill:'#7e8977'},svg,String(v));}
    for(const at of selected)svgEl('line',{x1:x(at),x2:x(at),y1:20,y2:183,stroke:'#a0b18c','stroke-dasharray':'3 5'},svg);
    const versioned=rows.filter(r=>r.baseline);
    for(let i=1;i<versioned.length;i++)if(versioned[i].baseline!==versioned[i-1].baseline){const xx=x(versioned[i].at);svgEl('line',{x1:xx,x2:xx,y1:20,y2:175,stroke:'#bc9f73','stroke-dasharray':'2 5'},svg);svgEl('text',{x:Math.min(xx+4,745),y:14,'font-size':9,fill:'#9b825c'},svg,`PB ${versioned[i].baseline}`);}
    for(const v of visits){const at=v.payload.observed_at_utc;if(Date.parse(at)<start||Date.parse(at)>end)continue;const n=svgEl('line',{x1:x(at),x2:x(at),y1:13,y2:183,stroke:'#b47c4a','stroke-width':2},svg);svgEl('title',{},n,`Посещение: ${datetime(at)} · ${v.payload.actor}`);}
    for(const row of rows){if(Date.parse(row.at)<start||Date.parse(row.at)>end)continue;for(const[key,colour,offset]of [['ndvi','#416b45',0],['ndmi','#448796',5]]){const value=row[key];const xx=x(row.at);if(value===null||value===undefined){const yy=182+offset;const n=svgEl('path',{d:`M${xx-2},${yy-2}L${xx+2},${yy+2}M${xx+2},${yy-2}L${xx-2},${yy+2}`,stroke:colour,'stroke-width':1,opacity:.5},svg);svgEl('title',{},n,`${date(row.at)} · ${key.toUpperCase()}: пропуск`);}else{const n=svgEl('circle',{cx:xx,cy:y(value),r:3,fill:colour},svg);svgEl('title',{},n,`${date(row.at)} · ${key.toUpperCase()} ${num(value)} · покрытие ${Math.round((row.coverage||0)*100)}%`);}}}
    for(const [at,anchor]of [[period.start,'start'],[period.end,'end']])svgEl('text',{x:x(at),y:210,'font-size':10,fill:'#7e8977','text-anchor':anchor},svg,date(at));
    const legend=el('div',undefined,parent,'chart-legend');for(const[text,cls]of [['NDVI',''],['NDMI','blue'],['Пропуск','gap']]){const n=el('span',undefined,legend);el('i',undefined,n,cls);el('span',text,n);}if(visits.length)el('span','│ Посещение',legend);
  }
  function prepareTask(){
    const p=S.detail.pair,key=[S.fid,p.before.id,p.after.id,JSON.stringify(S.relative)].join('|');
    if(draftKey!==key){draftKey=key;const old=drafts.get(key);$('task-form').reset();$('task-actor').value=old?.actor||S.task?.payload.actor||'';$('task-date').value=old?.date||'';$('task-objective').value=old?.objective||'';taskRequest=old?.request||uid();$('task-status').textContent='';}
    $('task-context-title').textContent=`Площадь ${S.fid}`;const body=$('task-context-body');body.replaceChildren();
    $('task-context-brief').textContent=`${date(p.before.at)} → ${date(p.after.at)} · ${S.metric.toUpperCase()} ${signed(p.metrics[S.metric].base)}`;
    for(const[label,value]of [['Съёмки',`${date(p.before.at)} → ${date(p.after.at)}`],['Индекс',S.metric.toUpperCase()],['Изменение по площади',signed(p.metrics[S.metric].base)],['Изменение в окне 60 м',signed(S.placeComparison?.[S.metric])],['Общее покрытие площади',`${Math.round(p.coverage*100)}%`],['Место',S.relative?'Выбрано на карте':'Внутренняя опорная точка'],['Границы','Не подтверждены']]){const n=el('div',undefined,body,'context-line');el('span',label,n);el('strong',value,n);}
  }
  const questionStarters={plants:'Что изменилось в состоянии растений в выбранном месте? Зафиксировать наблюдения и фото.',operations:'Какие полевые работы проводились между выбранными съёмками? Уточнить даты и сопоставить с изменением индексов.',sensor:'Подходит ли выбранное место для установки датчика? Проверить поверхность, доступ, связь, питание и безопасность.'};
  function taskReadiness(){const ready=$('task-actor').value.trim()&&$('task-objective').value.trim();$('task-readiness').textContent=ready?'Вопрос подготовлен. После сохранения задача появится в плане выезда.':'Укажите ответственного и вопрос для проверки.';}
  for(const button of document.querySelectorAll('[data-question]'))button.addEventListener('click',()=>{const input=$('task-objective'),text=questionStarters[button.dataset.question];if(!input.value.includes(text))input.value+=(input.value.trim()?'\n\n':'')+text;taskReadiness();input.focus();saveDraft();});
  for(const id of ['task-actor','task-objective'])$(id).addEventListener('input',taskReadiness);
  $('open-demo').addEventListener('click',()=>{$('demo-dialog').showModal();});
  $('close-demo').addEventListener('click',()=>{$('demo-dialog').close();});
  $('start-demo').addEventListener('click',async()=>{if(S.busy||S.commonBusy||!S.data)return;const button=$('start-demo');button.disabled=true;saveDraft();++routeRun;S.fid='R11';$('period').value='recent2026';S.relative=null;S.selectedLocation=null;S.task=null;draftKey='';$('demo-dialog').close();try{await loadCommon('S2C_T37TDK_20260921T082016_L2A','S2C_T37TDK_20261014T083251_L2A');renderAreaList();journalCounts();if(S.detail?.pair)go(2);}finally{button.disabled=false;}});
  $('toggle-task-context').addEventListener('click',()=>{
    const card=$('toggle-task-context').closest('.task-context'),collapsed=card.classList.toggle('collapsed');
    $('toggle-task-context').setAttribute('aria-expanded',String(!collapsed));$('toggle-task-context').textContent=collapsed?'Показать основание ↓':'Свернуть основание ↑';
  });
  $('task-form').addEventListener('submit',async e=>{
    e.preventDefault();if(S.busy)return;const button=e.submitter;S.busy=true;button.disabled=true;context();$('task-status').textContent='Сохраняем задачу…';
    try{
      const p=S.detail.pair;const task=await api('/api/scouting/tasks',{request_id:taskRequest,fragment:S.fid,period:S.detail.period,before:p.before.id,after:p.after.id,version:p.version,relative:S.relative,actor:$('task-actor').value,planned_date:$('task-date').value,objective:$('task-objective').value,metric:S.metric});
      S.data=await api('/api/scouting/state');S.task=task;drafts.delete(draftKey);draftKey='';taskRequest=uid();S.busy=false;journalCounts();S.planner.addTask(task.id);go(5);message('Задача сохранена и выбрана для плана. Записи посещения пока нет.');
    }catch(error){$('task-status').textContent=error.message;}finally{S.busy=false;button.disabled=false;context();}
  });
  const outcomes={observed_change:'Изменения описаны в заметке',nothing_notable:'Ничего существенного',unclear:'Нужно уточнение',not_checked:'Проверить не удалось'};
  function renderHistory(){
    $('history-area').textContent=S.fid;const tasks=S.data.tasks.filter(t=>t.payload.place.fragment===S.fid).slice().reverse();const target=$('task-list');target.replaceChildren();
    if(!tasks.length){el('p','Задач этой площади пока нет.',target,'muted');S.task=null;}
    if(!S.task||!tasks.some(t=>t.id===S.task.id))S.task=tasks[0]||null;
    for(const task of tasks){const p=task.payload,visits=S.data.visits.filter(v=>v.payload.task_id===task.id),btn=el('button',undefined,target,'task-item');btn.setAttribute('aria-pressed',String(task.id===S.task?.id));el('span',visits.length?'Есть запись посещения':'Ожидает проверки',btn,'tag');el('strong',p.objective,btn);el('p',`${p.actor}${p.planned_date?' · '+date(p.planned_date+'T12:00:00+03:00'):' · без плановой даты'}`,btn);el('small',`Место ${p.place.id} · ${visits.length} записей`,btn);btn.addEventListener('click',()=>{S.task=task;renderHistory();writeRoute(true);});}
    $('history-empty').hidden=!!S.task;$('place-history').hidden=!S.task;
    $('repeat-task').textContent=S.task?'+ Повторный осмотр':'+ Создать задачу';
    if(S.task)renderPlace();context();
  }
  async function renderPlace(){
    const task=S.task,place=task.payload.place,p=task.payload.evidence,run=++historyRun;
    const visits=S.data.visits.filter(v=>v.payload.place.id===place.id).sort((a,b)=>Date.parse(b.payload.observed_at_utc)-Date.parse(a.payload.observed_at_utc)||Date.parse(b.recorded_at_utc)-Date.parse(a.recorded_at_utc));
    $('place-title').textContent=`${place.fragment} · место ${place.id.split('-').slice(1).join('–')}`;$('place-coordinates').textContent=`${place.latitude.toFixed(5)}, ${place.longitude.toFixed(5)} · окно 60 м, точка по карте, не GPS`;
    $('visit-count').textContent=`${visits.length} записей посещений`;$('saved-objective').textContent=task.payload.objective;
    const ev=$('saved-evidence');ev.replaceChildren();el('p',`${date(p.before.at)} → ${date(p.after.at)} · ${task.payload.metric.toUpperCase()} ${signed(p.metrics[task.payload.metric].base)} · общее покрытие ${Math.round(p.coverage*100)}%`,ev);el('p',`Задача сохранена ${datetime(task.recorded_at_utc)}. Архивная версия ${p.version.slice(0,12)}. Это исходное основание задачи, не актуальная оценка поля.`,ev);
    if(task.payload.place_evidence){const m=task.payload.place_evidence;el('p',`Отдельно в выбранном окне: ΔNDVI ${signed(m.ndvi)}, ΔNDMI ${signed(m.ndmi)} · общие ячейки ${m.paired_cells}/${m.total_cells}.`,ev);}
    const timeline=$('visit-timeline');timeline.replaceChildren();
    if(!visits.length){const n=el('article',undefined,timeline,'card empty-state');el('span','↗',n,'empty-symbol');el('h3','Посещение ещё не записано',n);el('p','После реального выезда добавьте наблюдения и фото. Тогда здесь появится история и сравнение повторных посещений.',n);}
    visits.forEach((v,index)=>{
      const outer=el('article',undefined,timeline,'timeline-entry'),card=el('div',undefined,outer,'card visit-card'),value=v.payload;
      const heading=el('div',undefined,card,'visit-heading');el('strong',datetime(value.observed_at_utc),heading);el('span',outcomes[value.outcome],heading,'tag');el('p',`Автор: ${value.actor} · введено ${datetime(v.recorded_at_utc)}${value.retrospective_entry?' · запись о прошлом посещении':''}`,card,'muted');el('p',value.notes,card,'notes');
      if(value.follow_up)el('p','Следующий шаг: '+value.follow_up,card,'follow-up');
      if(value.photos.length){const photos=el('div',undefined,card,'visit-photos');for(const photo of value.photos){const link=el('a',undefined,photos);link.href=`/api/scouting/visits/${v.id}/photos/${photo.id}`;link.target='_blank';link.rel='noopener';const img=el('img',undefined,link);img.src=link.href;img.alt=photo.original_name;img.loading='lazy';}}
      const prev=visits[index+1];
      if(prev){const n=el('p',undefined,card,'visit-diff');const older=prev.payload;const changed=[];if(value.outcome!==older.outcome)changed.push(`результат: «${outcomes[older.outcome]}» → «${outcomes[value.outcome]}»`);if(value.notes!==older.notes)changed.push('текст наблюдений изменился');else changed.push('текст наблюдений совпадает');const previousIds=new Set(older.photos.map(p=>p.id)),newCount=value.photos.filter(p=>!previousIds.has(p.id)).length;changed.push(`новых файлов относительно прошлого посещения: ${newCount}`);n.textContent=`По сравнению с ${date(older.observed_at_utc)}: ${changed.join('; ')}. Это сравнение записей, а не автоматическая оценка состояния.`;}
    });
    $('place-chart').replaceChildren();el('p','Загружаем наблюдения выбранного окна…',$('place-chart'),'muted');S.series=null;
    try{const series=await api('/api/scouting/history?'+new URLSearchParams({place_id:place.id}));if(run!==historyRun)return;S.series=series;renderPlaceChart();}catch(e){if(run!==historyRun)return;$('place-chart').replaceChildren();el('p','История индексов недоступна: '+e.message,$('place-chart'),'muted');}
  }
  function renderPlaceChart(){
    if(!S.series||!S.task)return;const period=S.data.periods.find(v=>v.id===$('history-period').value),visits=S.data.visits.filter(v=>v.payload.place.id===S.task.payload.place.id);
    chart($('place-chart'),S.series.rows.filter(v=>v.period===period.id),period,visits);
    const outside=visits.filter(v=>Date.parse(v.payload.observed_at_utc)<Date.parse(period.start)||Date.parse(v.payload.observed_at_utc)>Date.parse(period.end)).length;
    $('place-chart-note').textContent=`В окне ${S.series.total_cells} полностью внутренних ячеек 20 м. Для точки нужны ≥5 пригодных ячеек и покрытие ≥80%; пропуски не заполняются.${outside?` За пределами этого архивного периода: ${outside} записей посещений. Они остаются в журнале ниже.`:''}`;
  }
  $('history-period').addEventListener('change',renderPlaceChart);
  function openVisit(preview=false){
    if(!S.task&&!preview)return;S.visitPreview=preview;visitRequest=uid();$('visit-form').reset();$('photo-preview').replaceChildren();$('visit-status').textContent='';
    $('visit-actor').value=preview?'':S.task.payload.actor;$('visit-place-label').textContent=preview?`Площадь ${S.fid} · поля формы заполняются после настоящего посещения`:`${S.task.payload.place.id} · ${S.task.payload.objective}`;
    $('visit-preview-note').hidden=!preview;$('visit-submit').disabled=preview;$('visit-submit').textContent=preview?'Запись доступна после создания задачи':'Сохранить результат посещения';
    $('visit-dialog').showModal();
  }
  $('open-visit-form').addEventListener('click',()=>openVisit());$('preview-visit').addEventListener('click',()=>openVisit(true));
  $('close-visit').addEventListener('click',()=>{if(!S.busy)$('visit-dialog').close();});
  $('visit-dialog').addEventListener('cancel',e=>{if(S.busy)e.preventDefault();});
  const previewUrls=[];
  $('visit-photos').addEventListener('change',()=>{for(const url of previewUrls)URL.revokeObjectURL(url);previewUrls.length=0;$('photo-preview').replaceChildren();for(const file of $('visit-photos').files){const url=URL.createObjectURL(file);previewUrls.push(url);const img=el('img',undefined,$('photo-preview'));img.src=url;img.alt=file.name;}});
  async function photo(file){if(file.size>5_000_000)throw Error('Фото должно быть не больше 5 МБ');if(!['image/jpeg','image/png','image/webp'].includes(file.type))throw Error('Нужен файл JPEG, PNG или WebP');return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve({filename:file.name,data_base64:reader.result.split(',')[1]});reader.onerror=()=>reject(Error('Не удалось прочитать фото'));reader.readAsDataURL(file);});}
  $('visit-form').addEventListener('submit',async e=>{
    e.preventDefault();if(S.busy||!S.task||S.visitPreview)return;const button=e.submitter;button.disabled=true;S.busy=true;context();$('visit-status').textContent='Проверяем и сохраняем запись…';
    try{const files=[...$('visit-photos').files];if(files.length>3)throw Error('Можно приложить до трёх фотографий');const photos=await Promise.all(files.map(photo));
      const raw={request_id:visitRequest,task_id:S.task.id,actor:$('visit-actor').value,observed_at:$('visit-time').value+':00+03:00',notes:$('visit-notes').value,outcome:$('visit-outcome').value,follow_up:$('visit-follow-up').value,photos,real_visit_confirmed:$('visit-confirm').checked};
      await api('/api/scouting/visits',raw);S.data=await api('/api/scouting/state');$('visit-dialog').close();journalCounts();renderHistory();message('Фактическая запись сохранена с указанными автором и датой.');
    }catch(error){$('visit-status').textContent=error.message;}finally{S.busy=false;button.disabled=false;context();}
  });
  async function applyRoute(){
    const run=++routeRun;
    const [name,query='']=location.hash.slice(1).split('?'),params=new URLSearchParams(query),fid=params.get('area'),period=params.get('period');
    saveDraft();if(S.data.items.some(v=>v.id===fid))S.fid=fid;
    if(S.data.periods.some(v=>v.id===period))$('period').value=period;
    S.metric=['ndvi','ndmi'].includes(params.get('metric'))?params.get('metric'):'ndmi';S.relative=null;S.selectedLocation=null;S.series=null;
    S.task=S.data.tasks.find(t=>t.id===params.get('task')&&t.payload.place.fragment===S.fid)||null;
    highlightMap();renderAreaList();journalCounts();context();const wantedTask=S.task;await loadCommon(params.get('before'),params.get('after'),false);if(run!==routeRun)return;S.task=wantedTask;
    S.explorer?.setLayer(S.metric);
    const step=routes.indexOf(name);go(step>0&&(step===5||step===4||step===1||S.detail?.pair)?step:1,false);writeRoute(true);
  }
  window.addEventListener('popstate',()=>{if(S.data&&!S.busy){if($('visit-dialog').open)$('visit-dialog').close();applyRoute();}});
  try{
    const[data,publicData]=await Promise.all([api('/api/scouting/state'),api('/scouting/assets/public-context.json')]);S.data=data;S.public=publicData;
    $('snapshot-date').textContent=date(data.snapshot_at);$('area-count').textContent=String(data.items.length);
    for(const period of data.periods)for(const id of ['period','period-mobile','history-period']){const option=el('option',period.id==='recent2026'?'Июль–октябрь 2026':period.label,$(id));option.value=period.id;}
    $('period').value='recent2026';$('history-period').value='recent2026';S.planner=window.ScoutingPlans.create({api,getData:()=>S.data,onChange:context,onRefresh:data=>{S.data=data;},onOpenTask:task=>{const p=task.payload;history.pushState(null,'','#history?'+new URLSearchParams({area:p.place.fragment,period:p.period,metric:p.metric,task:task.id,before:p.evidence.before.id,after:p.evidence.after.id}));applyRoute();}});renderMap();$('workspace').hidden=false;$('loading').hidden=true;await applyRoute();
  }catch(e){$('loading').textContent='Не удалось открыть рабочее пространство. '+e.message;message('Архив или журнал недоступен. Демонстрационные данные не подставлены.',true);}
})();
