(function(){
  'use strict';
  function displayValue(v,unit){return v===null?'':String(unit==='fraction'?Math.round(v*1000000)/10000:v);}
  function wireValue(v,unit){if(v==='')return null;const n=Number(v);if(!Number.isFinite(n))throw Error('Проверьте численные критерии');return unit==='fraction'?n/100:n;}
  if(typeof module!=='undefined')module.exports={displayValue,wireValue};
  if(typeof document==='undefined')return;
  const $=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const groups={technical:'Надёжность сбора',descriptive_review:'Предварительная оценка',human_preparation_support:'Помощь при подготовке',correction:'Преимущество новой коррекции',time_and_range:'Время и диапазон'};
  function setup(options){
    let ctx=null,dirty=false,busy=false,requestId=null,requestBody=null;
    function status(s){$('plan-editor-status').textContent=s;}
    function toggle(){
      const s=ctx?.sensors.find(s=>s.id===$('plan-sensor').value);
      const usable=s&&s.measurement_type==='air_2m'&&s.height_m!==null&&Math.abs(s.height_m-2)<=.2&&s.calibration_status==='checked'&&s.matches_profile_reference;
      $('plan-fix').disabled=busy||!usable||!ctx?.site_has_identity_and_geometry;
      $('plan-sensor-note').textContent=!ctx?.sensors.length?'Датчиков этого участка пока нет. Можно сохранить черновик с незаданной позицией. Для фиксации зарегистрируйте реальный прибор в паспорте.':!usable?'Для фиксации выберите проверенный датчик воздуха около 2 м, назначенный опорным в паспорте. Изменение паспорта выполняется в его вкладке.':'Выбран проверенный по сведениям паспорта опорный датчик. Его точность и установка не проверяются этой формой автоматически.';
    }
    function render(c){
      ctx=c;dirty=false;requestId=null;requestBody=null;const p=c.plan;
      $('plan-start').value=p.scope.start||'';$('plan-end').value=p.scope.end||'';$('plan-season').value=p.scope.validation_season;
      $('plan-orchard').checked=p.scope.orchard_confirmed;$('plan-agreement').checked=false;$('plan-actor').value='';$('plan-note').value='';
      $('plan-sensor').innerHTML='<option value="">Опорный датчик пока не задан</option>'+c.sensors.map(s=>`<option value="${esc(s.id)}">${esc(s.id)} · ${esc({air_2m:'воздух около 2 м',canopy:'крона',soil:'почва'}[s.measurement_type]||s.measurement_type)} · ${s.height_m===null?'высота неизвестна':s.height_m+' м'} · ${s.calibration_status==='checked'?'проверка указана':'проверка не указана'}${s.matches_profile_reference?' · опорный в паспорте':''}</option>`).join('');
      if(p.scope.reference_sensor_id&&!c.sensors.some(s=>s.id===p.scope.reference_sensor_id))$('plan-sensor').innerHTML+=`<option value="${esc(p.scope.reference_sensor_id)}">${esc(p.scope.reference_sensor_id)} · отсутствует в текущем паспорте</option>`;
      $('plan-sensor').value=p.scope.reference_sensor_id||'';
      $('plan-criteria-inputs').innerHTML=Object.entries(groups).map(([group,label])=>`<details><summary>${label}</summary><div class="plan-grid">${c.schema.filter(s=>s.key.startsWith(group+'.')).map(s=>{
        const ratio=s.unit==='fraction',unit=ratio?'%':s.unit,min=ratio?s.min*100:s.min,max=ratio?s.max*100:s.max;
        return `<label>${esc(s.label)} · ${unit}<input type="number" data-plan-limit="${s.key}" min="${min}" max="${max}" step="${['ночей','групп','случаев'].includes(unit)?'1':'any'}" value="${displayValue(c.criteria_values[s.key],s.unit)}" ${s.nullable?'':'required'}></label>`;}).join('')}</div></details>`).join('');
      $('plan-version').innerHTML='<option value="">Актуальная версия по умолчанию</option>'+c.versions.map(v=>`<option value="${v.id}">${v.action==='fix'?'Зафиксировано':'Черновик'} · ${new Date(v.recorded_at_utc).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'})} · ${esc(v.actor)}</option>`).join('');
      $('plan-version').value=c.selected_version||'';
      const chosen=c.selected_record;
      $('plan-record-summary').innerHTML=chosen?`<p><strong>${chosen.action==='fix'?'Зафиксированные условия':'Черновик'} · ${esc(chosen.actor)}</strong> · ${new Date(chosen.recorded_at_utc).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'})} МСК</p><p class="plan-record-note">${esc(chosen.note||'Основание не указано')}</p><p class="note">Показана запись автора. Она не подтверждает его личность и не даёт допуска модели.</p>`:'<p class="note">Сохранённых настроек пока нет. Показан исходный проект численных критериев.</p>';
      $('plan-history').innerHTML=c.versions.length?c.versions.map(v=>`<p><strong>${v.action==='fix'?'Условия зафиксированы':'Черновик'}</strong> · ${esc(v.actor)} · ${new Date(v.recorded_at_utc).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'})} МСК<br>${esc(v.start||'начало не задано')} → ${esc(v.end||'конец не задан')}<br><small>Версия ${v.id} · SHA-256 ${v.sha256}</small></p>`).join(''):'<p>Сохранённых версий пока нет. Исходный проект не является согласованием.</p>';
      status('Изменения создадут новую версию. Автор и подтверждение согласования не заполняются автоматически.');toggle();
    }
    function changed(){dirty=true;requestId=null;requestBody=null;status('Есть несохранённые изменения настройки. Выбор отчёта не меняет сохранённые версии.');toggle();}
    $('plan-form').addEventListener('input',changed);$('plan-form').addEventListener('change',changed);
    $('plan-form').addEventListener('submit',e=>{e.preventDefault();save('draft');});$('plan-fix').addEventListener('click',()=>save('fix'));
    $('plan-reset').addEventListener('click',()=>{if(ctx&&!busy)render(ctx);});
    $('plan-version').addEventListener('change',e=>options.onVersion(e.target.value||null));
    async function save(action){
      if(!ctx||busy)return;
      if(options.canSave&&!options.canSave()){status('Сначала сохраните или очистите выбранный CSV. Его проверка связана с прежней версией периода.');return;}
      const invalid=Array.from($('plan-form').querySelectorAll('input,select,textarea')).find(e=>!e.checkValidity());
      if(invalid?.closest('details'))invalid.closest('details').open=true;
      if(!$('plan-form').reportValidity())return;
      try{
        const criteria={};for(const spec of ctx.schema){const input=$('plan-criteria-inputs').querySelector(`[data-plan-limit="${spec.key}"]`);criteria[spec.key]=wireValue(input.value,spec.unit);}
        const body={profile_id:ctx.profile_id,zone_id:ctx.zone_id,profile_revision:ctx.profile_revision,expected_latest:ctx.latest_version,source_version:ctx.selected_version,
          action,start:$('plan-start').value||null,end:$('plan-end').value||null,reference_sensor_id:$('plan-sensor').value||null,season:$('plan-season').value,orchard_confirmed:$('plan-orchard').checked,
          actor:$('plan-actor').value,agreement_note:$('plan-note').value,confirmed_agreement:$('plan-agreement').checked,criteria};
        const encoded=JSON.stringify(body);if(requestBody!==encoded){requestId=crypto.randomUUID().replaceAll('-','');requestBody=encoded;}
        body.request_id=requestId;busy=true;$('plan-fields').disabled=true;status('Проверяем и сохраняем новую версию…');
        const r=await fetch('/api/land/status'),state=await r.json();if(!r.ok)throw Error(state.error||'Не удалось получить токен сохранения');
        const response=await fetch('/api/site-validation/plans',{method:'POST',headers:{'Content-Type':'application/json','X-Field-Token':state.token},body:JSON.stringify(body)});
        const saved=await response.json();if(!response.ok)throw Error(saved.error||'Версия не сохранена');
        dirty=false;await options.onSaved(saved);status(action==='fix'?'Критерии зафиксированы отдельной версией. Допуск модели не выдан.':'Черновик сохранён отдельной версией. Согласование не подтверждено.');
      }catch(e){status(e.message+' Ввод оставлен в форме; проверьте список версий перед повтором.');}
      finally{busy=false;$('plan-fields').disabled=false;toggle();}
    }
    window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
    return {render,hasChanges:()=>dirty||busy};
  }
  window.ValidationPlanEditor={setup};
})();
