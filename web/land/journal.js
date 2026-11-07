/* User-entered evidence only. A draft's map target is frozen on explicit selection. */
(function(){
  'use strict';
  function inspectionTarget(data,window,metric,threshold){
    if(!data?.inspection_reference||!window)return null;
    return JSON.parse(JSON.stringify({reference:data.inspection_reference,window,before:data.before,after:data.after,metric,threshold}));
  }
  function targetKey(t){return t?JSON.stringify([t.reference,t.window.id,t.metric,t.threshold]):null;}
  function observedAtUTC(value){
    if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/.test(value))throw Error('Укажите дату и время фактического осмотра, МСК.');
    const d=new Date(value+'+03:00');if(!Number.isFinite(d.getTime()))throw Error('Проверьте дату осмотра.');return d.toISOString();
  }
  const helpers={inspectionTarget,targetKey,observedAtUTC};
  if(typeof module!=='undefined')module.exports=helpers;
  if(typeof document==='undefined')return;
  const $=id=>document.getElementById(id);
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const number=v=>typeof v==='number'&&Number.isFinite(v)?v.toLocaleString('ru-RU',{minimumFractionDigits:3,maximumFractionDigits:3}):'—';
  const date=v=>v?new Date(v).toLocaleString('ru-RU',{timeZone:'Europe/Moscow',day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'}):'Не указана';
  async function json(url,options){const r=await fetch(url,options);const b=await r.json();if(!r.ok)throw Error(b.error||'Запрос не выполнен.');return b;}
  function setup(options){
    let target=null,files=[],urls=[],requestId=null,saving=false,entries=[],places=[],historyData=null,historyChoice=null,historyRequest=0;
    function placeChoices(){
      const select=$('inspection-place'),known=places.map(g=>({key:g.place.key,label:window.LandPlaceHistory.placeLabel(g.place)+` · записей ${g.record_ids.length}`}));
      if(historyData&&!known.some(p=>p.key===historyData.place.key))known.unshift({key:historyData.place.key,label:window.LandPlaceHistory.placeLabel(historyData.place)+' · записей 0'});
      select.innerHTML=known.length?known.map(p=>`<option value="${esc(p.key)}">${esc(p.label)}</option>`).join(''):'<option>Сначала выберите окно на карте</option>';
      select.disabled=!known.length;if(historyData)select.value=historyData.place.key;
    }
    async function showHistory(choice){
      const serial=++historyRequest;historyChoice=choice;historyData=null;
      $('place-history').innerHTML='';$('land-inspection-list').textContent='Загружаем историю выбранного места…';$('repeat-land-inspection').disabled=true;
      $('place-history-status').textContent='Проверяем снимки выбранного окна и даты посещений…';
      try{
        const url=choice.record?'/api/land/inspections/'+choice.record+'/history':'/api/land/inspections/history?'+new URLSearchParams({reference:JSON.stringify(choice.target.reference),window:choice.target.window.id});
        const d=await json(url);if(serial!==historyRequest)return;historyData=d;placeChoices();
        $('place-history').innerHTML=window.LandPlaceHistory.view(d);
        $('place-history-status').textContent=d.visits.length?`История одного окна: ${d.visits.length} записей. Соседние места исключены.`:'У этого места ещё нет осмотров. Показаны только реальные спутниковые съёмки; полевые наблюдения не добавлены.';
        $('repeat-land-inspection').disabled=false;render();
      }catch(err){if(serial!==historyRequest)return;$('place-history-status').textContent=err.message+' История не заменена другими местами или вымышленными значениями.';$('land-inspection-list').textContent='Для показа записей повторите загрузку выбранного места.';}
    }
    function dirty(){return Boolean(files.length||$('land-inspection-actor').value||$('land-inspection-at').value||$('land-inspection-notes').value);}
    function message(text,error=false){$('inspection-form-status').textContent=text;$('inspection-form-status').className=error?'error':'';}
    function clear(){
      for(const u of urls)URL.revokeObjectURL(u);urls=[];files=[];target=null;requestId=null;
      $('land-inspection-form').reset();$('land-inspection-fields').disabled=true;$('inspection-target').hidden=true;$('inspection-photo-preview').innerHTML='';
    }
    function previews(){
      for(const u of urls)URL.revokeObjectURL(u);urls=files.map(f=>URL.createObjectURL(f));
      $('inspection-photo-preview').innerHTML=files.map((f,i)=>`<figure><img src="${urls[i]}" alt="Выбранное приложение: ${esc(f.name)}"><figcaption>${esc(f.name)} · ${(f.size/1000000).toFixed(2)} МБ</figcaption><button type="button" data-remove-photo="${i}">Убрать фото</button></figure>`).join('');
      $('inspection-photo-preview').querySelectorAll('[data-remove-photo]').forEach(b=>b.addEventListener('click',()=>{files.splice(Number(b.dataset.removePhoto),1);previews();}));
    }
    function prepare(next){
      if(saving){message('Дождитесь завершения сохранения.',true);return;}
      if(!next){message('Сначала выберите окно в сравнении снимков.',true);return;}
      if(dirty()&&targetKey(target)!==targetKey(next)){
        message('В черновике уже есть данные другого места или сравнения. Сохраните его или очистите перед выбором нового места.',true);
        $('land-journal').scrollIntoView({behavior:'smooth',block:'start'});return;
      }
      target=JSON.parse(JSON.stringify(next));requestId=requestId||crypto.randomUUID().replaceAll('-','');
      const c=target.window.centre;
      $('inspection-target').innerHTML=`<p><strong>Окно 60×60 м: ${c.latitude.toFixed(5)}, ${c.longitude.toFixed(5)}</strong></p><p>A: ${esc(date(target.before.acquired_at_utc))} → B: ${esc(date(target.after.acquired_at_utc))} МСК</p><p>ΔNDVI ${number(target.window.ndvi.median)} · ΔNDMI ${number(target.window.ndmi.median)} · ${target.window.paired_cells}/9 общих пикселей.</p><p>Настройка отбора: ${target.metric.toUpperCase()}, |Δ| ≥ ${target.threshold.toFixed(2)}. Место выбрано на карте, GPS-посещение не подтверждено.</p>`;
      $('inspection-target').hidden=false;$('land-inspection-fields').disabled=false;
      message('Место и пара снимков закреплены в черновике. Введите фактический осмотр; дата, автор и заметки не заполнены автоматически.');
      showHistory({target:JSON.parse(JSON.stringify(target))});
      $('land-journal').scrollIntoView({behavior:'smooth',block:'start'});
    }
    function relation(p){
      const hours=(Date.parse(p.observed_at_utc)-Date.parse(p.after.acquired_at_utc))/3600000;
      return hours>=0?`Осмотр на ${(hours/24).toFixed(1)} дн. позже снимка B.`:`Осмотр на ${(-hours/24).toFixed(1)} дн. раньше снимка B.`;
    }
    function render(){
      const visits=historyData?.visits||[];
      if(!visits.length){$('land-inspection-list').innerHTML='<p class="note">Осмотров этого места пока нет. Реальные наблюдения, автор, дата и фотографии появятся только после ручного сохранения записи.</p>';return;}
      $('land-inspection-list').innerHTML=visits.map((r,index)=>{
        const p=r.payload,w=p.selected_window,c=w.centre;
        return `<article class="inspection-entry"><h4>№${index+1} · ${esc(date(p.observed_at_utc))} МСК · ${esc(p.actor)}</h4><p class="entry-context">Окно 60×60 м: ${c.latitude.toFixed(5)}, ${c.longitude.toFixed(5)} · указано автором, не проверено автоматически.<br>Записано ${esc(date(r.recorded_at_utc))} МСК. ${esc(relation(p))}</p><p class="entry-notes">${esc(p.notes)}</p><p class="entry-context">Сохранённая пара A ${esc(date(p.before.acquired_at_utc))} → B ${esc(date(p.after.acquired_at_utc))} МСК · поддержка ${w.paired_cells}/9. Фильтр ${p.metric.toUpperCase()} ≥ |${p.display_threshold.toFixed(2)}|: ${p.passed_inspection_filter?'окно было выделено':'окно не было выделено'}.</p><div class="value-grid">${['ndvi','ndmi'].map(k=>`<div><small>Δ${k.toUpperCase()} · при сохранении</small><strong>${number(w[k].median)}</strong><span>Сдвиги B: ${w[k].shift_range?w[k].shift_range.map(number).join('…'):'нет полной поддержки'} · ${w[k].shift_scenarios}/9 сценариев</span></div>`).join('')}</div><div class="photo-grid">${p.photos.map(photo=>`<figure><a href="/api/land/inspections/${r.id}/photos/${photo.id}" target="_blank" rel="noopener"><img loading="lazy" src="/api/land/inspections/${r.id}/photos/${photo.id}" alt="Приложено автором: ${esc(photo.original_name)}"></a><figcaption>${esc(photo.original_name)} · дата и GPS фотографии не проверены</figcaption></figure>`).join('')}</div>${p.photos.length?'':'<p class="note">Фотографии к этому осмотру не приложены.</p>'}<button type="button" data-open-inspection="${r.id}">Показать место и сохранённое сравнение</button>${window.LandPlaceHistory.between(historyData,index)}<p class="inspection-compare-note">Индексы и сообщение автора показаны рядом; причинная связь не установлена. Запись не участвует в расчёте качества прогноза.</p></article>`;
      }).join('');
      $('land-inspection-list').querySelectorAll('[data-open-inspection]').forEach(button=>button.addEventListener('click',async()=>{
        button.disabled=true;try{const record=visits.find(r=>r.id===button.dataset.openInspection);const d=await json('/api/land/inspections/'+record.id+'/comparison');options.onOpenSaved(d,record);}catch(err){message(err.message,true);}finally{button.disabled=false;}
      }));
      $('land-inspection-list').querySelectorAll('img').forEach(img=>img.addEventListener('error',()=>{img.alt='Фотография недоступна или не прошла проверку; исходная запись сохранена.';}));
    }
    async function load(){
      try{const d=await json('/api/land/inspections');entries=d.items;places=d.places;placeChoices();
        if(historyChoice)await showHistory(historyChoice);
        else if(entries.length)await showHistory({record:entries[0].id});
        else render();}
      catch(err){$('land-inspection-list').textContent=err.message+' Список не заменён учебными данными.';}
    }
    $('land-inspection-photos').addEventListener('change',e=>{
      const chosen=Array.from(e.target.files);e.target.value='';
      if(files.length+chosen.length>3){message('Можно приложить до трёх фотографий.',true);return;}
      if(chosen.some(f=>!['image/jpeg','image/png','image/webp'].includes(f.type)||!f.size||f.size>5000000)){
        message('Нужны JPEG, PNG или WebP до 5 МБ каждый. Уже выбранные файлы сохранены.',true);return;
      }
      files.push(...chosen);previews();message('Фото выбраны. Они попадут в журнал только после сохранения осмотра.');
    });
    $('clear-land-inspection').addEventListener('click',()=>{if(!saving){clear();message('Черновик очищен. Выберите место на карте для новой записи.');}});
    $('reload-land-inspections').addEventListener('click',load);
    $('inspection-place').addEventListener('change',e=>{
      const g=places.find(p=>p.place.key===e.target.value);
      if(g)showHistory({record:g.record_ids[0]});
    });
    $('repeat-land-inspection').addEventListener('click',async()=>{
      if(!historyChoice||!historyData)return;
      const choice=historyChoice,serial=historyRequest;
      try{
        if(choice.target)prepare(choice.target);
        else {
          const record=entries.find(r=>r.id===choice.record),d=await json('/api/land/inspections/'+record.id+'/comparison');
          if(serial!==historyRequest)return;
          const w=d.blocks.find(b=>b.id===record.payload.selected_window.id);
          prepare(inspectionTarget(d,w,record.payload.metric,record.payload.display_threshold));
        }
      }catch(err){message(err.message,true);}
    });
    $('land-inspection-form').addEventListener('submit',async e=>{
      e.preventDefault();if(!target||saving)return;saving=true;$('land-inspection-fields').disabled=true;message('Проверяем запись и фотографии…');
      try{
        const photos=[];
        for(const file of files){const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(Error('Не удалось прочитать фото.'));reader.readAsDataURL(file);});photos.push({filename:file.name,data_base64:data});}
        const body={request_id:requestId,reference:target.reference,window_id:target.window.id,metric:target.metric,threshold:target.threshold,
          actor:$('land-inspection-actor').value,observed_at:observedAtUTC($('land-inspection-at').value),notes:$('land-inspection-notes').value,photos};
        const state=await json('/api/land/status');
        const saved=await json('/api/land/inspections',{method:'POST',headers:{'Content-Type':'application/json','X-Field-Token':state.token},body:JSON.stringify(body)});
        clear();message(`Осмотр сохранён с исходной парой снимков. Фотографий: ${saved.payload.photos.length}.`);await load();
      }catch(err){message(err.message+' Черновик сохранён в этой вкладке. Проверьте журнал перед повторной отправкой.',true);}
      finally{saving=false;$('land-inspection-fields').disabled=!target;}
    });
    window.addEventListener('beforeunload',e=>{if(dirty()){e.preventDefault();e.returnValue='';}});
    function showPlace(next){
      if(!next)return;
      showHistory({target:JSON.parse(JSON.stringify(next))});
      $('place-history-status').scrollIntoView({behavior:'smooth',block:'start'});
    }
    return {prepare,load,showPlace};
  }
  window.LandJournal={setup,inspectionTarget};
})();
