'use strict';
(async()=>{
 const $=id=>document.getElementById(id),NS='http://www.w3.org/2000/svg';
 const fmt=(v,d=1)=>v===null||v===undefined||!Number.isFinite(v)?'нет данных':v.toFixed(d).replace('.',',');
 const range=(a,b,d=1)=>a===null||a===undefined||b===null||b===undefined?'недостаточно данных':`${fmt(a,d)}…${fmt(b,d)}`;
 const unitRange=(a,b,unit,d=1)=>a===null||a===undefined||b===null||b===undefined?'недостаточно данных':`${range(a,b,d)} ${unit}`;
 const date=v=>new Date(v).toLocaleDateString('ru-RU',{timeZone:'UTC'});
 const datetime=v=>`${date(v)} ${new Date(v).toLocaleTimeString('ru-RU',{timeZone:'UTC',hour12:false})} UTC`;
 const el=(tag,text,parent,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;if(parent)parent.appendChild(n);return n};
 const tag=(m,parent)=>el('span',m.robust?'Устойчиво в сценариях':!m.core_supported?'Недостаточно ядра':!m.all_shifts_available?'Не все сдвиги доступны':'Чувствительно к границе',parent,'context-tag'+(m.robust?' stable':''));
 const layerNames={terrain:'DSM · шкала 0–100 м; сетка расчёта 30 м; включает растительность и здания',clay:'Глина · 15–55%; внешняя модель 0–5 см, нативная сетка 250 м',ph:'pH · 5,5–8,5; внешняя модель 0–5 см, нативная сетка 250 м'};
 $('context-layer').addEventListener('change',e=>{const v=e.target.value,n=$('context-raster');$('context-scale').hidden=v==='none';if(v==='none'){n.setAttribute('visibility','hidden');$('context-layer-legend').textContent='Оценки относятся к неподтверждённым обводкам.';return}n.setAttribute('href',`/farm-reconstruction/context-${v}.png`);n.setAttribute('visibility','visible');$('context-scale').src=`/farm-reconstruction/context-${v}-scale.png`;$('context-layer-legend').textContent=layerNames[v]});
 $('context-raster').addEventListener('error',()=>{$('context-raster').setAttribute('visibility','hidden');$('context-layer-legend').textContent='Слой недоступен. Сводку по нему не подставляем из другой области.'});
 let selected=null,data=null;
 document.addEventListener('fragment-selected',e=>{selected=e.detail;if(data&&data.features[selected]){$('context-fragment').value=selected;render()}});
 function chart(name,rows,period,parent){
  const article=el('article',undefined,parent);el('h4',name.toUpperCase(),article);
  const svg=document.createElementNS(NS,'svg');svg.setAttribute('viewBox','0 0 560 260');svg.setAttribute('role','img');svg.setAttribute('aria-label',`${name.toUpperCase()}: медианы по датам, диапазон сценариев и пропуски`);article.appendChild(svg);
  const add=(tag,attrs,text)=>{const n=document.createElementNS(NS,tag);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,v);if(text!==undefined)n.textContent=text;svg.appendChild(n);return n};
  const t0=Date.parse(period.start),t1=Date.parse(period.end),lo=-1,hi=1;
  const x=t=>50+480*(Date.parse(t)-t0)/(t1-t0),y=v=>210-170*(v-lo)/(hi-lo);
  const colour=name==='ndvi'?'#257d53':'#4676aa';
  for(let k=0;k<4;k++){const v=lo+(hi-lo)*k/3;add('line',{x1:50,y1:y(v),x2:530,y2:y(v),stroke:'#e3e8dd'});add('text',{x:40,y:y(v)+4,'text-anchor':'end','font-size':11,fill:'#7a8777'},fmt(v,1))}
  add('text',{x:50,y:248,'font-size':10,fill:'#778573'},date(period.start));add('text',{x:530,y:248,'text-anchor':'end','font-size':10,fill:'#778573'},date(period.end));
  const title=(n,text)=>{const t=document.createElementNS(NS,'title');t.textContent=text;n.appendChild(t)};
  const present=rows.filter(r=>r.processing_baseline);
  for(let i=1;i<present.length;i++)if(present[i].processing_baseline!==present[i-1].processing_baseline){
   const xx=x(present[i].acquired_at_utc);add('line',{x1:xx,y1:32,x2:xx,y2:213,stroke:'#bba571','stroke-dasharray':'4 4'});
   const n=add('text',{x:Math.min(xx+4,455),y:25,'font-size':9,fill:'#8a784f'},`PB ${present[i].processing_baseline}`);title(n,`Смена версии обработки ${present[i-1].processing_baseline} → ${present[i].processing_baseline}`);
  }
  for(const row of rows){const m=row[name],xx=x(row.acquired_at_utc);if(!m||m.base===null){const n=add('path',{d:`M${xx-3},221L${xx+3},227M${xx+3},221L${xx-3},227`,stroke:'#a9aea5','stroke-width':1.5});title(n,`${date(row.acquired_at_utc)}: нет пригодной сводки`);continue}
   if(m.all_shifts_available){const n=add('line',{x1:xx,y1:y(m.shift_min),x2:xx,y2:y(m.shift_max),stroke:colour,'stroke-width':3,'stroke-opacity':.45});title(n,`${date(row.acquired_at_utc)} · сдвиги ${range(m.shift_min,m.shift_max,3)}`)}
   const n=add('circle',{cx:xx,cy:y(m.base),r:3.2,fill:m.robust?colour:'#fff',stroke:colour,'stroke-width':1.5});title(n,`${datetime(row.acquired_at_utc)} · ${fmt(m.base,3)} · ${m.robust?'устойчиво в сценариях':'чувствительность или недостаточно данных'}`);
  }
  el('p','Закрашено: устойчиво во всех проверенных сценариях границ. Пустая точка: без отметки устойчивости. ×: пропуск. В один день возможны несколько съёмок.',article,'context-chart-note');
 }
 function renderAll(periodId){
  const body=$('context-all');body.replaceChildren();
  for(const [id,item]of Object.entries(data.features)){
   const tr=el('tr',undefined,body),m=item.dsm.height,av=item.availability[periodId];el('td',id,tr);el('td',`${fmt(m.base,1)} м`,tr);
   el('td',`${range(m.shift_min,m.shift_max,1)} м`,tr);tag(m,el('td',undefined,tr));
   el('td',`${av.base_usable} / ${av.total_full_extent}`,tr);el('td',`${av.robust_both_indices} / ${av.total_full_extent}`,tr);
  }
 }
 function render(){
  const item=data.features[selected],period=data.protocol.periods.find(p=>p.id===$('context-period').value),depth=$('context-depth').value;
  if(!item||!period)return;renderAll(period.id);
  const terrain=$('context-terrain');terrain.replaceChildren();
  for(const [title,m,unit,d]of [['Медиана высоты поверхности',item.dsm.height,'м',1],['P90 уклона поверхности',item.dsm.slope,'°',1]]){
   const a=el('article',undefined,terrain);el('h3',title,a);el('strong',`${fmt(m.base,d)} ${unit}`,a);tag(m,a);
   el('p',`Девять положений: ${unitRange(m.shift_min,m.shift_max,unit,d)}. С ядром и расширением: ${unitRange(m.all_scenario_min,m.all_scenario_max,unit,d)}.`,a);
  }
  const a=el('article',undefined,terrain);el('h3','Поддержка внутри площади',a);el('strong',`${fmt(item.core_area_ha,1)} га`,a);el('p',`Геометрическое ядро после сужения на ${item.radius_m} м; это часть условной обводки. Пригодных ячеек DSM в ядре: ${item.dsm.core_cells}.`,a);el('p','Высота и уклон относятся к DSM. Холодные карманы, затопление и распределение воды здесь не установлены.',a);
  const body=$('context-soil');body.replaceChildren();const names={clay:'Глина',sand:'Песок',silt:'Пылеватая фракция',phh2o:'pH в воде',soc:'Органический углерод'};
  for(const layer of item.soil.filter(l=>l.depth===depth)){
   const tr=el('tr',undefined,body),m=layer.sensitivity,d=1;el('td',`${names[layer.property]} · ${layer.units==='g/kg'?'г/кг':layer.units}`,tr);
   const td=el('td',fmt(m.base,d),tr);el('small',`${layer.native_cells_intersected} нативных ячеек; эффективный вес ${fmt(layer.effective_cell_count,1)} яч.`,td);
   el('td',range(m.shift_min,m.shift_max,d),tr);el('td',range(m.all_scenario_min,m.all_scenario_max,d),tr);
   const span=layer.provider_cell_quantile_span;el('td',span?range(span[0],span[1],1):'нет данных',tr);tag(m,el('td',undefined,tr));
  }
  const rows=item.history.filter(r=>r.period===period.id&&r.source_status!=='outside_full_grid'),av=item.availability[period.id];
  $('context-availability').textContent=`${selected} · ${date(period.start)} — ${date(period.end)}. Полный охват: ${av.total_full_extent} съёмок на ${av.full_extent_calendar_days} календарных днях. Пригодно по обводке: ${av.base_usable} съёмок на ${av.base_usable_calendar_days} днях. Оба индекса устойчивы во всех сценариях границ: ${av.robust_both_indices} съёмок на ${av.robust_both_calendar_days} днях.`;
  const charts=$('context-charts');charts.replaceChildren();chart('ndvi',rows,period,charts);chart('ndmi',rows,period,charts);
  const dates=$('context-dates');dates.replaceChildren();
  for(const row of rows){const tr=el('tr',undefined,dates),td=el('td',datetime(row.acquired_at_utc),tr);el('small',row.id,td);el('small',row.processing_baseline?`Обработка ${row.processing_baseline}`:'Версия недоступна',td);
   el('td',row.coverage===undefined?'нет данных':`${Math.round(row.coverage*100)}% · ${row.valid_cells}/${row.base_cells} яч.`,tr);
   el('td',row.minimum_shift_coverage===undefined?'нет данных':`${Math.round(row.minimum_shift_coverage*100)}%`,tr);
   el('td',row.ndvi?fmt(row.ndvi.base,3):'нет данных',tr);el('td',row.ndmi?fmt(row.ndmi.base,3):'нет данных',tr);
   const state=row.source_status!=='ready'?'Ошибка получения; данные не подменены':!row.ndvi||row.ndvi.base===null?'Недостаточно пригодных ячеек':row.ndvi.robust&&row.ndmi.robust?'Устойчиво в сценариях':'Чувствительность / недостаточно ядра';el('td',state,tr);
  }
  const change=$('context-change');change.replaceChildren();el('h4','Что изменилось на общей пригодной части снимков',change);
  const pair=item.changes.find(c=>c.period===period.id);
  if(!pair?.available)el('p','Нет двух дат с достаточным покрытием. Изменение не вычислено.',change);
  else{
   const days=Math.round((Date.parse(pair.after)-Date.parse(pair.before))/86400000);el('p',`${date(pair.before)} → ${date(pair.after)} · общая пригодная часть по обводке ${Math.round(pair.common_coverage*100)}%. Выбраны первая и последняя достаточно покрытые даты периода; сравниваются одни и те же пиксели.`,change);
   const labels={increase:'Повышение сохраняется в сценариях',decrease:'Снижение сохраняется в сценариях',small:'Изменение остаётся в пределах ±0,05',uncertain:'Устойчивое направление не установлено'};
   for(const name of ['ndvi','ndmi']){const m=pair[name];el('p',`${name.toUpperCase()}: ${fmt(m.base,3)}; диапазон сдвигов ${range(m.shift_min,m.shift_max,3)}; с ядром и расширением ${range(m.all_scenario_min,m.all_scenario_max,3)}. ${labels[m.direction]}. Численная величина ${m.robust?'устойчива':'не получила отметку устойчивости'}.`,change)}
   if(!pair.same_processing_baseline)el('p','Версии обработки различаются: отметка устойчивого направления отключена.',change);
   el('p',`${days>14?`Между снимками ${days} дней: возможны сезонные изменения и операции хозяйства. `:''}Причина изменений, культура и агрономический диагноз не определены.`,change);
   const window=pair.same_baseline_comparison;
   if(window){
    el('h4','Отдельно: сопоставимое окно одной версии обработки',change);
    el('p',`${date(window.before)} → ${date(window.after)} · версия ${window.before_baseline}. Это часть выбранного периода. Взята последняя версия с двумя пригодными датами, затем первая и последняя даты внутри неё. Выбор не зависит от величины изменения. Общая пригодная часть: ${Math.round(window.common_coverage*100)}%.`,change);
    for(const name of ['ndvi','ndmi']){const m=window[name];el('p',`${name.toUpperCase()}: ${fmt(m.base,3)}; девять положений ${range(m.shift_min,m.shift_max,3)}; с ядром и расширением ${range(m.all_scenario_min,m.all_scenario_max,3)}. ${labels[m.direction]}. Это наблюдение поверхности, причина не установлена.`,change)}
   }
  }
 }
 try{
  const response=await fetch('/farm-reconstruction/context-data.json');if(!response.ok)throw Error('Сводка недоступна');data=await response.json();
  if(data.protocol.confirmed_boundary!==false||data.summary.used_by_model!==false)throw Error('Неверный статус исследовательских данных');
  const fragments=$('context-fragment');for(const id of Object.keys(data.features))el('option',id,fragments).value=id;
  const periods=$('context-period');for(const p of data.protocol.periods)el('option',`${p.label} · ${date(p.start)}–${date(p.end)}`,periods).value=p.id;
  periods.value='recent2026';selected=selected&&data.features[selected]?selected:Object.keys(data.features)[0];fragments.value=selected;
  const overview=$('context-overview');for(const [value,label]of [[`${data.summary.stable_dsm_count} / ${data.summary.fragment_count}`,'медиан DSM устойчивы в сценариях границ'],[`${data.summary.stable_topsoil_clay_count} / ${data.summary.fragment_count}`,'сводок глины 0–5 см численно устойчивы к границе'],[`${data.summary.satellite_counts.ready}`,'снимков с полным охватом, до проверки облаков по каждой площади']]){const n=el('div',undefined,overview);el('strong',value,n);el('span',label,n)}
  const c=data.summary.satellite_counts;$('context-source-status').textContent=`Каталог пройден полностью. Полный охват: ${c.ready} снимков; частичные соседние плитки: ${c.outside_full_grid} (сохранены в каталоге, исключены из общего ряда); ошибки получения: ${c.extraction_error}. Снимок набора получен ${date(data.protocol.created_at_utc)}; автоматического обновления нет.`;
  fragments.addEventListener('change',()=>{selected=fragments.value;document.dispatchEvent(new CustomEvent('fragment-request',{detail:selected}));render()});periods.addEventListener('change',render);$('context-depth').addEventListener('change',render);
  $('context-content').hidden=false;$('context-status').hidden=true;render();document.dispatchEvent(new CustomEvent('fragment-request',{detail:selected}));
 }catch(e){$('context-status').textContent=`Не удалось открыть сводку: ${e.message}. Данные прежнего окна у ориентира сюда не подставлены.`}
})();
