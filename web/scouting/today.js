'use strict';
/* «Сегодня»: field queue by draft disease-risk rules, farm-records import, inspection marks,
   and sensor-node siting factors. Everything goes through the local API; the example dataset
   is only previewed and never written to the journal. */
window.ScoutingToday={create({api,getData,onNotice,onChange,onOpenArea}){
  const $=id=>document.getElementById(id);
  const el=(tag,text,parent,cls)=>{const n=document.createElement(tag);if(text!==undefined&&text!==null)n.textContent=text;if(cls)n.className=cls;if(parent)parent.append(n);return n;};
  const uid=()=>crypto.randomUUID().replaceAll('-','');
  const day=v=>v?new Date(v+'T12:00:00Z').toLocaleDateString('ru-RU',{day:'numeric',month:'long'}):'—';
  const fullDay=v=>v?new Date(v+'T12:00:00Z').toLocaleDateString('ru-RU',{day:'numeric',month:'long',year:'numeric'}):'—';
  const STATUS={risk:'Риск',check:'Проверить',normal:'Норма',no_rules:'Нет правил',no_data:'Нет данных учёта'};
  const DEMO_DATE='2024-05-15';
  let source='example',date=DEMO_DATE,result=null,run=0,decided=false,recordsRequest=uid(),markRequest=uid(),markFid='',preview=null;

  const pressSource=()=>{for(const b of document.querySelectorAll('[data-today-source]'))b.setAttribute('aria-pressed',String(b.dataset.todaySource===source));};
  $('today-date').value=date;
  $('today-date').addEventListener('change',()=>{if($('today-date').value){date=$('today-date').value;render();}});
  for(const b of document.querySelectorAll('[data-today-source]'))b.addEventListener('click',()=>{source=b.dataset.todaySource;decided=true;pressSource();render();});
  pressSource();

  async function render(){
    const current=++run;
    // Use the farm's own records when they exist; otherwise start on the labelled example.
    if(!decided){decided=true;try{const probe=await api(`/api/scouting/risk?date=${date}&source=journal`);if(probe.source?.kind==='journal')source='journal';}catch{}pressSource();}
    $('today-list').setAttribute('aria-busy','true');
    try{result=await api(`/api/scouting/risk?${new URLSearchParams({date,source})}`);}
    catch(error){if(current!==run)return;result=null;$('today-list').replaceChildren();el('p',error.message,$('today-list'),'today-error');$('today-summary').replaceChildren();$('today-source').replaceChildren();$('today-side').replaceChildren();$('today-list').removeAttribute('aria-busy');return;}
    if(current!==run)return;
    $('today-list').removeAttribute('aria-busy');
    drawSource();drawSummary();drawList();drawSide();onChange?.();
  }

  function drawSource(){
    const box=$('today-source');box.replaceChildren();box.className='today-source '+(result.source.kind==='example'?'is-example':result.source.kind==='journal'?'is-journal':'is-none');
    const title=result.source.kind==='example'?'Демонстрационный пример — не данные хозяйства':result.source.kind==='journal'?'Данные учёта хозяйства':'Данных учёта пока нет';
    el('strong',title,box);
    const parts=[result.source.label];if(result.source.filename)parts.push(result.source.filename);if(result.source.imported_at)parts.push('загружено '+new Date(result.source.imported_at).toLocaleString('ru-RU',{timeZone:'Europe/Moscow',dateStyle:'medium',timeStyle:'short'}));
    el('span',parts.filter(Boolean).join(' · '),box);
    if(result.source.kind==='none'){const b=el('button','Загрузить выгрузку учёта →',box,'text-button');b.type='button';b.addEventListener('click',openRecords);}
  }

  function drawSummary(){
    const box=$('today-summary');box.replaceChildren();
    for(const key of ['risk','check','normal','no_data']){
      const n=key==='no_data'?(result.summary.no_data||0)+(result.summary.no_rules||0):result.summary[key]||0;
      const tile=el('div',null,box,'today-tile is-'+key);el('span',key==='no_data'?'Без данных или правил':STATUS[key],tile);el('strong',String(n),tile);
    }
    const when=el('div',null,box,'today-tile is-date');el('span','Расчёт на',when);el('strong',day(result.date),when);
  }

  function drawList(){
    const box=$('today-list');box.replaceChildren();
    const active=result.fields.filter(f=>!['no_data','no_rules'].includes(f.status)),rest=result.fields.filter(f=>['no_data','no_rules'].includes(f.status));
    if(!active.length){const empty=el('div',null,box,'today-empty');el('h3',result.source.kind==='none'?'Загрузите данные учёта':'Нет полей с правилами на эту дату',empty);el('p',result.source.kind==='none'?'Очередь строится по культурам, фазам и обработкам из учёта хозяйства. Пока можно открыть пример.':'Правила черновика охватывают озимые зерновые. Проверьте культуры и фазы в выгрузке.',empty);}
    for(const f of active)row(f,box);
    if(rest.length){const d=el('details',null,box,'today-rest');el('summary',`Ещё ${rest.length} обводок без данных учёта или правил`,d);el('p',rest.map(f=>f.id+(f.status==='no_rules'&&f.crop?' · '+f.crop:'')).join(', '),d);}
  }

  function row(f,box){
    const card=el('article',null,box,'today-row is-'+f.status);
    const head=el('div',null,card,'today-row-head');
    const id=el('div',null,head,'today-id');el('strong',f.id,id);el('span',STATUS[f.status]||f.status_label,id,'today-chip is-'+f.status);
    const meta=el('div',null,head,'today-meta');el('b',[f.field_name,f.crop].filter(Boolean).join(' · ')||f.id,meta);
    el('span',[f.phase?'фаза: '+f.phase+(f.phase_date?' с '+day(f.phase_date):''):null,f.sowing_date?'сев '+fullDay(f.sowing_date):null].filter(Boolean).join(' · '),meta);
    if(f.inspect_by&&f.status!=='normal'){const due=el('div',null,head,'today-due');el('span','Осмотреть до',due);el('strong',day(f.inspect_by),due);}
    const reasons=el('ul',null,card,'today-reasons');for(const r of f.reasons||[])el('li',r,reasons);
    const foot=el('div',null,card,'today-row-foot');
    el('span',f.data_source_label||'',foot,'today-source-label'+(String(f.data_source).startsWith('sensor:')?' is-sensor':''));
    if(f.last_fungicide)el('span','Фунгицид: '+(typeof f.last_fungicide==='string'?fullDay(f.last_fungicide):[f.last_fungicide.product,fullDay(f.last_fungicide.date)].filter(Boolean).join(', ')),foot,'today-source-label');
    if(f.marks?.length){const last=f.marks[f.marks.length-1];el('span',`Осмотр ${fullDay(last.observed_on)}: ${last.result==='focus'?'нашли очаг':'чисто'}`,foot,'today-mark is-'+last.result);}
    const actions=el('div',null,foot,'today-actions');
    const events=(f.diseases||[]).flatMap(d=>(d.events||[]).map(e=>({...e,name:d.name})));
    if(events.length){const det=el('details',null,card,'today-events');el('summary','Почасовое основание · '+events.length+(events.length===1?' эпизод':' эпизодов'),det);const t=el('table',null,det);const tr=el('tr',null,el('thead',null,t));for(const h of ['Болезнь','Дата','Часы влажного листа','Температура'])el('th',h,tr);const tb=el('tbody',null,t);for(const e of events){const r=el('tr',null,tb);el('td',e.name,r);el('td',fullDay(e.date),r);el('td',String(e.wet_hours)+' ч',r);el('td',(e.temp_min!=null?`+${Math.round(e.temp_min)}…${Math.round(e.temp_max)} °C`:'—'),r);}}
    const map=el('button','На карте',actions,'secondary');map.type='button';map.addEventListener('click',()=>onOpenArea?.(f.id));
    const mark=el('button','Отметить осмотр',actions,'primary');mark.type='button';mark.addEventListener('click',()=>openMark(f.id));
  }

  function drawSide(){
    const box=$('today-side');box.replaceChildren();
    const rules=el('section',null,box,'today-card');el('h3','Как считается',rules);
    el('p','Фаза из учёта × часы влажного листа при нужной температуре × защита фунгицидом (смыв дождём отменяет защиту).',rules);
    const list=el('ul',null,rules,'today-rules');for(const d of result.rules.diseases||[])el('li',`${d.name}: от ${d.wet_hours} ч влажного листа при +${d.temp_min}…${d.temp_max} °C → осмотр через ${d.inspect_after_days} дн.`,list);
    el('p',result.rules.note,rules,'today-draft');
    const w=el('section',null,box,'today-card');el('h3','Погода',w);el('p',result.weather.label,w);
    if(result.weather.coverage?.length)el('p','Доступно: '+result.weather.coverage.map(c=>`${fullDay(c.start)} — ${fullDay(c.end)}`).join('; '),w,'muted');
    const m=result.marks_summary||{};const ms=el('section',null,box,'today-card');el('h3','Проверка сигнала по осмотрам',ms);
    const g=el('dl',null,ms,'today-metrics');
    for(const[k,v]of [['Сигналы с отметкой осмотра',`${m.signals_with_marks??0} из ${m.signals_total??0}`],['Очаги после сигнала',`${m.focus_after_signal??0} из ${m.focus_total??0}`]]){const d=el('div',null,g);el('dt',k,d);el('dd',v,d);}
    if(result.limitations?.length){const l=el('details',null,box,'today-card today-limits');el('summary','Ограничения расчёта',l);const ul=el('ul',null,l);for(const x of result.limitations)el('li',x,ul);}
  }

  /* Farm records import */
  function openRecords(){recordsRequest=uid();preview=null;$('records-file').value='';$('records-preview').replaceChildren();$('records-confirm').checked=false;$('records-status').textContent='';updateSave();$('records-dialog').showModal();}
  function updateSave(){$('save-records').disabled=!preview||preview.errors?.length>0||!$('records-confirm').checked;}
  $('open-records').addEventListener('click',openRecords);
  $('close-records').addEventListener('click',()=>$('records-dialog').close());
  $('records-confirm').addEventListener('change',updateSave);
  $('records-file').addEventListener('change',async()=>{
    const file=$('records-file').files[0];preview=null;updateSave();const box=$('records-preview');box.replaceChildren();if(!file)return;
    if(file.size>2_500_000){el('p','Файл больше 2,5 МБ.',box,'records-error');return;}
    try{
      const text=await file.text();preview=await api('/api/scouting/records/preview',{filename:file.name,csv:text});preview.csv=text;preview.filename=file.name;
      const tiles=el('div',null,box,'records-counts');for(const[k,v]of [['Полей',preview.fields?.length??0],['Обработок',preview.treatments?.length??0],['Очагов',preview.foci?.length??0]]){const t=el('div',null,tiles);el('span',k,t);el('strong',String(v),t);}
      if(preview.errors?.length){const ul=el('ul',null,box,'records-errors');for(const e of preview.errors.slice(0,12))el('li',e,ul);}
      if(preview.warnings?.length){const ul=el('ul',null,box,'records-warnings');for(const e of preview.warnings.slice(0,8))el('li',e,ul);}
    }catch(error){el('p',error.message,box,'records-error');}
    updateSave();
  });
  $('save-records').addEventListener('click',async()=>{
    if(!preview)return;const button=$('save-records');button.disabled=true;$('records-status').textContent='Сохраняем…';
    try{await api('/api/scouting/records',{request_id:recordsRequest,filename:preview.filename,csv:preview.csv,confirm_farm_data:true});$('records-dialog').close();source='journal';decided=true;pressSource();onNotice?.('Данные учёта сохранены. Очередь пересчитана по ним.');render();}
    catch(error){$('records-status').textContent=error.message;updateSave();}
  });

  /* Inspection marks */
  function openMark(fid){
    markFid=fid;markRequest=uid();$('mark-fid').textContent=fid;$('mark-form').reset();$('mark-status').textContent='';
    $('mark-date').value=new Date().toLocaleDateString('sv-SE',{timeZone:'Europe/Moscow'});$('mark-date').max=$('mark-date').value;
    const example=result?.source.kind==='example';$('save-mark').disabled=example;
    $('mark-status').textContent=example?'Просмотр для демонстрации: на примере отметка не сохраняется. Отметки записываются для данных хозяйства после реального осмотра.':'';
    $('mark-dialog').showModal();
  }
  $('close-mark').addEventListener('click',()=>$('mark-dialog').close());
  $('mark-form').addEventListener('submit',async event=>{
    event.preventDefault();if(result?.source.kind==='example')return;
    const button=$('save-mark');button.disabled=true;$('mark-status').textContent='Сохраняем…';
    try{
      await api('/api/scouting/marks',{request_id:markRequest,fid:markFid,observed_on:$('mark-date').value,result:document.querySelector('[name="mark-result"]:checked')?.value,disease:$('mark-disease').value,notes:$('mark-notes').value,actor:$('mark-actor').value,real_visit_confirmed:$('mark-confirm').checked});
      $('mark-dialog').close();onNotice?.(`Отметка осмотра ${markFid} сохранена.`);render();
    }catch(error){$('mark-status').textContent=error.message;}finally{button.disabled=false;}
  });

  /* Sensor node siting for the selected outline (sensors tab of the selection card) */
  let sitingFid='',sitingRun=0;
  async function siting(){
    const fid=$('selected-area')?.textContent?.trim(),panel=$('siting-panel');
    if(!panel||!fid||panel.closest('[hidden]')||fid===sitingFid)return;
    sitingFid=fid;const current=++sitingRun;panel.replaceChildren();el('p','Подбираем места для узла…',panel,'muted');
    try{
      const s=await api('/api/scouting/siting?'+new URLSearchParams({fid}));if(current!==sitingRun)return;panel.replaceChildren();
      el('h4','Где поставить узел',panel);if(s.recommendation)el('p',s.recommendation,panel,'siting-reco');
      const f=el('ul',null,panel,'siting-factors');for(const x of s.factors||[]){const li=el('li',null,f,'is-'+x.status);el('b',x.name,li);el('span',x.summary,li);}
      const sites=el('ol',null,panel,'siting-sites');for(const x of s.sites||[]){const li=el('li',null,sites);el('b',`${x.rank}. ${({low:'Низина',high:'Возвышение',shelter:'У высоких объектов',focus:'У прошлого очага'})[x.kind]||x.kind}`,li);el('span',(x.reasons||[]).join(' · '),li);el('small',`${x.latitude.toFixed(5)}, ${x.longitude.toFixed(5)}`,li);}
      if(s.limitations?.length)el('p',s.limitations[0],panel,'data-footnote');
    }catch(error){if(current===sitingRun){panel.replaceChildren();el('p',error.message,panel,'muted');sitingFid='';}}
  }
  const watch=new MutationObserver(()=>siting());
  for(const id of ['selected-area'])if($(id))watch.observe($(id),{childList:true,characterData:true,subtree:true});
  for(const b of document.querySelectorAll('[data-aside]'))b.addEventListener('click',()=>setTimeout(siting,0));

  return {render,openRecords,note:()=>result?`Риск ${result.summary.risk||0} · проверить ${result.summary.check||0}`:'Поля по риску'};
}};
