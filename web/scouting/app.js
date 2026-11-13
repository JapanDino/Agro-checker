'use strict';
(async () => {
  const $ = id => document.getElementById(id), NS = 'http://www.w3.org/2000/svg';
  const S = {step:6, fid:'R11', metric:'ndmi', detail:null, data:null, geometry:null, relative:null, selectedLocation:null, placeComparison:null, task:null, series:null, busy:false, locationPending:false, visitPreview:false, comparison:null, commonBusy:false,locationConfirmed:false,compareView:"pair"};
  const drafts = new Map();let detailRun = 0, historyRun = 0, locationRun = 0, routeRun = 0, draftKey = '', taskRequest = '', commonRun = 0;
  let reviewRun=0,reviewKey='',reviewData=null;
  const date = v => new Date(v).toLocaleDateString('ru-RU',{timeZone:'Europe/Moscow'});
  const datetime = v => new Date(v).toLocaleString('ru-RU',{timeZone:'Europe/Moscow',dateStyle:'medium',timeStyle:'short'});
  const num = (v,d=3) => typeof v === 'number' && Number.isFinite(v) ? v.toFixed(d).replace('.',',') : '—';
  const signed = v => typeof v === 'number' ? (v>0?'+':'')+num(v) : '—';
  const daySpan = value => {const n=Math.round(value),r=n%100;return `${n} ${r>=11&&r<=14?'дней':n%10===1?'день':n%10>=2&&n%10<=4?'дня':'дней'}`;};
  const localDay = value => new Intl.DateTimeFormat('sv-SE',{timeZone:'Europe/Moscow'}).format(value);
  const uid = () => crypto.randomUUID().replaceAll('-','');
  const el = (tag,text,parent,cls) => {const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;if(parent)parent.appendChild(n);return n;};
  const svgEl = (tag,attrs,parent,text) => {const n=document.createElementNS(NS,tag);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,v);if(text!==undefined)n.textContent=text;if(parent)parent.appendChild(n);return n;};
  const message = (text,error=false) => {$('message').textContent=text;$('message').classList.toggle('error',error);$('message').setAttribute('role',error?'alert':'status');$('message').hidden=!text;};
  const routes=['','map','changes','task','history','plan','today'];
  function writeRoute(replace=false){
    if(!S.data)return;
    const query=new URLSearchParams({area:S.fid,period:$('period').value,metric:S.metric});
    if(S.comparison?.pair){query.set('before',S.comparison.pair.before.id);query.set('after',S.comparison.pair.after.id);}
    if((S.step===2||S.step===3)&&S.locationConfirmed)query.set('point',S.relative?JSON.stringify(S.relative):'reference');
    if(S.step===4&&S.task)query.set('task',S.task.id);
    const hash=S.step===6?'#today':'#'+routes[S.step]+'?'+query;
    if(location.hash!==hash)history[replace?'replaceState':'pushState'](null,'',hash);
  }
  function context(){
    if(S.data)document.title=`${S.fid} · ${$('page-title').textContent} · AgroPulse`;
    if($('period-mobile'))$('period-mobile').value=$('period').value;
    $('context-area').textContent=S.fid;$('focus-label').textContent=S.fid;
    $('context-period').textContent=S.data?.periods.find(p=>p.id===$('period').value)?.label.replace('Последние 90 дней 2026','Июль–октябрь 2026')||'Архив';
    $('imagery-column')?.setAttribute('aria-busy',String(S.commonBusy||!S.detail?.pair));
    $('mobile-action').hidden=!S.data||S.step===6;$('start-demo').disabled=!S.data||S.busy||S.commonBusy;
    if(S.step===6)return;
    const plan=S.planner?.state()||{};
    const copy={1:[`Площадь ${S.fid}`,'Архивное сравнение','Смотреть снимки →'],2:[`${S.fid} · окно 60 м`,S.locationConfirmed?'Место подтверждено':S.detail?.pair?'Выберите место для проверки':'Проверяем снимки',S.locationConfirmed?'Создать задачу →':'Выбрать место →'],3:[`Задача · ${S.fid}`,'Сохраняем вопрос и основание','Сохранить →'],4:[`История · ${S.fid}`,S.task?'Выбранное место':'Посещений пока нет',S.task?'Записать результат':'Выбрать место →'],5:['Выезд',plan.saving?'Сохраняем план…':plan.hasDraft?'Проверьте название и места':plan.hasReport?'Готовый маршрутный лист':'Места и вопросы для выезда',plan.hasReport&&!plan.hasDraft?'Скачать лист ↓':'Сохранить план →']}[S.step];
    $('mobile-action-title').textContent=copy[0];$('mobile-action-note').textContent=copy[1];$('mobile-primary').textContent=copy[2];
    $('mobile-primary').disabled=S.busy||S.commonBusy||(S.step===5&&(plan.saving||(!plan.canSave&&!(plan.hasReport&&!plan.hasDraft))))||((S.step===1||S.step===2||S.step===3)&&(!S.detail?.pair||S.locationPending));
  }
  async function api(path,body){
    const options=body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Field-Token':S.data.token},body:JSON.stringify(body)};
    if(path==='/api/scouting/state'||path==='/api/scouting/visits')options.signal=AbortSignal.timeout(30000);
    const response=await fetch(path,options);
    const data=await response.json();
    if(!response.ok)throw Object.assign(Error(data.error||'Не удалось выполнить запрос'),{status:response.status});
    if(path==='/api/scouting/state')await saveOfflineWorkspace(data);
    return data;
  }
  let todayRun=0,manualRequest='',manualPicker=null,manualCoordinates=null;
  function renderTodayInspections(drafts){
    if(S.step!==6)return;
    const openList=$('today-inspections'),completed=$('today-completed-list');
    openList.replaceChildren();completed.replaceChildren();
    const visits=S.data.visitStatuses??S.data.visits.map(visit=>({...visit.payload,recorded_at_utc:visit.recorded_at_utc}));
    const latest=new Map(),savedRequests=new Set();
    for(const visit of visits){
      savedRequests.add(visit.request_id);
      const previous=latest.get(visit.task_id);
      if(!previous||Date.parse(visit.observed_at_utc)>Date.parse(previous.observed_at_utc)||
        (visit.observed_at_utc===previous.observed_at_utc&&visit.recorded_at_utc>previous.recorded_at_utc)){
        latest.set(visit.task_id,visit);
      }
    }
    const local=new Map(drafts.filter(draft=>!savedRequests.has(draft.request)).map(draft=>[draft.taskId,draft]));
    const day=localDay(new Date()),tasks=[...S.data.tasks].sort((a,b)=>
      (a.payload.planned_date||day).localeCompare(b.payload.planned_date||day)||a.recorded_at_utc.localeCompare(b.recorded_at_utc));
    let needsVisit=0,awaitingUpload=0,done=0;
    const pending=[],localCards=[];
    for(const task of tasks){
      const {place,objective,planned_date:planned,actor}=task.payload;
      const draft=local.get(task.id),visit=latest.get(task.id);
      const needsCheck=!visit||visit.outcome==='not_checked';
      const finished=!draft&&!needsCheck;
      const activity=visit&&localDay(new Date(visit.observed_at_utc));
      if(finished&&planned!==day&&activity!==day)continue;
      if(!draft&&needsCheck&&planned>day&&visit?.outcome!=='not_checked')continue;
      if(draft)awaitingUpload++;
      else if(needsCheck)needsVisit++;
      else done++;
      const key=draft?.queued?'queued':draft?'draft':visit?'saved':'assigned';
      const label={assigned:'Назначен',draft:'Есть офлайн-черновик',queued:'Ждёт отправки',saved:'Сохранён в журнале'}[key];
      const card=el('article',undefined,finished?completed:null,'card today-place today-inspection');
      if(!finished)(draft?localCards:pending).push(card);
      const heading=el('div',undefined,card,'today-inspection-heading');
      el('h3','Площадь '+place.fragment,heading);
      const badge=el('span',label,heading,'tag today-inspection-badge');badge.dataset.status=key;
      el('p',objective,card);
      el('p',`${place.latitude.toFixed(5)}, ${place.longitude.toFixed(5)} · ${actor}`,card,'muted');
      el('p',planned?`${planned<day&&!finished?'Срок прошёл · ':'Назначено на '}${date(planned+'T12:00:00+03:00')}`:'Без плановой даты',card,'muted');
      if(!draft&&visit?.outcome==='not_checked')el('p','Проверить не удалось · нужен повторный осмотр',card);
      if(draft?.error)el('p','Перед отправкой нужно исправить запись.',card);
      const button=el('button',draft?.queued?'Открыть запись':draft?'Продолжить черновик':finished?'Открыть журнал':'Записать результат',card,'secondary');
      button.type='button';
      button.addEventListener('click',()=>{openSavedTask(task);if(!finished)openVisit();});
    }
    openList.append(...pending,...localCards);
    $('today-completed').hidden=!done;
    $('today-completed-label').textContent=`Сохранённые осмотры сегодня · ${done}`;
    const summary=needsVisit||awaitingUpload?`Требуют осмотра: ${needsVisit} · Черновики и очередь: ${awaitingUpload}`:'На сегодня и раньше нет осмотров без результата.';
    $('today-inspections-status').textContent=summary+
      (offlineCached&&!S.data.visitStatuses?' Сведения о журнале появятся после подключения.':'');
  }
  async function loadToday(){
    const run=++todayRun,list=$('today-places'),status=$('today-status');
    list.replaceChildren();status.textContent='Проверяем список мест…';status.hidden=false;
    $('today-date').textContent=date(Date.now());
    try{
      const data=await api('/api/scouting/today');
      if(run!==todayRun)return;
      $('today-date').textContent=date(data.as_of_utc);
      status.textContent=data.status==='unavailable'?'Не удалось проверить спутниковый список. Ручное назначение доступно.':
        data.suggestions.length?'Места по экспериментальному спутниковому списку. Точка внутри площади служит ориентиром для осмотра.':
        data.status==='no_supported_places'?'В актуальном выпуске нет мест с подтверждённым снижением индекса. Можно назначить осмотр вручную.':
        'Недостаточно данных для актуальной спутниковой подсказки. Можно назначить осмотр вручную.';
      for(const {place,reason,acquired_at_utc:acquired,target_window:window} of data.suggestions){
        const card=el('article',undefined,list,'card today-place');
        el('h3','Площадь '+place.fragment,card);
        el('p',`Опорная точка · ${place.latitude.toFixed(5)}, ${place.longitude.toFixed(5)}`,card,'muted');
        el('p',reason,card);
        const details=el('dl',undefined,card);
        el('dt','Окно проверки',details);
        el('dd',`${datetime(window.start_utc)} — ${datetime(window.end_exclusive_utc)} МСК`,details);
        el('dt','Снимок для сигнала',details);
        const age=Math.floor((Date.parse(data.as_of_utc)-Date.parse(acquired))/86400000);
        el('dd',`${date(acquired)} · ${age===0?'меньше суток назад':daySpan(age)+' назад'}`,details);
        const assign=el('button','Назначить осмотр',card,'secondary');
        assign.type='button';
        const windowStart=localDay(new Date(window.start_utc));
        const planned=windowStart>data.date?windowStart:data.date;
        assign.addEventListener('click',()=>openManualTask(place.fragment,null,reason,planned));
      }
    }catch(error){
      if(run===todayRun)status.textContent='Список мест недоступен. Ручное назначение доступно.';
    }
  }
  function manualPoint(){
    const reference=S.data.items.find(item=>item.id===$('manual-task-area').value).reference;
    const point=manualCoordinates||reference;
    manualPicker?.showPoint(point.latitude,point.longitude);
    $('manual-task-reference').hidden=!manualCoordinates;
    $('manual-task-point').textContent=`${manualCoordinates?'Выбранная точка':'Опорная точка'}: ${point.latitude.toFixed(5)}, ${point.longitude.toFixed(5)}`;
  }
  function setManualPoint(place){
    manualCoordinates=place;
    $('manual-task-status').textContent='';
    manualPoint();
  }
  function openManualTask(fid=S.fid,place=null,objective='',planned=localDay(new Date())){
    if(!S.data||S.busy)return;
    $('manual-task-form').reset();manualRequest=uid();
    $('manual-task-area').value=fid;
    $('manual-task-objective').value=objective;$('manual-task-date').value=planned;
    $('manual-task-dialog').showModal();
    manualPicker??=window.ScoutingMap.pointPicker({container:$('manual-task-map'),bundle:S.public,
      onPick:({lat,lng})=>setManualPoint({latitude:lat,longitude:lng}),
      onOutside:()=>$('manual-task-status').textContent='Выберите точку внутри выделенной обводки.'});
    manualPicker.showArea(fid);
    setManualPoint(place);
  }
  $('open-manual-task').addEventListener('click',()=>openManualTask());
  $('open-area-manual-task').addEventListener('click',()=>openManualTask());
  $('manual-task-area').addEventListener('change',()=>{
    manualPicker.showArea($('manual-task-area').value);
    setManualPoint(null);
  });
  $('manual-task-reference').addEventListener('click',()=>setManualPoint(null));
  $('close-manual-task').addEventListener('click',()=>{if(!S.busy)$('manual-task-dialog').close();});
  $('manual-task-dialog').addEventListener('cancel',e=>{if(S.busy)e.preventDefault();});
  $('manual-task-form').addEventListener('submit',async e=>{
    e.preventDefault();if(S.busy)return;
    const status=$('manual-task-status'),button=$('save-manual-task');
    const raw={request_id:manualRequest,source:'manual',fragment:$('manual-task-area').value,
      coordinates:manualCoordinates?[manualCoordinates.longitude,manualCoordinates.latitude]:null,actor:$('manual-task-actor').value,
      planned_date:$('manual-task-date').value,objective:$('manual-task-objective').value};
    S.busy=true;button.disabled=true;status.textContent='Сохраняем задачу…';
    try{
      const task=await api('/api/scouting/tasks',raw);
      if(!S.data.tasks.some(item=>item.id===task.id))S.data.tasks.push(task);
      await saveOfflineWorkspace(S.data);
      $('manual-task-dialog').close();S.busy=false;
      openSavedTask(task);
      message('Осмотр назначен. Здесь можно записать результат или добавить задачу в план выезда.');
    }catch(error){status.textContent=error.message;}
    finally{S.busy=false;button.disabled=false;context();}
  });
  function openSavedTask(task){
    S.fid=task.payload.place.fragment;S.task=task;S.selectedLocation=null;S.detail=null;
    $('selected-area').textContent=S.fid;
    ++detailRun;++locationRun;S.locationPending=false;S.locationConfirmed=false;S.relative=null;
    highlightMap();journalCounts();go(4);
  }
  function saveDraft(){
    if(S.step!==3||!draftKey)return;
    const value={actor:$('task-actor').value,date:$('task-date').value,objective:$('task-objective').value,request:taskRequest};
    drafts.set(draftKey,value);
    const stored=window.ScoutingDrafts.save(draftKey,value);
    $('draft-status').textContent=stored?'Черновик сохранён в этой вкладке. Задача ещё не создана.':'Черновик в памяти. Хранилище вкладки недоступно; обновление страницы удалит текст.';
  }
  function go(step,recordRoute=true){
    if(S.busy)return;
    if(!S.data){message('Загружаем архив. Разделы откроются через несколько секунд.');return;}
    if((step===2||step===3)&&S.commonBusy){message('Рассчитываем общую пару снимков. Дождитесь результата.');return;}
    if(step===3&&S.locationPending){message('Проверяем выбранное место. Дождитесь привязки к карте.');return;}
    if((step===2||step===3)&&!S.detail?.pair){message('Для этой площади пока нет пригодной пары снимков выбранного периода. Можно выбрать другую площадь или период.',true);return;}
    if(step===3&&!S.locationConfirmed){go(2);message('Выберите место на снимке или подтвердите опорную точку.');return;}
    if(recordRoute)++routeRun;
    message('');
    saveDraft();
    if(step===3){prepareTask();taskReadiness();$('draft-status').textContent='Черновик в этой вкладке. Задача ещё не создана.';}
    S.step=step;
    document.body.dataset.stage=String(step);
    for(const n of document.querySelectorAll('[data-panel]'))n.hidden=Number(n.dataset.panel)!==step;
    for(const n of document.querySelectorAll('[data-step]')){if(Number(n.dataset.step)===step)n.setAttribute('aria-current','step');else n.removeAttribute('aria-current');}
    for(const button of document.querySelectorAll('.primary-nav [data-step]')){
      const active=Number(button.dataset.step)===(step===2||step===3?1:step);
      button.classList.toggle('is-active',active);
      if(active)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');
    }
    if(step===1){
      renderAreaList();highlightMap();
      if(['ndvi','ndmi'].includes($('map-layer').value))S.explorer?.setLayer(S.metric);
      if(!S.comparison&&!S.commonBusy)loadCommon();
      else if(S.comparison&&!S.detail&&!S.commonBusy)loadDetail();
    }
    if(step===4)renderHistory();if(step===5)S.planner?.render();
    if(step===6)loadToday();
    renderFieldDrafts();
    $('page-title').textContent=['','Карта хозяйства','Сравнение снимков','Задача на осмотр','История места','План обследования','Сегодня'][step];
    document.title=`${S.fid} · ${$('page-title').textContent} · AgroPulse`;
    $('page-subtitle').textContent=['','От спутникового изменения к вопросу агроному и проверке на месте.','Сравните две даты и выберите место, которое хотите проверить.','Сохраните место, ответственного и вопрос для проверки на месте.','Заметки и фото реальных посещений рядом со спутниковыми наблюдениями.','Объедините вопросы и места в проверяемый план выезда.','Места для ближайшего осмотра'][step];
    context();if(step===1)S.explorer?.resize();if(recordRoute)writeRoute();
    window.scrollTo({top:0,behavior:'instant'});
    if(recordRoute){const heading=$(step===1?'farm-heading':'page-title');heading.setAttribute('tabindex','-1');heading.focus({preventScroll:true});}
  }
  $('mobile-primary').addEventListener('click',()=>{
    if(S.step===5){const plan=S.planner.state();if(plan.saving)return;if(plan.hasReport&&!plan.hasDraft)S.planner.downloadReport();else $('plan-form').requestSubmit($('save-plan'));return;}if(S.step===1)go(2);else if(S.step===2){if(S.locationConfirmed)go(3);else{$('location-box').scrollIntoView({behavior:'smooth',block:'center'});$('use-reference').focus({preventScroll:true});}}else if(S.step===3)$('task-form').requestSubmit($('task-form').querySelector('button[type="submit"]'));else if(S.task)openVisit();else go(2);
  });
  for(const n of document.querySelectorAll('[data-step]'))n.addEventListener('click',()=>go(Number(n.dataset.step)));
  for(const n of document.querySelectorAll('[data-go]'))n.addEventListener('click',()=>go(Number(n.dataset.go)));
  $('open-change').addEventListener('click',()=>go(2));$('prepare-task').addEventListener('click',()=>go(3));
  $('open-area-history').addEventListener('click',()=>{S.task=null;go(4);});
  $('repeat-task').addEventListener('click',async()=>{
    if(!S.task?.payload.evidence||!S.detail?.pair){openManualTask(S.fid,S.task?.payload.place);return;}
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
      else {const usable=c.scenes.filter(v=>v.usable_areas>0);if(usable.length)target.value=(side==='before'?usable[0]:usable[usable.length-1]).id;}
    }
    $('common-quality').textContent=c.pair?`Данные: ${c.available_areas} из ${c.total_areas} площадей · ${daySpan(c.pair.days)}`:'Нет пары для автовыбора — выберите даты вручную';
    $('date-summary').textContent=c.pair?`${date(c.pair.before.at)} → ${date(c.pair.after.at)}`:'Выбрать даты';
    $('atlas-availability').textContent=c.pair?`${c.available_areas} из ${c.total_areas} площадей · ${daySpan(c.pair.days)} между съёмками`:'Наблюдения доступны по датам; общей пары для автовыбора нет';
    $('common-quality').title='Пригодные сравнения на одинаковых датах. Это полнота данных, не точность модели. Даты по умолчанию выбираются по покрытию, версии и интервалу, без подбора величины изменения.';
  }
  async function loadCommon(before,after,route=true){
    if(S.step===3){saveDraft();go(2,false);}
    const run=++commonRun;++detailRun;++locationRun;S.commonBusy=true;S.locationConfirmed=false;S.detail=null;S.relative=null;S.task=null;S.series=null;
    $('apply-common').disabled=true;$('open-change').disabled=true;$('prepare-task').disabled=true;$('selected-dates').textContent='Проверяем общие даты…';$('selected-ndvi').textContent='—';$('selected-ndmi').textContent='—';$('selection-summary').textContent='';$('selection-quality').textContent='';$('common-quality').textContent='Проверяем…';context();
    S.explorer?.setComparison({rows:[]});
    requestReview();
    try{
      const q=new URLSearchParams({period:$('period').value});if(before&&after){q.set('before',before);q.set('after',after);}
      const data=await api('/api/scouting/workspace?'+q);if(run!==commonRun)return;
      S.comparison=data;commonControls();$('date-picker').open=false;$('comparison-dates').open=false;S.explorer?.setComparison(data);renderAreaList();
      await loadDetail();if(run!==commonRun)return;if(route)writeRoute(true);
    }catch(error){if(run!==commonRun)return;S.comparison=null;renderAreaList();if(S.step===2||S.step===3)go(1,false);message(error.message,true);$('common-quality').textContent='Сравнение недоступно';$('selected-dates').textContent='Нет сравнения';}
    finally{if(run===commonRun){S.commonBusy=false;$('apply-common').disabled=false;context();requestReview();}}
  }
  $('apply-common').addEventListener('click',()=>loadCommon($('common-before').value,$('common-after').value));
  function expandInspector(){document.querySelector('.selection-card').classList.remove('mobile-collapsed');document.body.classList.add('inspector-open');$('toggle-details').setAttribute('aria-expanded','true');$('toggle-details').textContent='Свернуть ↓';}
  function showAside(name){
    document.querySelector('.selection-card').dataset.aside=name;
    for(const n of document.querySelectorAll('[data-aside-panel]'))n.hidden=n.dataset.asidePanel!==name;
    for(const n of document.querySelectorAll('[data-aside]'))n.setAttribute('aria-pressed',String(n.dataset.aside===name));
  }
  for(const n of document.querySelectorAll('[data-aside]'))n.addEventListener('click',()=>{showAside(n.dataset.aside);if(n.dataset.aside==='sensors')S.explorer?.setLayer('sensors');if(n.dataset.aside==='change')S.explorer?.setLayer(S.metric);});
  $('toggle-details').addEventListener('click',()=>{const closed=$('toggle-details').closest('.selection-card').classList.toggle('mobile-collapsed');document.body.classList.toggle('inspector-open',!closed);$('toggle-details').setAttribute('aria-expanded',String(!closed));$('toggle-details').textContent=closed?'О площади ↑':'Свернуть ↓';});
  $('toggle-area-list').addEventListener('click',()=>{const open=document.body.classList.toggle('map-list-open');$('toggle-area-list').setAttribute('aria-expanded',String(open));if(open){$('date-picker').open=false;collapseInspector();}});
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
      if(['terrain','clay','ph'].includes(layer)){showAside('context');expandInspector();}
      if(layer==='sensors'){showAside('sensors');expandInspector();}
      if(['ndvi','ndmi'].includes(layer)){showAside('change');S.metric=layer;if(S.detail?.pair)renderChange();renderAreaList();}
    }});
  }
  function highlightMap(){S.explorer?.select(S.fid,$('period').value);}
  function collapseInspector(){if(true){document.querySelector('.selection-card').classList.add('mobile-collapsed');document.body.classList.remove('inspector-open');$('toggle-details').setAttribute('aria-expanded','false');$('toggle-details').textContent='О площади ↑';}}
  function focusMap(){collapseInspector();S.explorer?.focus(S.fid);}
  $('focus-map').addEventListener('click',focusMap);
  $('zoom-in').addEventListener('click',()=>S.explorer?.zoom(1));
  $('zoom-out').addEventListener('click',()=>S.explorer?.zoom(-1));
  $('reset-map').addEventListener('click',()=>{collapseInspector();S.explorer?.fitAll();});
  window.addEventListener('resize',()=>{if(S.step===1&&S.data){renderAreaList();S.explorer?.resize();}});
  window.addEventListener('scouting-design-change',()=>{if(S.step===1&&S.data)requestAnimationFrame(()=>S.explorer?.showAll());});
  function selectArea(fid,focus=false){document.body.classList.remove('map-list-open');$('toggle-area-list').setAttribute('aria-expanded','false');++routeRun;saveDraft();S.fid=fid;S.relative=null;S.selectedLocation=null;S.task=null;S.series=null;draftKey='';highlightMap();renderAreaList();journalCounts();context();if(focus)focusMap();writeRoute(true);loadDetail();}
  $('area-search').addEventListener('input',renderAreaList);
  $('period').addEventListener('change',()=>{saveDraft();loadCommon();});
  $('period-mobile').addEventListener('change',()=>{$('period').value=$('period-mobile').value;saveDraft();loadCommon();});
  async function loadDetail(before,after){
    const run=++detailRun;++locationRun;S.locationPending=false;S.locationConfirmed=false;S.selectedLocation=null;S.explorer?.clearSite();S.detail=null;$('open-change').disabled=true;$('prepare-task').disabled=true;
    requestReview();loadFieldContext(run,S.fid);context();
    $('selected-area').textContent=S.fid;$('selected-dates').textContent='Проверяем архив…';$('selected-ndvi').textContent='—';$('selected-ndmi').textContent='—';$('selection-summary').textContent='';$('selection-quality').textContent='';
    const pair=S.comparison?.pair;if(!pair){$('selected-dates').textContent='Выберите две даты';$('selection-summary').textContent='Автовыбор требует достаточного покрытия хозяйства и совместимой обработки. Выберите даты вручную: качество будет проверено отдельно для каждой площади.';$('date-picker').open=true;renderPublicContext();highlightMap();context();return;}
    const query=new URLSearchParams({fid:S.fid,period:$('period').value,before:pair.before.id,after:pair.after.id});
    try{
      const d=await api('/api/scouting/area?'+query);if(run!==detailRun)return;S.detail=d;S.placeComparison=d.default_place_comparison;renderPublicContext();highlightMap();
      if(!d.pair){$('selected-dates').textContent='Нет пригодной пары';$('selection-summary').textContent=d.unavailable_reason||'Недостаточно общих пригодных данных выбранной пары. Другие даты не подставлены.';if(S.step===2||S.step===3)go(1,false);context();return;}
      $('selected-dates').textContent=`${date(d.pair.before.at)} → ${date(d.pair.after.at)}`;
      for(const key of ['ndvi','ndmi'])$('selected-'+key).textContent=signed(d.pair.metrics[key].base);
      const mi=d.pair.metrics.ndmi,nd=d.pair.metrics.ndvi;
      $('selection-summary').textContent=mi.base===null?'Общего пригодного покрытия не хватает для сводки.':mi.base<-.05&&nd.base<-.05?'Снизились оба индекса. Причину и необходимость осмотра уточняет агроном.':'Архив показывает изменение поверхности. Значимость и причину оценивает агроном.';
      $('selection-quality').textContent=`Общее покрытие ${Math.round(d.pair.coverage*100)}% · ${daySpan(d.pair.days)} между съёмками.`;
      $('open-change').disabled=false;$('prepare-task').disabled=!S.locationConfirmed;renderChange();context();
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
    $('analysis-date-summary').textContent=`${date(p.before.at)} → ${date(p.after.at)} · изменить`;
    $('swipe-before-label').textContent=date(p.before.at);$('swipe-after-label').textContent=date(p.after.at);
    for(const side of ['before','after'])$('swipe-'+side).src=$(side+'-image').src;
    $('index-legend-label').textContent=`${S.metric.toUpperCase()} · единая шкала −1…+1`;$('index-gradient').classList.toggle('ndmi',S.metric==='ndmi');
    $('index-explainer').textContent=S.metric==='ndmi'?'NDMI · индекс, чувствительный к содержанию влаги в растительности. Это не измерение влажности почвы.':'NDVI · индекс зелёной растительности. Снижение может быть связано с сезоном, уборкой и другими изменениями.';
    $('comparison-coverage').textContent=`${Math.round(p.coverage*100)}% общих пригодных пикселей · ${daySpan(p.days)}`;
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
  function reviewContext(){
    const d=S.detail,p=d?.pair,place=S.selectedLocation||d?.default_location;
    if(!p||!place?.id||d.fragment!==S.fid)return null;
    const query={fid:d.fragment,period:d.period,before:p.before.id,after:p.after.id,place_id:place.id};
    return {query,key:JSON.stringify([query,p.version])};
  }
  function renderReview(){
    const root=$('inspection-review');if(!root||!reviewData)return;
    window.ScoutingReviewCard.render(root,reviewData,{confirmed:S.locationConfirmed&&!S.locationPending&&!S.commonBusy,onQuestion:question=>{
      if(!S.locationConfirmed||S.locationPending||S.commonBusy)return;
      go(3);const field=$('task-objective');
      if(!field.value.trim())field.value=question;
      else if(!field.value.includes(question)){if(field.value.length+question.length+2>2000){message('Вопрос не добавлен: сократите существующий текст задачи.',true);return;}field.value+='\n\n'+question;}
      field.dispatchEvent(new Event('input',{bubbles:true}));field.focus();
    }});
  }
  async function requestReview(){
    const root=$('inspection-review'),c=reviewContext();if(!root)return;
    if(!c){++reviewRun;reviewKey='';reviewData=null;root.replaceChildren();return;}
    if(c.key===reviewKey){renderReview();return;}
    const run=++reviewRun;reviewKey=c.key;reviewData=null;root.textContent='Проверяем основание выбранного места…';
    try{
      const data=await api('/api/scouting/review?'+new URLSearchParams(c.query));
      if(run!==reviewRun||reviewContext()?.key!==c.key)return;reviewData=data;renderReview();
    }catch(error){if(run===reviewRun){reviewKey='';root.textContent='Карточка недоступна. Повторите выбор места; прогноз не подставлен.';}}
  }
  function renderLocation(){
    const loc=currentLocation(),crop=S.detail?.pair?.crop;if(!loc||!crop)return;
    const relative=S.relative||[(loc.utm_x-crop.west)/(crop.width*20),(crop.north-loc.utm_y)/(crop.height*20)];
    for(const side of ['before','after']){const pin=$(side+'-wrap').querySelector('.map-pin');pin.hidden=false;pin.style.left=relative[0]*100+'%';pin.style.top=relative[1]*100+'%';}
    const geo=S.selectedLocation||(!S.relative?loc:null);
    $('chosen-location').textContent=geo?`${geo.latitude.toFixed(5)}, ${geo.longitude.toFixed(5)}`:'Выбрано на карте · окно внутри обводки';
    $('location-choice-status').textContent=S.locationConfirmed?(S.relative?'Выбрано вами':'Опорная точка подтверждена'):'Опорная точка · не подтверждена';
    $('location-box').classList.toggle('confirmed',S.locationConfirmed);$('reset-location').hidden=!S.locationConfirmed;
    $('prepare-task').disabled=!S.locationConfirmed||S.locationPending||S.commonBusy;
    $('location-readiness').textContent=S.locationConfirmed?'Сохранится окно 60 м и исходные съёмки.':'Выберите место или подтвердите опорную точку.';
    $('location-source').textContent=S.relative?'Фиксируем окно 60 м внутри обводки; не GPS датчика':'Опорная точка внутри обводки; не измеренный GPS';
    const m=S.placeComparison;
    $('location-indices').textContent=m?`В выбранном окне: ΔNDVI ${signed(m.ndvi)} · ΔNDMI ${signed(m.ndmi)}. Общие ячейки: ${m.paired_cells}/${m.total_cells}.${m.ndvi===null?' Недостаточно данных; сводка не вычислена.':''}`:'Сводка выбранного окна недоступна.';
    requestReview();
  }
  async function chooseLocation(relative){
    const d=S.detail,run=++locationRun;
    if(!d?.pair)return;S.locationPending=true;$('prepare-task').disabled=true;context();
    const q=new URLSearchParams({fid:d.fragment,period:d.period,before:d.pair.before.id,after:d.pair.after.id,relative:JSON.stringify(relative)});
    try{const result=await api('/api/scouting/location?'+q);if(run!==locationRun)return false;const loc=result.location,c=d.pair.crop;S.selectedLocation=loc;S.locationConfirmed=true;S.placeComparison=result.comparison;S.relative=[(loc.utm_x-c.west)/(c.width*20),(c.north-loc.utm_y)/(c.height*20)];renderLocation();S.explorer?.showSite(loc);message('Место выбрано внутри обводки. Повторные посещения будут связаны с этим же окном 60 м.');return true;}
    catch(error){if(run===locationRun)message(error.message,true);return false;}finally{if(run===locationRun){S.locationPending=false;$('prepare-task').disabled=!S.locationConfirmed;context();requestReview();writeRoute(true);}}
  }
  for(const side of ['before','after'])$(side+'-image').addEventListener('click',e=>{const rect=e.currentTarget.getBoundingClientRect();chooseLocation([(e.clientX-rect.left)/rect.width,(e.clientY-rect.top)/rect.height]);});
  $('reset-location').addEventListener('click',()=>{++locationRun;S.locationPending=false;S.locationConfirmed=false;$('prepare-task').disabled=true;S.relative=null;S.selectedLocation=null;S.placeComparison=S.detail.default_place_comparison;renderLocation();context();writeRoute(true);});
  const operationLabels={tillage:'Обработка почвы',fertilization:'Внесение удобрений',sowing:'Сев',crop_protection:'Защита растений',harvest:'Уборка'};
  const fieldTimelines=new Map();
  async function loadFieldContext(run,fid){
    const button=$('open-field-context'),inline=$('field-context-inline');
    button.hidden=true;inline.hidden=true;inline.replaceChildren();
    try{
      const result=await api('/api/scouting/field-context?'+new URLSearchParams({fid}));
      if(run!==detailRun||result.status!=='ready')return;
      const timeline=fieldTimelines.get(fid)??{eventKey:null,days:14};
      fieldTimelines.set(fid,timeline);
      const views=[];
      const update=change=>{Object.assign(timeline,change);views.forEach(draw=>draw());};
      views.push(renderFieldContext(inline,result,timeline,update));
      views.push(renderFieldContext($('field-context-body'),result,timeline,update));
      inline.hidden=false;button.hidden=false;
      button.textContent=`Поле № ${result.field.source_field_number} · работы и уборка`;
    }catch{
      if(run!==detailRun)return;
      el('p','История полевых работ недоступна.',inline,'muted');
      inline.hidden=false;
    }
  }
  $('open-field-context').addEventListener('click',()=>$('field-context-dialog').showModal());
  function renderFieldContext(parent,data,timeline,update){
    const {field}=data,match=field.map_match;
    parent.replaceChildren();
    el('h3',`Поле № ${field.source_field_number} · сезон 2025`,parent);
    el('p',`${field.crop_2025} · ${num(field.reported_area_ha,2)} га · предшественник: ${field.predecessor_2025||'нет данных'}`,parent);
    el('p',`Сев ${date(field.reported_sowing_date_2025)} · уборка ${date(field.reported_harvest_date_2025)} · урожайность ${num(field.reported_yield_2025_centners_per_ha,1)} ц/га`,parent);
    el('p',match.use_scouting_fid_as_field_context
      ?`По карте АгроМон поле примерно соответствует ${match.fid}. Граница требует подтверждения.`
      :`Поле занимает северную часть ${match.fid}. Спутниковые индексы рассчитаны по отдельному приближённому контуру поля № ${field.source_field_number}.`,parent,'muted');

    const heading=el('h4','Индексы, осадки и работы',parent);
    heading.tabIndex=-1;
    const controls=el('div',undefined,parent,'segmented field-timeline-window');
    controls.setAttribute('role','group');controls.setAttribute('aria-label','Окно графика');
    const windowButtons=new Map();
    for(const [days,label] of [[14,'±14 дней'],[30,'±30 дней'],[null,'Сезон']]){
      const button=el('button',label,controls);
      button.type='button';button.addEventListener('click',()=>update({days}));
      windowButtons.set(days,button);
    }
    const works=el('details',undefined,parent);
    el('summary',`Выбрать работу из журнала · ${field.operations.length}`,works);
    const events=el('ul',undefined,works,'field-operation-list');
    const eventButtons=new Map();
    for(const operation of field.operations){
      const details=operation.details;
      const note=details.method||details.product||details.products||details.cultivar_or_hybrid;
      const row=el('li',undefined,events);
      const button=el('button',`${date(operation.date)} · ${operationLabels[operation.type]}`,row,'field-operation');
      button.type='button';
      if(note)el('small',note,button);
      button.addEventListener('click',()=>{
        update({eventKey:operation.event_key,days:timeline.days??14});
        works.open=false;
        heading.focus();
      });
      eventButtons.set(operation.event_key,button);
    }
    el('p',`Источник: ${data.source}`,works,'muted');
    const plot=el('div',undefined,parent);
    const focus=el('p',undefined,plot);
    focus.setAttribute('aria-live','polite');
    const series=el('div',undefined,plot);
    const rotation=el('details',undefined,parent);
    el('summary','Севооборот',rotation);
    const seasons=el('ul',undefined,rotation);
    for(const season of [...field.seasons].reverse()){
      const status=season.status==='future_season_record'?' · будущий сезон':'';
      el('li',`${season.season} · ${season.crop||'культура не указана'}${status}`,seasons);
    }
    function draw(){
      const operation=field.operations.find(item=>item.event_key===timeline.eventKey)
        ??field.operations.find(item=>item.type==='harvest')??field.operations.at(-1);
      for(const [days,button] of windowButtons)button.setAttribute('aria-pressed',String(days===timeline.days));
      for(const [key,button] of eventButtons)button.setAttribute('aria-pressed',String(key===operation?.event_key));
      focus.textContent=operation?`${operationLabels[operation.type]} · ${date(operation.date)}`:'';
      renderOperationTimeline(series,data,operation,timeline.days);
    }
    draw();
    return draw;
  }
  function renderOperationTimeline(parent,data,operation,days){
    const {field,harvest_review:harvest,operation_reviews:operationReviews={},season_satellite:satellite,
      operation_weather:weather,season_window:season}=data;
    parent.replaceChildren();
    if(!operation)return;
    const shift=offset=>new Date(Date.parse(operation.date)+offset*86400000).toISOString().slice(0,10);
    const range=days===null?season:{start:shift(-days),end:shift(days)};
    if(!range)return;
    const inWindow=day=>day>=range.start&&day<=range.end;
    const satelliteRows=new Map();
    for(const review of satellite?[satellite]:[harvest,...Object.values(operationReviews)]){
      for(const row of review?.rows??[])if(inWindow(row.at.slice(0,10)))satelliteRows.set(row.id,row);
    }
    const rows=[...satelliteRows.values()].sort((a,b)=>a.at.localeCompare(b.at)||a.id.localeCompare(b.id));
    const rain=(weather?.rows??[]).filter(row=>inWindow(row.date));
    const measured=rain.filter(row=>Number.isFinite(row.precipitation_mm));
    const operationReview=operationReviews[operation.event_key];
    const comparison=satellite?satellite.pairs[operation.event_key]?.[days??30]
      :(operationReview??(operation.type==='harvest'?harvest:null))?.pair;
    const pair=comparison&&inWindow(comparison.before.at.slice(0,10))&&inWindow(comparison.after.at.slice(0,10))?comparison:null;
    const calendarWindow={start:range.start+'T00:00:00+03:00',end:range.end+'T23:59:59+03:00'};
    const plot=el('div',undefined,parent);
    chart(plot,rows,calendarWindow,[],pair?[pair.before.at,pair.after.at]:[],field.operations,rain,operation.date);
    if(measured.length){
      const total=measured.reduce((sum,row)=>sum+row.precipitation_mm,0);
      const coverage=measured.length===rain.length?'':` · данные за ${measured.length} из ${rain.length} дней`;
      el('p',`Осадки по хозяйству · ${num(total,1)} мм за показанные дни${coverage}. Точка наблюдения в выгрузке не указана.`,parent,'muted');
    }else{
      el('p','Суточные осадки за это окно недоступны.',parent,'muted');
    }
    if(!rows.length){
      el('p','В этом окне нет снимков в загруженном архиве.',parent,'muted');
    }else if(pair){
      el('p',`Пара для расчёта ${date(pair.before.at)} → ${date(pair.after.at)} · ${Math.round(pair.coverage*100)}% общих пригодных пикселей · версия обработки ${pair.before.baseline}`,parent);
      const stats=el('ul',undefined,parent);
      for(const [key,metric] of Object.entries(pair.metrics)){
        el('li',`${key.toUpperCase()} ${num(metric.before)} → ${num(metric.after)} · медиана попиксельного изменения ${signed(metric.delta)}`,stats);
      }
    }else if(!rows.some(row=>Number.isFinite(row.ndvi)&&Number.isFinite(row.ndmi))){
      el('p','В этом окне нет пригодных спутниковых наблюдений.',parent,'muted');
    }else if(satellite||operationReview){
      el('p','Для сравнения до и после работы нужна пара с достаточным общим покрытием.',parent,'muted');
    }
    if(rows.length)el('p',data.satellite_source,parent,'muted');
    if(satellite||operationReview)renderOperationImages(parent,field,operation,satellite??operationReview,pair,inWindow);
  }
  function renderOperationImages(parent,field,operation,review,pair,inWindow){
    const rows=review.rows.filter(row=>row.raster_sha256&&row.coverage>0&&inWindow(row.at.slice(0,10)));
    if(!rows.length)return;
    el('h5','Снимки Sentinel‑2 · NDVI',parent);
    el('p','В списках дат указано пригодное покрытие поля. Шкала NDVI от −1 до +1, клетчатая заливка отмечает пропуски.',parent,'muted');
    const figures=el('div',undefined,parent,'field-operation-images');
    const representative=dates=>dates.find(row=>row.ndvi!==null)
      ?? dates.reduce((best,row)=>!best||row.coverage>best.coverage?row:best,null);
    const before=rows.filter(row=>row.at.slice(0,10)<operation.date).reverse();
    const after=rows.filter(row=>row.at.slice(0,10)>=operation.date);
    for(const [label,options,selected] of [
      ['До работы',before,pair?.before],['В день работы и после',after,pair?.after]
    ]){
      if(!options.length){
        el('p',label+' · нет снимков с пригодными пикселями',figures,'muted');
        continue;
      }
      const figure=el('figure',undefined,figures);
      const caption=el('label',label,el('figcaption',undefined,figure));
      const picker=el('select',undefined,caption);
      const describe=row=>`${datetime(row.at)} · ${Math.round(row.coverage*100)}%${row.ndvi===null?' · частичное покрытие':''}`;
      for(const row of options)el('option',describe(row),picker).value=row.id;
      picker.value=(selected??representative(options)).id;
      const link=el('a',undefined,figure);
      link.target='_blank';link.rel='noopener';
      const image=el('img',undefined,link);
      image.loading='lazy';
      const error=el('p','Не удалось открыть снимок.',figure,'muted');
      error.hidden=true;
      image.onerror=()=>{link.hidden=true;error.hidden=false;};
      function show(){
        const row=options.find(row=>row.id===picker.value);
        const query=new URLSearchParams({fid:field.map_match.fid,event:operation.event_key,scene:row.id});
        link.href='/api/scouting/operation-image?'+query;
        image.alt=`Карта NDVI за ${date(row.at)} · поле № ${field.source_field_number}`;
        error.hidden=true;link.hidden=false;image.src=link.href;
      }
      picker.addEventListener('change',show);
      show();
    }
  }
  function chart(parent,rows,period,visits=[],selected=[],operations=[],rain=[],focusedDay=null){
    parent.replaceChildren();if(!period){el('p','Нет периода для отображения',parent,'muted');return;}
    const rainShown=rain.some(row=>Number.isFinite(row.precipitation_mm));
    const label=operations.length?'Наблюдаемые NDVI и NDMI. Крестики — пропуски. Вертикальные линии отмечают полевые работы.':'Наблюдаемые NDVI и NDMI по датам. Крестики — пропуски; вертикальные отметки — реальные посещения.';
    const svg=svgEl('svg',{viewBox:'0 0 850 215',role:'img','aria-label':label+(rainShown?' Столбцы — суточные осадки по хозяйству в миллиметрах, шкала справа.':''),class:'chart-svg'},parent);
    const start=Date.parse(period.start),end=Date.parse(period.end),right=rainShown?799:821;
    const x=t=>46+(right-46)*(Date.parse(t)-start)/(end-start),y=v=>167-(v+1)*68;
    for(const v of [-1,0,1]){svgEl('line',{x1:46,y1:y(v),x2:right,y2:y(v),stroke:'#e3e8dc'},svg);svgEl('text',{x:31,y:y(v)+4,'text-anchor':'end','font-size':10,fill:'#7e8977'},svg,String(v));}
    if(rainShown)drawRainfall(svg,rain,x,right);
    for(const at of selected)svgEl('line',{x1:x(at),x2:x(at),y1:20,y2:183,stroke:'#a0b18c','stroke-dasharray':'3 5'},svg);
    const versioned=rows.filter(r=>r.baseline);
    for(let i=1;i<versioned.length;i++)if(versioned[i].baseline!==versioned[i-1].baseline){const xx=x(versioned[i].at);svgEl('line',{x1:xx,x2:xx,y1:20,y2:175,stroke:'#bc9f73','stroke-dasharray':'2 5'},svg);svgEl('text',{x:Math.min(xx+4,745),y:14,'font-size':9,fill:'#9b825c'},svg,`PB ${versioned[i].baseline}`);}
    for(const v of visits){const at=v.payload.observed_at_utc;if(Date.parse(at)<start||Date.parse(at)>end)continue;const n=svgEl('line',{x1:x(at),x2:x(at),y1:13,y2:183,stroke:'#b47c4a','stroke-width':2},svg);svgEl('title',{},n,`Посещение: ${datetime(at)} · ${v.payload.actor}`);}
    const operationDays=new Map();
    for(const operation of operations){
      const at=operation.date+'T12:00:00+03:00';
      if(Date.parse(at)<start||Date.parse(at)>end)continue;
      if(!operationDays.has(at))operationDays.set(at,new Set());
      operationDays.get(at).add(operation.type);
    }
    for(const [at,types] of operationDays){
      const harvest=types.has('harvest'),focused=at.slice(0,10)===focusedDay,xx=x(at);
      const n=svgEl('line',{x1:xx,x2:xx,y1:20,y2:183,stroke:focused?'#d9a141':harvest?'#b47c4a':'#8b9d7c','stroke-width':focused?3:harvest?2:1,'stroke-dasharray':harvest||focused?'0':'4 4'},svg);
      svgEl('title',{},n,`${date(at)} · ${[...types].map(type=>operationLabels[type]).join(', ')}`);
      if(focused&&xx>116&&xx<right-70)svgEl('text',{x:xx,y:210,'text-anchor':'middle','font-size':11,fill:'#7e8977'},svg,date(at).slice(0,5));
    }
    for(const row of rows){if(Date.parse(row.at)<start||Date.parse(row.at)>end)continue;for(const[key,colour,offset]of [['ndvi','#416b45',0],['ndmi','#448796',5]]){const value=row[key];const xx=x(row.at);if(value===null||value===undefined){const yy=182+offset;const n=svgEl('path',{d:`M${xx-2},${yy-2}L${xx+2},${yy+2}M${xx+2},${yy-2}L${xx-2},${yy+2}`,stroke:colour,'stroke-width':1,opacity:.5},svg);svgEl('title',{},n,`${date(row.at)} · ${key.toUpperCase()}: пропуск`);}else{const n=svgEl('circle',{cx:xx,cy:y(value),r:3,fill:colour},svg);svgEl('title',{},n,`${date(row.at)} · ${key.toUpperCase()} ${num(value)} · покрытие ${Math.round((row.coverage||0)*100)}%`);}}}
    for(const [at,anchor]of [[period.start,'start'],[period.end,'end']])svgEl('text',{x:x(at),y:210,'font-size':10,fill:'#7e8977','text-anchor':anchor},svg,date(at));
    const legend=el('div',undefined,parent,'chart-legend');for(const[text,cls]of [['NDVI',''],['NDMI','blue'],['Пропуск','gap']]){const n=el('span',undefined,legend);el('i',undefined,n,cls);el('span',text,n);}if(visits.length)el('span','│ Посещение',legend);
    if(operationDays.size){el('span','│ Уборка',legend);el('span','┆ Другие работы',legend);}
    if(focusedDay){
      const item=el('span',undefined,legend);
      el('i',undefined,item,'focused-operation');el('span','Выбранная работа',item);
    }
    if(rainShown){
      const item=el('span',undefined,legend);
      el('i',undefined,item,'rainfall');el('span','Осадки по хозяйству · мм',item);
      if(rain.some(row=>row.precipitation_mm===null))el('span','× Нет данных об осадках',legend);
    }
  }
  function drawRainfall(svg,rows,x,right){
    const baseline=167,top=31,peak=Math.max(2,Math.ceil(Math.max(...rows.map(row=>row.precipitation_mm??0))/2)*2);
    const rainY=mm=>baseline-(baseline-top)*mm/peak;
    const width=Math.min(24,(right-46)/rows.length*.65);
    const group=svgEl('g',{class:'chart-rainfall'},svg);
    svgEl('line',{x1:right,y1:top,x2:right,y2:baseline,stroke:'#5799d7',opacity:.6},group);
    for(const tick of [0,peak/2,peak]){
      svgEl('line',{x1:right,y1:rainY(tick),x2:right+4,y2:rainY(tick),stroke:'#5799d7'},group);
      svgEl('text',{x:right+8,y:rainY(tick)+4,'font-size':11,fill:'#7e8977'},group,String(tick));
    }
    svgEl('text',{x:right+8,y:19,'font-size':11,fill:'#7e8977'},group,'мм');
    for(const row of rows){
      const at=row.date+'T12:00:00+03:00',mm=row.precipitation_mm;
      if(mm===null){
        const gap=svgEl('text',{x:x(at),y:177,'text-anchor':'middle','font-size':12,fill:'#5799d7'},group,'×');
        svgEl('title',{},gap,`${date(at)} · нет данных об осадках`);
        continue;
      }
      if(!Number.isFinite(mm)||mm<=0)continue;
      const yy=rainY(mm);
      const bar=svgEl('rect',{x:x(at)-width/2,y:yy,width,height:baseline-yy,fill:'#5799d7',opacity:.55},group);
      svgEl('title',{},bar,`${date(at)} · осадки ${num(mm,1)} мм`);
    }
  }
  function taskDraftKey(){const p=S.detail?.pair;if(!p)return null;const placeId=S.selectedLocation?.id||S.detail.default_location.id;return window.ScoutingDrafts.key('task',[S.fid,S.detail.period,p.before.id,p.after.id,p.version,placeId,S.metric]);}
  function prepareTask(){
    const p=S.detail.pair,key=taskDraftKey();
    if(draftKey!==key){draftKey=key;const old=drafts.get(key)||window.ScoutingDrafts.load(key);$('task-form').reset();$('task-actor').value=old?.actor||S.task?.payload.actor||'';$('task-date').value=old?.date||'';$('task-objective').value=old?.objective||'';taskRequest=old?.request||uid();$('task-status').textContent='';}
    $('task-context-title').textContent=`Площадь ${S.fid}`;const body=$('task-context-body');body.replaceChildren();
    $('task-context-brief').textContent=`${date(p.before.at)} → ${date(p.after.at)} · ${S.metric.toUpperCase()} ${signed(p.metrics[S.metric].base)}`;
    for(const[label,value]of [['Съёмки',`${date(p.before.at)} → ${date(p.after.at)}`],['Индекс',S.metric.toUpperCase()],['Изменение по площади',signed(p.metrics[S.metric].base)],['Изменение в окне 60 м',signed(S.placeComparison?.[S.metric])],['Общее покрытие площади',`${Math.round(p.coverage*100)}%`],['Место',S.relative?'Выбрано на карте':'Внутренняя опорная точка'],['Границы','Не подтверждены']]){const n=el('div',undefined,body,'context-line');el('span',label,n);el('strong',value,n);}
  }
  const questionStarters={plants:'Что изменилось в состоянии растений в выбранном месте? Зафиксировать наблюдения и фото.',operations:'Какие полевые работы проводились между выбранными съёмками? Уточнить даты и сопоставить с изменением индексов.',sensor:'Подходит ли выбранное место для установки датчика? Проверить поверхность, доступ, связь, питание и безопасность.'};
  function taskReadiness(){const ready=$('task-actor').value.trim()&&$('task-objective').value.trim();$('task-readiness').textContent=ready?'Вопрос подготовлен. После сохранения откроется история этого места.':'Укажите ответственного и вопрос для проверки.';}
  for(const button of document.querySelectorAll('[data-question]'))button.addEventListener('click',()=>{const input=$('task-objective'),text=questionStarters[button.dataset.question];if(!input.value.includes(text))input.value+=(input.value.trim()?'\n\n':'')+text;taskReadiness();input.focus();saveDraft();});
  for(const id of ['task-actor','task-objective','task-date'])$(id).addEventListener('input',()=>{taskReadiness();saveDraft();});
  $('open-demo').addEventListener('click',()=>{$('demo-dialog').showModal();});
  $('close-demo').addEventListener('click',()=>{$('demo-dialog').close();});
  $('start-demo').addEventListener('click',async()=>{if(S.busy||S.commonBusy||!S.data)return;const button=$('start-demo');button.disabled=true;saveDraft();++routeRun;S.fid='R11';$('period').value='recent2026';S.relative=null;S.selectedLocation=null;S.task=null;draftKey='';$('demo-dialog').close();try{await loadCommon('S2C_T37TDK_20260921T082016_L2A','S2C_T37TDK_20261014T083251_L2A');renderAreaList();journalCounts();if(S.detail?.pair)go(2);}finally{button.disabled=false;}});
  $('toggle-task-context').addEventListener('click',()=>{
    const card=$('toggle-task-context').closest('.task-context'),collapsed=card.classList.toggle('collapsed');
    $('toggle-task-context').setAttribute('aria-expanded',String(!collapsed));$('toggle-task-context').textContent=collapsed?'Показать основание ↓':'Свернуть основание ↑';
  });
  $('task-form').addEventListener('submit',async e=>{
    e.preventDefault();if(S.busy)return;if(S.commonBusy||S.locationPending||!S.detail?.pair||!S.locationConfirmed){$('task-status').textContent='Данные или место изменились. Вернитесь к снимкам и подтвердите место.';return;}if(draftKey!==taskDraftKey()){saveDraft();prepareTask();taskReadiness();$('task-status').textContent='Основание задачи обновилось. Проверьте вопрос и сохраните ещё раз.';return;}const button=e.submitter||$('task-form').querySelector('button[type="submit"]');S.busy=true;button.disabled=true;context();$('task-status').textContent='Сохраняем задачу…';
    try{
      const p=S.detail.pair;const task=await api('/api/scouting/tasks',{request_id:taskRequest,fragment:S.fid,period:S.detail.period,before:p.before.id,after:p.after.id,version:p.version,relative:S.relative,actor:$('task-actor').value,planned_date:$('task-date').value,objective:$('task-objective').value,metric:S.metric});
      S.data=await api('/api/scouting/state');S.task=task;drafts.delete(draftKey);window.ScoutingDrafts.remove(draftKey);draftKey='';taskRequest=uid();S.busy=false;journalCounts();go(4);message('Задача сохранена. После выезда запишите результат здесь. План выезда можно составить отдельно.');
    }catch(error){$('task-status').textContent=error.message;}finally{S.busy=false;button.disabled=false;context();}
  });
  const outcomes={observed_change:'Изменения описаны в заметке',nothing_notable:'Ничего существенного',unclear:'Нужно уточнение',not_checked:'Проверить не удалось'};
  function renderHistory(){
    $('history-area').textContent=S.fid;const tasks=S.data.tasks.filter(t=>t.payload.place.fragment===S.fid).slice().reverse();const target=$('task-list');target.replaceChildren();
    if(!tasks.length){el('p','Задач этой площади пока нет.',target,'muted');S.task=null;}
    if(!S.task||!tasks.some(t=>t.id===S.task.id)){const placeId=S.selectedLocation?.id||S.detail?.default_location?.id;S.task=tasks.find(t=>t.payload.place.id===placeId)||tasks[0]||null;}
    for(const task of tasks){const p=task.payload,visits=S.data.visits.filter(v=>v.payload.task_id===task.id),btn=el('button',undefined,target,'task-item');btn.setAttribute('aria-pressed',String(task.id===S.task?.id));el('span',visits.length?'Есть запись посещения':'Ожидает проверки',btn,'tag');el('strong',p.objective,btn);el('p',`${p.actor}${p.planned_date?' · '+date(p.planned_date+'T12:00:00+03:00'):' · без плановой даты'}`,btn);el('small',`Место ${p.place.id} · записей: ${visits.length}`,btn);btn.addEventListener('click',()=>{S.task=task;renderHistory();writeRoute(true);});}
    $('history-empty').hidden=!!S.task;$('place-history').hidden=!S.task;
    $('repeat-task').textContent=S.task?'+ Новая задача для места':'+ Создать задачу';
    if(S.task)renderPlace();context();
  }
  $('add-task-to-plan').addEventListener('click',()=>{if(!S.task||S.busy)return;const added=S.planner.addTask(S.task.id);go(5);if(!added)message('Место не добавлено: в плане уже три места или это место выбрано через другую задачу. Проверьте список.',true);});
  async function renderPlace(){
    const task=S.task,place=task.payload.place,p=task.payload.evidence,run=++historyRun;
    const visits=S.data.visits.filter(v=>v.payload.place.id===place.id).sort((a,b)=>Date.parse(b.payload.observed_at_utc)-Date.parse(a.payload.observed_at_utc)||Date.parse(b.recorded_at_utc)-Date.parse(a.recorded_at_utc));
    $('place-title').textContent=`${place.fragment} · выбранное место`;$('place-coordinates').textContent=`${place.latitude.toFixed(5)}, ${place.longitude.toFixed(5)} · окно 60 м, точка по карте, не GPS`;
    $('visit-count').textContent=visits.length?`Посещений: ${visits.length}`:'Осмотр ещё не записан';$('saved-objective').textContent=task.payload.objective;
    const ev=$('saved-evidence');ev.replaceChildren();
    if(p){el('p',`${date(p.before.at)} → ${date(p.after.at)} · ${task.payload.metric.toUpperCase()} ${signed(p.metrics[task.payload.metric].base)} · общее покрытие ${Math.round(p.coverage*100)}%`,ev);el('p',`Задача сохранена ${datetime(task.recorded_at_utc)}. Архивная версия ${p.version.slice(0,12)}. Это исходное основание задачи, не актуальная оценка поля.`,ev);}
    else el('p',`Назначено вручную ${datetime(task.recorded_at_utc)}. Спутниковая пара к задаче не приложена.`,ev);
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
    const visibleVisits=visits.filter(v=>Date.parse(v.payload.observed_at_utc)>=Date.parse(period.start)&&Date.parse(v.payload.observed_at_utc)<=Date.parse(period.end));
    chart($('place-chart'),S.series.rows.filter(v=>v.period===period.id),period,visibleVisits);
    const outside=visits.filter(v=>Date.parse(v.payload.observed_at_utc)<Date.parse(period.start)||Date.parse(v.payload.observed_at_utc)>Date.parse(period.end)).length;
    $('place-chart-note').textContent=`В окне ${S.series.total_cells} полностью внутренних ячеек 20 м. Для точки нужны ≥5 пригодных ячеек и покрытие ≥80%; пропуски не заполняются.${outside?` Посещений за пределами этого архивного периода: ${outside}. Они остаются в журнале ниже.`:''}`;
  }
  $('history-period').addEventListener('change',renderPlaceChart);
  const visitStorage=window.ScoutingVisitDrafts;
  const visitFields=['actor','time','outcome','notes','follow-up'];
  let visitEditor=null,visitWrites=Promise.resolve(),openingVisit=false;
  let offlineReady=false,offlineCached=false,offlineTasksSaved=false,syncing=false,syncNote='',draftListRun=0,syncRetry;
  async function saveOfflineWorkspace(state){
    try{await visitStorage.saveWorkspace(state);offlineTasksSaved=true;}
    catch{offlineTasksSaved=false;}
  }
  function setVisitLocked(locked){
    for(const input of $('visit-form').querySelectorAll('input, textarea, select'))input.disabled=locked;
    for(const id of ['visit-now','visit-save-local','visit-submit'])$(id).disabled=locked;
  }
  async function renderFieldDrafts(){
    const run=++draftListRun,container=$('field-draft-list'),button=$('sync-visits');
    let drafts;
    try{drafts=await visitStorage.list();}
    catch{
      if(run===draftListRun){
        $('field-offline-status').textContent='Хранилище черновиков на этом устройстве недоступно.';
        renderTodayInspections([]);
      }
      return;
    }
    if(run!==draftListRun)return;
    renderTodayInspections(drafts);
    const queued=drafts.filter(draft=>draft.queued).length;
    $('field-offline').hidden=![4,6].includes(S.step)&&!drafts.length&&navigator.onLine&&!offlineCached;
    $('field-offline-status').textContent=(!navigator.onLine?'Связи нет. Черновики сохраняются на этом устройстве.':syncNote)||(offlineCached?'Открыта копия заданий с устройства.':
      !offlineTasksSaved?'Не удалось сохранить задания для открытия без связи.':
      offlineReady?'Задания доступны без связи на этом устройстве.':'Офлайн-режим готовится.');
    button.hidden=!queued;button.disabled=syncing||!navigator.onLine;
    button.textContent=`Отправить в журнал (${queued})`;
    container.replaceChildren();
    for(const draft of drafts){
      const task=S.data?.tasks.find(item=>item.id===draft.taskId);
      if(S.step===6&&task)continue;
      const row=el('div',undefined,container,'field-draft-row');
      const title=task?`${task.payload.place.fragment} · ${task.payload.objective}`:'Задание отсутствует в текущем журнале';
      const state=draft.queued?'Ожидает отправки':draft.error?'Нужно исправить':'Черновик';
      const open=el('button',`${state} · ${title}`,row,'text-button');
      open.type='button';open.disabled=!task;
      open.addEventListener('click',()=>{openSavedTask(task);openVisit();});
    }
    if(offlineCached||!navigator.onLine){
      const ids=new Set(drafts.map(draft=>draft.taskId));
      const tasks=S.data.tasks.filter(task=>!ids.has(task.id));
      if(tasks.length){
        const saved=el('details',undefined,container);
        el('summary','Задания на устройстве',saved);
        for(const task of tasks.slice().reverse()){
          const row=el('div',undefined,saved,'field-draft-row');
          const open=el('button',`${task.payload.place.fragment} · ${task.payload.objective}`,row,'text-button');
          open.type='button';
          open.addEventListener('click',()=>{openSavedTask(task);openVisit();});
        }
      }
    }
  }
  function rememberVisit(queued=false){
    const editor=visitEditor;
    if(!editor||editor.queued||S.visitPreview)return Promise.resolve(false);
    const fields=Object.fromEntries(visitFields.map(name=>[name,$('visit-'+name).value]));
    const draft={taskId:editor.taskId,request:editor.request,revision:uid(),fields,
      files:[...$('visit-photos').files],confirmed:$('visit-confirm').checked,queued};
    $('visit-draft-note').textContent='Сохраняем на устройстве…';
    const write=visitWrites.then(async()=>{
      if(!await visitStorage.save(draft,editor.revision))throw Error('Черновик изменился в другой вкладке. Откройте его заново.');
      editor.revision=draft.revision;editor.queued=queued;
      if(visitEditor===editor)$('visit-draft-note').textContent=queued?'Сохранено на устройстве. Ожидает отправки.':'Черновик сохранён на устройстве.';
      renderFieldDrafts();
      return true;
    }).catch(error=>{
      if(visitEditor===editor)$('visit-draft-note').textContent='Не удалось сохранить черновик на устройстве. '+error.message;
      return false;
    });
    visitWrites=write;
    return write;
  }
  for(const name of [...visitFields,'confirm'])$('visit-'+name).addEventListener('input',()=>rememberVisit());
  async function openVisit(preview=false){
    if(openingVisit||$('visit-dialog').open||(!S.task&&!preview))return;
    const task=S.task;
    openingVisit=true;
    try{
      await visitWrites;
      const draft=preview?null:await visitStorage.get(task.id).catch(()=>null);
      S.visitPreview=preview;
      visitEditor=preview?null:{taskId:task.id,request:draft?.request||uid(),revision:draft?.revision,queued:!!draft?.queued,files:draft?.files||[]};
      $('visit-form').reset();$('visit-status').textContent=draft?.error||'';
      $('visit-actor').value=preview?'':task.payload.actor;
      if(draft){
        for(const name of visitFields)$('visit-'+name).value=draft.fields[name]||'';
        const transfer=new DataTransfer();
        for(const file of draft.files)transfer.items.add(file);
        $('visit-photos').files=transfer.files;
        $('visit-confirm').checked=draft.confirmed;
      }
      renderPhotoPreview();
      $('visit-place-label').textContent=preview?`Площадь ${S.fid} · поля формы заполняются после настоящего посещения`:`${task.payload.place.fragment} · ${task.payload.objective}`;
      $('visit-dialog-title').textContent=preview?'Форма результата · просмотр':'Что вы увидели на месте?';
      $('visit-preview-note').hidden=!preview;
      setVisitLocked(preview||!!draft?.queued);
      $('visit-draft-note').textContent=preview?'':draft?.queued?'Осмотр ждёт отправки. Статус можно проверить в списке черновиков.':
        draft?'Черновик восстановлен с устройства.':'Текст и фото автоматически сохраняются на этом устройстве.';
      $('visit-dialog').showModal();$('visit-dialog').scrollTop=0;
    }finally{openingVisit=false;}
  }
  $('open-visit-form').addEventListener('click',()=>openVisit());$('preview-visit').addEventListener('click',()=>openVisit(true));$('preview-saved-visit').addEventListener('click',()=>openVisit(true));
  $('visit-now').addEventListener('click',()=>{const now=new Date(Date.now()+3*60*60*1000).toISOString().slice(0,16);$('visit-time').value=now;rememberVisit();});
  $('close-visit').addEventListener('click',()=>{if(!S.busy)$('visit-dialog').close();});
  $('visit-dialog').addEventListener('cancel',e=>{if(S.busy)e.preventDefault();});
  $('visit-dialog').addEventListener('close',()=>{if(!$('visit-dialog').open){visitEditor=null;clearPhotoPreview();}});
  $('visit-save-local').addEventListener('click',()=>rememberVisit());
  function compareView(view){
    S.compareView=view;const swipe=view==='swipe';$('image-pair').hidden=swipe;$('swipe-stage').hidden=!swipe;
    for(const b of document.querySelectorAll('[data-compare-view]'))b.setAttribute('aria-pressed',String(b.dataset.compareView===view));
  }
  for(const b of document.querySelectorAll('[data-compare-view]'))b.addEventListener('click',()=>compareView(b.dataset.compareView));
  $('swipe-range').addEventListener('input',()=>{$('swipe-stage').style.setProperty('--reveal',$('swipe-range').value+'%');});
  const divider=$('swipe-stage').querySelector('.swipe-divider');
  const moveDivider=event=>{const rect=divider.parentElement.getBoundingClientRect();const value=Math.max(0,Math.min(100,(event.clientX-rect.left)/rect.width*100));$('swipe-range').value=String(Math.round(value));$('swipe-stage').style.setProperty('--reveal',value+'%');};
  divider.addEventListener('pointerdown',event=>{if(event.button!==0)return;event.preventDefault();divider.setPointerCapture(event.pointerId);moveDivider(event);});
  divider.addEventListener('pointermove',event=>{if(divider.hasPointerCapture(event.pointerId))moveDivider(event);});
  divider.addEventListener('pointerup',event=>{if(divider.hasPointerCapture(event.pointerId))divider.releasePointerCapture(event.pointerId);});

  $('pick-location').addEventListener('click',()=>{compareView('pair');$('before-wrap').scrollIntoView({behavior:'smooth',block:'center'});$('before-image').setAttribute('tabindex','-1');$('before-image').focus({preventScroll:true});message('Нажмите внутри обводки на одном из двух снимков.');});
  $('use-reference').addEventListener('click',()=>{if(!S.detail?.pair||S.locationPending||S.commonBusy)return;S.relative=null;S.selectedLocation=null;S.placeComparison=S.detail.default_place_comparison;S.locationConfirmed=true;renderLocation();context();writeRoute(true);});
  $('close-area-list').addEventListener('click',()=>{document.body.classList.remove('map-list-open');$('toggle-area-list').setAttribute('aria-expanded','false');$('toggle-area-list').focus();});
  document.addEventListener('keydown',e=>{
    if(e.target.closest('input,textarea,select,[contenteditable=true]')||e.metaKey||e.ctrlKey||e.altKey||document.querySelector('dialog[open]'))return;
    if(e.key==='/'&&S.step===1){e.preventDefault();document.body.classList.add('map-list-open');$('toggle-area-list').setAttribute('aria-expanded','true');$('area-search').focus();}
    if(e.key.toLowerCase()==='c'&&S.step===2){e.preventDefault();compareView(S.compareView==='pair'?'swipe':'pair');}
    if(e.key==='Escape'){$('date-picker').open=false;collapseInspector();}
  });
  const previewUrls=[];
  function clearPhotoPreview(){
    for(const url of previewUrls)URL.revokeObjectURL(url);
    previewUrls.length=0;
    $('photo-preview').replaceChildren();
  }
  function renderPhotoPreview(){
    clearPhotoPreview();
    for(const file of $('visit-photos').files){
      const url=URL.createObjectURL(file);previewUrls.push(url);
      const img=el('img',undefined,$('photo-preview'));img.src=url;img.alt=file.name;
    }
  }
  function validatePhotos(files){
    if(files.length>3)throw Error('Можно приложить до трёх фотографий.');
    if(files.some(file=>file.size>5_000_000))throw Error('Фото должно быть не больше 5 МБ.');
    if(files.some(file=>!['image/jpeg','image/png','image/webp'].includes(file.type)))throw Error('Нужен файл JPEG, PNG или WebP.');
  }
  $('visit-photos').addEventListener('change',()=>{
    const files=[...$('visit-photos').files];
    try{validatePhotos(files);}
    catch(error){
      const transfer=new DataTransfer();
      for(const file of visitEditor?.files||[])transfer.items.add(file);
      $('visit-photos').files=transfer.files;
      $('visit-status').textContent=error.message;
      return;
    }
    if(visitEditor)visitEditor.files=files;
    $('visit-status').textContent='';renderPhotoPreview();rememberVisit();
  });
  function photo(file){
    return new Promise((resolve,reject)=>{
      const reader=new FileReader();
      reader.onload=()=>resolve({filename:file.name,data_base64:reader.result.split(',')[1]});
      reader.onerror=()=>reject(Error('Не удалось прочитать фото.'));
      reader.readAsDataURL(file);
    });
  }
  async function syncVisits(){
    if(syncing||!S.data||!navigator.onLine)return;
    clearTimeout(syncRetry);
    syncing=true;
    try{
      const queue=(await visitStorage.list()).filter(draft=>draft.queued);
      if(!queue.length&&!offlineCached)return;
      if(queue.length){syncNote='Отправляем осмотры в журнал…';renderFieldDrafts();}
      const state=await api('/api/scouting/state');
      S.data=state;offlineCached=false;
      if(!queue.length){syncNote='Связь с журналом восстановлена.';return;}
      let needsCorrection=false;
      for(const draft of queue){
        const alreadySaved=state.visits.find(visit=>visit.payload.request_id===draft.request&&visit.payload.task_id===draft.taskId);
        try{
          if(!alreadySaved){
            const fields=draft.fields;
            const raw={request_id:draft.request,task_id:draft.taskId,actor:fields.actor,
              observed_at:fields.time.length===16?fields.time+':00+03:00':fields.time+'+03:00',
              notes:fields.notes,outcome:fields.outcome,follow_up:fields['follow-up'],
              photos:await Promise.all(draft.files.map(photo)),real_visit_confirmed:draft.confirmed};
            const visit=await api('/api/scouting/visits',raw);
            if(!state.visits.some(item=>item.id===visit.id))state.visits.push(visit);
          }
        }catch(error){
          if(error.status!==400&&error.status!==422)throw error;
          await visitStorage.reject(draft,error.message);
          if(visitEditor?.request===draft.request){
            visitEditor.queued=false;
            setVisitLocked(false);
            $('visit-status').textContent=error.message;
          }
          needsCorrection=true;
          continue;
        }
        await visitStorage.acknowledge(draft);
        if(visitEditor?.request===draft.request)$('visit-dialog').close();
      }
      syncNote=needsCorrection?'Некоторые осмотры требуют исправления. Откройте их в списке.':'Осмотры отправлены в журнал.';
    }catch(error){
      syncNote='Журнал пока недоступен. Черновики остаются на устройстве.';
      syncRetry=setTimeout(syncVisits,15000);
    }finally{
      if(!offlineCached)await saveOfflineWorkspace(S.data);
      syncing=false;
      journalCounts();
      if(S.step===4&&!$('visit-dialog').open)renderHistory();
      renderFieldDrafts();
    }
  }
  $('sync-visits').addEventListener('click',syncVisits);
  window.addEventListener('online',()=>{renderFieldDrafts();syncVisits();});
  window.addEventListener('offline',()=>{offlineCached=true;renderFieldDrafts();});
  window.addEventListener('focus',syncVisits);
  $('visit-form').addEventListener('submit',async e=>{
    e.preventDefault();
    if(S.busy||!visitEditor||visitEditor.queued||S.visitPreview)return;
    try{validatePhotos([...$('visit-photos').files]);}
    catch(error){$('visit-status').textContent=error.message;return;}
    S.busy=true;setVisitLocked(true);
    try{
      if(await rememberVisit(true)){
        $('visit-dialog').close();
        syncNote='Осмотр сохранён на устройстве и ждёт отправки.';
        renderFieldDrafts();
        syncVisits();
      }else setVisitLocked(false);
    }finally{S.busy=false;context();}
  });
  async function applyRoute(){
    const run=++routeRun;
    const [name,query='']=location.hash.slice(1).split('?'),params=new URLSearchParams(query),fid=params.get('area'),period=params.get('period');
    if(!name||name==='today'){go(6,false);writeRoute(true);return;}
    saveDraft();if(S.data.items.some(v=>v.id===fid))S.fid=fid;
    if(S.data.periods.some(v=>v.id===period))$('period').value=period;
    S.metric=['ndvi','ndmi'].includes(params.get('metric'))?params.get('metric'):'ndmi';S.relative=null;S.selectedLocation=null;S.series=null;
    S.task=S.data.tasks.find(t=>t.id===params.get('task')&&t.payload.place.fragment===S.fid)||null;
    if(name==='history'&&S.task&&(!S.task.payload.evidence||offlineCached)){
      ++detailRun;++locationRun;S.detail=null;S.locationPending=false;S.locationConfirmed=false;
      go(4,false);writeRoute(true);return;
    }
    highlightMap();renderAreaList();journalCounts();context();const wantedTask=S.task;await loadCommon(params.get('before'),params.get('after'),false);if(run!==routeRun)return;S.task=wantedTask;
    const point=params.get('point');if(S.detail?.pair&&point){if(point==='reference'){S.locationConfirmed=true;renderLocation();}else{try{const value=JSON.parse(point);if(Array.isArray(value)&&value.length===2&&value.every(v=>Number.isFinite(v)&&v>=0&&v<=1))await chooseLocation(value);}catch{message('Место из ссылки не удалось восстановить. Выберите его заново.',true);}}}
    S.explorer?.setLayer(S.metric);
    if(run!==routeRun)return;
    const step=routes.indexOf(name);go(step>0&&(step===5||step===4||step===1||S.detail?.pair)?step:1,false);writeRoute(true);
  }
  window.addEventListener('popstate',()=>{if(S.data&&!S.busy){if($('visit-dialog').open)$('visit-dialog').close();applyRoute();}});
  try{
    if(navigator.serviceWorker){
      navigator.serviceWorker.register('/scouting/offline.js',{scope:'/'}).then(()=>navigator.serviceWorker.ready).then(()=>{
        offlineReady=true;
        if(S.data)renderFieldDrafts();
      }).catch(()=>{
        syncNote='Не удалось подготовить страницу для повторного открытия без связи.';
        if(S.data)renderFieldDrafts();
      });
    }
    const state=api('/api/scouting/state').catch(async error=>{
      if(error.status&&error.status<500)throw error;
      const cached=await visitStorage.workspace();
      if(!cached)throw error;
      offlineCached=true;
      return cached;
    });
    const[data,publicData]=await Promise.all([state,api('/scouting/assets/public-context.json')]);S.data=data;S.public=publicData;
    $('snapshot-date').textContent=date(data.snapshot_at);$('area-count').textContent=String(data.items.length);
    for(const period of data.periods)for(const id of ['period','period-mobile','history-period']){const option=el('option',period.id==='recent2026'?'Июль–октябрь 2026':period.label,$(id));option.value=period.id;}
    for(const item of data.items){const option=el('option','Площадь '+item.id,$('manual-task-area'));option.value=item.id;}
    $('period').value=data.default_period||'recent2026';$('history-period').value=$('period').value;
    S.planner=window.ScoutingPlans.create({api,getData:()=>S.data,onChange:context,onNotice:message,onRefresh:data=>{S.data=data;},onOpenTask:task=>{
      const p=task.payload;
      if(!p.evidence){openSavedTask(task);return;}
      history.pushState(null,'','#history?'+new URLSearchParams({area:p.place.fragment,period:p.period,metric:p.metric,task:task.id,before:p.evidence.before.id,after:p.evidence.after.id}));
      applyRoute();
    }});
    renderMap();$('workspace').hidden=false;$('loading').hidden=true;renderFieldDrafts();await applyRoute();syncVisits();
  }catch(e){$('loading').textContent='Не удалось открыть рабочее пространство. '+e.message;message('Архив или журнал недоступен. Демонстрационные данные не подставлены.',true);}
})();
