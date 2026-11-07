(function(){
  'use strict';
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const moment=t=>new Date(t).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'});
  const fmt=t=>new Date(t).toLocaleTimeString('ru-RU',{timeZone:'Europe/Moscow',hour:'2-digit',minute:'2-digit'});
  const num=v=>Number.isFinite(v)?v.toLocaleString('ru-RU',{maximumFractionDigits:2}):'—';
  const states={present:'Есть пригодный отсчёт',missing:'Нет пригодного отсчёта',pending:'Срок ещё не наступил'};
  const reasonLabels={temperature_incomplete:'Неполная температура',reference_sensor_unverified:'Опорный датчик не подтверждён',protection_used:'Применялась защита',protection_unknown:'Не указан статус защиты',night_pending:'Ночь ещё не завершилась',demo:'Демонстрационные данные',superseded_issue:'Есть более новая выдача'};
  const qc={accepted:'QC: принято',suspect:'QC: нужна проверка',rejected:'QC: исключено',duplicate:'QC: повтор'};
  function model(d){
    const start=Date.parse(d.start_at_utc),end=Date.parse(d.end_at_utc);
    const x=t=>65+(Date.parse(t)-start)/(end-start)*890;
    const samples=d.samples.filter(s=>Number.isFinite(s.temperature_c));
    const values=[0,...samples.map(s=>s.temperature_c),...(d.forecast?.hours||[]).map(s=>s.temperature_c).filter(Number.isFinite)];
    const low=Math.floor(Math.min(...values)-1),high=Math.ceil(Math.max(...values)+1),y=v=>290-(v-low)/(high-low)*250;
    const interval=d.sensor?.interval_minutes,gap=Number.isFinite(interval)?interval*60000+60000:0;
    function segments(rows,tkey,usable,maxGap){
      const out=[];let part=[];let previous=null;
      for(const r of rows){
        const t=Date.parse(r[tkey]);
        if(!usable(r)||!Number.isFinite(t)||t<start||t>=end){if(part.length)out.push(part);part=[];previous=null;continue;}
        if(previous!==null&&(t-previous>maxGap||t<previous)){if(part.length)out.push(part);part=[];}
        part.push({x:x(r[tkey]),y:y(r.temperature_c),value:r.temperature_c,source:r});previous=t;
      }
      if(part.length)out.push(part);return out;
    }
    return {low,high,x,y,zero:y(0),observed:segments(d.samples,'observed_at_utc',r=>r.usable&&Number.isFinite(r.temperature_c),gap),
      gfs:segments(d.forecast?.hours||[],'valid_at_utc',r=>Number.isFinite(r.temperature_c),3600001),
      excluded:samples.filter(r=>!r.usable).map(r=>({x:x(r.observed_at_utc),y:y(r.temperature_c),source:r})),
      missing:d.hourly.filter(r=>r.state==='missing').map(r=>({left:Math.max(65,x(r.valid_at_utc)-890/28),right:Math.min(955,x(r.valid_at_utc)+890/28)}))};
  }
  function chartHTML(d){
    const m=model(d),path=segments=>segments.map(s=>`<path d="${s.map((p,i)=>(i?'L':'M')+p.x.toFixed(2)+' '+p.y.toFixed(2)).join(' ')}"/>`).join('');
    const dots=m.observed.flat().map(p=>`<circle cx="${p.x}" cy="${p.y}" r="3"><title>${esc(fmt(p.source.observed_at_utc))} МСК · ${num(p.value)} °C · пригодный отсчёт</title></circle>`).join('');
    const excluded=m.excluded.map(p=>`<path d="M${p.x-4} ${p.y-4}l8 8 M${p.x-4} ${p.y+4}l8 -8"><title>${esc(fmt(p.source.observed_at_utc))} МСК · ${num(p.source.temperature_c)} °C · ${esc(p.source.reasons.join(' · '))}</title></path>`).join('');
    const grid=Array.from({length:6},(_,i)=>{const v=m.low+(m.high-m.low)*i/5,at=m.y(v);return `<line x1="65" x2="955" y1="${at}" y2="${at}"/><text x="55" y="${at+4}" text-anchor="end">${num(v)}</text>`;}).join('');
    const ticks=Array.from({length:15},(_,i)=>`<text x="${65+i*890/14}" y="315" text-anchor="middle">${String((19+i)%24).padStart(2,'0')}</text>`).join('');
    const slots=d.slots.map((s,i)=>`<rect class="slot-${s.state}" x="${65+i*890/168}" y="344" width="${890/168-.4}" height="15"><title>${esc(fmt(s.valid_at_utc))} МСК · ${esc(states[s.state])}</title></rect>`).join('');
    return `<div class="night-plot"><svg viewBox="0 0 1000 395" role="img" aria-label="Температура ночи ${esc(d.night)}, GFS и загруженные измерения" xmlns="http://www.w3.org/2000/svg"><title>Температура · ночь ${esc(d.night)}</title><desc>Часы по Москве. Зелёная серия — пригодные отсчёты, синий — сохранённый GFS, оранжевые кресты — исключённые измерения. Разрывы не заполняются. Оранжевые полосы отмечают отсутствующие часовые цели, нижняя полоса — пятиминутные слоты. Доступная таблица находится под графиком.</desc><g class="night-missing">${m.missing.map(r=>`<rect x="${r.left}" y="40" width="${r.right-r.left}" height="250"/>`).join('')}</g><g class="night-grid">${grid}</g><line class="night-zero" x1="65" x2="955" y1="${m.zero}" y2="${m.zero}"/><g class="night-gfs">${path(m.gfs)}</g><g class="night-observed">${path(m.observed)}${dots}</g><g class="night-excluded">${excluded}</g><g class="night-axis"><text x="24" y="22">°C</text>${ticks}<text x="955" y="334" text-anchor="end">Часы МСК · 09 — конец окна</text><text x="65" y="381">Слоты 5 мин: есть / нет / срок ещё не наступил</text></g>${slots}</svg></div>`;
  }
  function detailHTML(d){
    const c=d.counts;let forecast='Сохранённой выдачи GFS для этой ночи нет; линия прогноза не построена.';
    if(d.forecast)forecast=`GFS: выпуск ${moment(d.forecast.run_at_utc)} МСК, выдача ${moment(d.forecast.issued_at_utc)} МСК. ID ${d.forecast.id}. Показана последняя сохранённая выдача этой ночи, без подмены более старой.`;
    const admission=d.evidence?.included?'Ночь включена в описательную оценку отчёта; это не допуск модели.':'Ночь не включена в оценку: '+(d.evidence?.reasons.map(r=>reasonLabels[r]||r).join(' · ')||'нет сохранённого GFS');
    const rows=[...d.samples].sort((a,b)=>Number(a.usable)-Number(b.usable)).slice(0,200).map(r=>`<tr><td>${esc(fmt(r.observed_at_utc))}<br><small>${esc(r.observed_at_utc)}</small></td><td>${num(r.temperature_c)}</td><td>${r.usable?'Пригодна для сопоставления':'Исключена из принятой серии'}<p class="note">${esc(qc[r.status]||r.status)}${r.reasons.length?' · '+esc(r.reasons.join(' · ')):''}${r.review_note?' · Разбор: '+esc(r.review_note):''}</p></td><td>${esc(r.batch_id||'—')}<br>Строка ${esc(r.line??'—')}</td></tr>`).join('');
    const hourly=d.hourly.map(h=>`<tr><td>${esc(fmt(h.valid_at_utc))}</td><td>${esc(states[h.state])}</td><td>${h.observed_at_utc?esc(fmt(h.observed_at_utc)):'—'}</td><td>${num(h.temperature_c)}</td></tr>`).join('');
    const unknown=d.unplaced_count?`<details><summary>Записи без определимого срока: ${d.unplaced_count}</summary><p class="note">Их нельзя достоверно отнести к выбранной ночи. Показаны первые ${d.unplaced.length} записей выбранного датчика из журнала, без привязки к периоду.</p><ul>${d.unplaced.map(r=>`<li>${esc(r.raw_observed_at||'Нет времени')} · ${esc(r.reasons.join(' · '))} · пакет ${esc(r.batch_id||'—')}, строка ${esc(r.line??'—')}</li>`).join('')}</ul></details>`:'';
    return `<p class="night-context"><strong>${esc(d.selected.profile_name||'')} · ${esc(d.selected.zone_name||'')}</strong></p><p><strong>${esc(d.night)} · датчик ${esc(d.selected.reference_sensor_id||'не задан')}</strong> · записей ${c.rows}, пригодных ${c.usable}, исключённых ${c.excluded}. Часовых целей: ${c.hourly.present||0} из 14, нет ${c.hourly.missing||0}, ещё не наступили ${c.hourly.pending||0}.</p><p class="note night-provenance">${esc(forecast)}</p><p class="night-assessment">${esc(admission)}</p><p class="night-scroll-hint note">На узком экране график можно прокручивать по горизонтали.</p>${chartHTML(d)}<div class="night-legend"><span class="legend-gfs">━ GFS</span><span class="legend-accepted">● Пригодная температура</span><span class="legend-excluded">× Исключённая точка</span><span>▧ Нет часовой цели / слота</span><span>□ Будущий слот</span></div><p class="note">Непрерывные отрезки измерений соединены только в пределах заявленного шага датчика + 1 минута; при неизвестном шаге показаны точки. Отсутствующие часовые цели подсвечены полосами, пропуски не интерполируются. Исключённые измерения не входят в зелёную кривую. Отсчёты без численной температуры показаны только в таблице.</p><details><summary>14 часовых целей · пригодная температура в пределах ±5 минут</summary><div class="table-wrap"><table><thead><tr><th>Цель МСК</th><th>Состояние</th><th>Отсчёт МСК</th><th>°C</th></tr></thead><tbody>${hourly}</tbody></table></div></details><details><summary>Загруженные измерения и причины исключения · ${c.rows}</summary><p class="note">Таблица показывает первые ${Math.min(200,c.rows)} записей с приоритетом исключённых; график учитывает все ${c.rows}. Идентификаторы пакетов и номера строк помогают найти источник.</p><div class="table-wrap"><table><thead><tr><th>Время МСК / UTC</th><th>°C</th><th>Статус и основание</th><th>Источник CSV</th></tr></thead><tbody>${rows||'<tr><td colspan="4">Сохранённых измерений этой опорной позиции за ночь нет</td></tr>'}</tbody></table></div></details>${unknown}<ul class="note">${d.limitations.map(s=>'<li>'+esc(s)+'</li>').join('')}</ul>`;
  }
  if(typeof module!=='undefined')module.exports={model,chartHTML,detailHTML};
  if(typeof document==='undefined')return;
  const $=id=>document.getElementById(id);
  function setup(){
    let data=null,serial=0,active=null;
    function invalidate(){serial++;$('validation-night-detail').hidden=true;$('validation-night-detail').innerHTML='';}
    async function load(night){
      if(!data||!night)return;invalidate();const n=serial;active=night;
      $('validation-night-status').textContent='Читаем сохранённые данные ночи…';$('validation-night-export').hidden=true;
      try{
        const q=new URLSearchParams({profile_id:data.selected.profile_id,zone_id:data.selected.zone_id,version:data.plan.version_id,night});
        const url='/api/site-validation/night?'+q;const response=await fetch(url);const d=await response.json();if(n!==serial)return;
        if(!response.ok)throw Error(d.error||'Данные ночи недоступны');
        if(d.night!==night||d.version!==data.plan.version_id)throw Error('Ответ относится к другой ночи или версии');
        $('validation-night-detail').innerHTML=detailHTML(d);$('validation-night-detail').hidden=false;
        $('validation-night-export').href=url;$('validation-night-export').hidden=false;$('validation-night-status').textContent='Показаны сохранённые данные. Журнал, модель и статус допуска не изменены.';
      }catch(e){if(n===serial)$('validation-night-status').textContent=e.message+' Прежний график скрыт, учебные значения не подставлены.';}
    }
    function render(d){
      const same=data?.selected.profile_id===d.selected.profile_id&&data?.selected.zone_id===d.selected.zone_id&&data?.plan.version_id===d.plan.version_id;
      data=d;invalidate();$('validation-night-export').hidden=true;
      const choices=d.night_choices||[];const usable=Boolean(d.plan.version_id&&choices.length);
      $('validation-night').disabled=!usable;
      $('validation-night').innerHTML=usable?choices.map(n=>`<option value="${n.night}">${n.night} · измерений ${n.measurement_rows} · GFS ${n.has_gfs?'есть':'нет'}${n.completed?'':' · ночь не завершена'}</option>`).join(''):'<option value="">Период ещё не сохранён</option>';
      if(!usable){active=null;$('validation-night-status').textContent='Сначала сохраните период проверки. Ночи и график не заполнены демонстрационными данными.';return;}
      const preferred=same&&choices.some(n=>n.night===active)?active:(choices.find(n=>n.measurement_rows>0)||choices.find(n=>n.has_gfs)||choices[0]).night;
      $('validation-night').value=preferred;load(preferred);
    }
    $('validation-night').addEventListener('change',e=>load(e.target.value));
    return {render,invalidate};
  }
  window.ValidationNightChart={setup};
})();
