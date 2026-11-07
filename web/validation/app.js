(function(){
  'use strict';
  const labels={met:'Численный порог выполнен',not_met:'Порог не выполнен',insufficient_data:'Недостаточно данных',not_agreed:'Не согласовано',not_available:'Нет сопоставимого кандидата',requires_review:'Нужен отдельный разбор'};
  function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
  function value(v,unit=''){if(v===null||v===undefined||!Number.isFinite(v))return '—';return unit==='fraction'?(100*v).toLocaleString('ru-RU',{maximumFractionDigits:1})+'%':v.toLocaleString('ru-RU',{maximumFractionDigits:3})+(unit?' '+unit:'');}
  function pair(a,b){return value(a)+' / '+value(b);}
  function criteriaHTML(rows,group='all'){return rows.filter(r=>group==='all'||r.group===group).map(r=>`<tr><td>${esc(r.label)}</td><td>${r.limit===null?'Требует подтверждения':(r.direction==='min'?'≥ ':'≤ ')+value(r.limit,r.unit)}</td><td>${value(r.value,r.unit)}</td><td><span class="criterion-state ${esc(r.status)}">${esc(labels[r.status]||'Не определено')}</span>${r.reason?`<p class="note">${esc(r.reason)}</p>`:''}</td></tr>`).join('');}
  const helpers={value,pair,criteriaHTML};if(typeof module!=='undefined')module.exports=helpers;
  if(typeof document==='undefined')return;
  const $=id=>document.getElementById(id);let data=null,serial=0;
  const editor=window.ValidationPlanEditor.setup({canSave:()=>!importer.hasChanges(),onSaved:saved=>load(saved.profile_id,saved.payload.zone_id,saved.id,true),onVersion:version=>load(data.selected.profile_id,data.selected.zone_id,version)});
  const importer=window.ValidationMeasurementImport.setup({isPlanDirty:()=>editor.hasChanges(),onSaved:version=>load(data.selected.profile_id,data.selected.zone_id,version,true)});
  const nightChart=window.ValidationNightChart.setup();
  const reasons={night_pending:'Ночь ещё не завершилась',reference_sensor_unverified:'Не подтверждён опорный датчик',temperature_incomplete:'Неполная температура',protection_used:'Применялась защита',protection_unknown:'Статус защиты неизвестен',superseded_issue:'Есть более новая выдача',demo:'Демонстрационные данные'};
  const qcLabels={accepted:'принято',suspect:'требует проверки',rejected:'исключено',duplicate:'повтор'};
  function row(items){return '<tr>'+items.map(v=>'<td>'+v+'</td>').join('')+'</tr>';}
  async function load(profile,zone,version=null,force=false){
    if(!force&&(editor.hasChanges()||importer.hasChanges())){
      $('validation-status').textContent='Есть несохранённая настройка или выбранный CSV. Сохраните их или сбросьте ввод перед сменой отчёта.';
      if(data){$('validation-profile').value=data.selected.profile_id;$('validation-zone').value=data.selected.zone_id;$('plan-version').value=data.plan.version_id||'';}return;
    }
    const n=++serial;nightChart.invalidate();$('validation-content').hidden=true;$('validation-export').hidden=true;$('validation-status').textContent='Читаем план и полевой журнал…';
    try{
      const q=new URLSearchParams();if(profile)q.set('profile_id',profile);if(zone)q.set('zone_id',zone);if(version)q.set('version',version);
      const url='/api/site-validation'+(q.size?'?'+q:'');const response=await fetch(url);const d=await response.json();if(n!==serial)return;if(!response.ok)throw Error(d.error||'Отчёт недоступен');data=d;
      $('validation-profile').innerHTML=d.choices.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('');$('validation-profile').value=d.selected.profile_id;$('validation-profile').disabled=false;
      const p=d.choices.find(p=>p.id===d.selected.profile_id);const zones=p.zones.some(z=>z.id===d.selected.zone_id)?p.zones:[...p.zones,{id:d.selected.zone_id,name:d.selected.zone_name}];
      $('validation-zone').innerHTML=zones.map(z=>`<option value="${esc(z.id)}">${esc(z.name)}</option>`).join('');$('validation-zone').value=d.selected.zone_id;$('validation-zone').disabled=false;
      $('admission-title').textContent=d.overall.title;$('admission-reason').textContent=d.overall.reason;
      $('validation-scope').innerHTML=`<p><strong>${esc(d.selected.profile_name)} · ${esc(d.selected.zone_name)}</strong></p><p class="note">${d.period?`Назначенный период: ${esc(d.period.start)} — ${esc(d.period.end)}. Запланировано ${d.period.planned_nights} ночей.`:'Период проверки не назначен: ожидаемое число ночей неизвестно.'} Опорный датчик: ${esc(d.selected.reference_sensor_id||'не задан')}. ${d.selected.latitude!==null?`Координаты паспорта ${d.selected.latitude.toFixed(6)}, ${d.selected.longitude.toFixed(6)}; это не подтверждение границ сада.`:''}</p><p class="note">План: ${d.plan.approved_at?'отметка согласования есть; нужен разбор фиксации':'проект, согласование не записано'}. ${d.selected.profile_revision!==d.selected.latest_profile_revision?'Показана закреплённая версия паспорта, отличающаяся от последней.':''}</p>`;
      $('validation-missing').innerHTML=d.missing_setup.map(s=>`<li>${esc(s)}</li>`).join('')||'<li>Заполненный паспорт ещё не доказывает качество модели; нужны факты и проверка протокола.</li>';
      const c=d.coverage,m=d.gfs;
      $('validation-stats').innerHTML=[['Назначено завершённых ночей',value(c.scheduled_completed_nights),'Знаменатель неизвестен до назначения периода'],['Сохранено GFS-ночей',value(c.captured_nights),'Наличие выдачи не означает проверку'],['Учтено в оценке',value(c.eligible_nights),'Только полные проверяемые факты'],['Холодных / групп',c.eligible_nights?pair(c.cold_nights,c.cold_groups):'—','Нет холодных данных — нет оценки обнаружения']].map(([a,b,note])=>`<div class="metric"><small>${a}</small><strong>${b}</strong><p>${note}</p></div>`).join('');
      $('coverage-explanation').textContent=c.measurement_rows?'Полученные строки и принятые значения показаны отдельно от полных ночей. Причины исключения раскрыты ниже.':'Измерений выбранной опорной позиции пока нет. Ошибки модели, покрытие диапазона и проценты обнаружения не вычислены.';
      $('coverage-table').innerHTML=[['GFS до 18:00, возраст ≤18 ч',c.timely_fresh_issues,c.scheduled_completed_nights,c.timely_fresh_issue_fraction],['Полные 14 часовых измерений',c.complete_truth_nights,c.scheduled_completed_nights,c.complete_truth_fraction],['Пятиминутные слоты ±1 мин',c.frequent_slots_found,c.frequent_slots_expected,c.frequent_fraction]].map(([label,a,b,v])=>row([label,pair(a,b),value(v,'fraction')])).join('');
      $('qc-summary').textContent=`Строк измерений${d.period?' в ночных окнах выбранного периода':''}: ${c.measurement_rows}; вне периода: ${c.outside_period_rows||0}; принятых температурных: ${c.accepted_temperature_rows}; не подтверждено соответствие метаданным: ${c.unverified_metadata_rows}. Проверка качества: ${Object.entries(c.qc_counts).map(([k,v])=>(qcLabels[k]||k)+' '+v).join(', ')||'записей нет'}.`;
      $('missing-nights').textContent=d.period?(c.missing_forecast_dates.join(', ')||'В завершённой части периода отсутствующих выдач нет; проверьте также качество и своевременность.'):'Список неизвестен, пока период не назначен.';
      $('comparison-context').textContent=`Проверяемых ночей: ${m.nights}; холодных: ${m.cold_nights}. ${d.plan.candidate?'Кандидат указан в плане, но совместимых сохранённых прогнозов для парного сравнения нет.':'Локальный кандидат не зафиксирован. Его значения и выигрыш не вычислены.'}`;
      $('comparison-table').innerHTML=[['MAE минимума, °C',m.minimum_mae_c],['MAE минимума холодных, °C',m.cold_minimum_mae_c],['Смещение минимума, °C',m.bias_c],['Почасовая MAE, °C',m.hourly_mae_c],['Обнаружение холода',m.recall,'fraction'],['Ложные среди тревог',m.false_alarm_share_alerts,'fraction'],['Ложные среди тёплых',m.false_alarm_rate_warm,'fraction'],['Серьёзные пропуски при факте ≤−1 °C',m.severe_misses],['Длительность на холодных, ч',m.cold_duration_mae_h],['Первый холодный отсчёт: MAE попаданий, ч',m.onset_mae_hits_h],['Покрытие холодных диапазоном',null,'fraction']].map(([label,v,u])=>row([label,value(v,u),'—','—'])).join('')+row(['TP / FN / FP / TN',[m.tp,m.fn,m.fp,m.tn].map(v=>value(v)).join(' / '),'—','—']);
      $('criteria-context').textContent=d.plan.status==='criteria_fixed'?'Показаны критерии выбранной зафиксированной версии. Согласование заявлено автором; это не автоматический допуск.':'Показаны предложенные или черновые критерии. Они не считаются согласованными до отдельной фиксации.';renderCriteria();
      const outcomes={hit:'Холод обнаружен',miss:'Пропуск холода',false_alarm:'Ложная тревога',correct_negative:'Верно без холода'};
      $('evidence-table').innerHTML=d.evidence.map(r=>row([esc(r.night),r.matched_hours+' / 14',r.included?'Учтена':'Не учтена',esc(r.included?outcomes[r.outcome]:r.reasons.map(v=>reasons[v]||v).join(' · '))])).join('')||row(['Нет сохранённых реальных GFS-ночей этого участка','—','—','Фиктивные строки не добавлены']);
      $('validation-limits').innerHTML=d.limitations.map(s=>`<li>${esc(s)}</li>`).join('');
      $('validation-export').href=url;$('validation-export').hidden=false;$('validation-content').hidden=false;
      editor.render(d.configuration);importer.render(d);nightChart.render(d);
      $('validation-status').textContent='Отчёт прочитан '+new Date(d.generated_at_utc).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'})+' МСК. Чтение отчёта не меняет измерения и модель.';return true;
    }catch(e){if(n===serial)$('validation-status').textContent=e.message+' Отчёт не заменён прежними или учебными данными.';return false;}
  }
  function renderCriteria(){if(data)$('criteria-table').innerHTML=criteriaHTML(data.gates,$('criteria-group').value);}
  $('validation-profile').addEventListener('change',e=>load(e.target.value));$('validation-zone').addEventListener('change',e=>load($('validation-profile').value,e.target.value));
  $('validation-refresh').addEventListener('click',()=>load($('validation-profile').value,$('validation-zone').value,data?.plan.version_id));$('criteria-group').addEventListener('change',renderCriteria);
  const q=new URLSearchParams(location.search);load(q.get('profile_id'),q.get('zone_id'),q.get('version'));
})();
