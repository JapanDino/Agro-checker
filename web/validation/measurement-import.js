(function(){
  'use strict';
  const labels={ready:'Готово к сохранению',invalid:'Ошибка',conflict:'Конфликт',suspect:'Скачок: нужна проверка',duplicate:'Точный повтор',outside:'Вне ночного периода'};
  function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
  function previewHTML(p){
    const counts=Object.entries(p.counts).map(([k,n])=>`${labels[k]||k}: ${n}`).join(' · ');
    const table=rows=>'<div class="table-wrap"><table>'+rows.join('')+'</table></div>';
    const summary=`<h3>Результат предварительной проверки</h3><p>${esc(p.filename)} · всего ${p.rows_total} строк. ${esc(counts)}</p><p>${p.can_commit?'Можно сохранить только новые проверенные строки. Повторы и строки вне ночных окон будут пропущены.':'Сохранение недоступно: исправьте ошибки, конфликты и скачки либо добавьте новые строки. Повторите проверку CSV.'}</p><p class="note">Температура приведена к °C, время — к UTC. ${p.unsorted?'Сроки в исходном файле идут не по порядку; проверка выполнена по времени.':''} ${p.plan_action==='draft'?'Выбрана черновая версия: загрузка не превращает её в согласованный контроль.':''} ${!p.sensor_checked?'В паспорте нет отметки проверки прибора; данные не подтверждают местное качество.':''}</p>`;
    const rows=table(['<tr><th>Строка CSV</th><th>Время UTC</th><th>°C / RH %</th><th>Результат</th></tr>',...p.rows.map(r=>`<tr><td>${r.line}</td><td>${esc(r.observed_at_utc||'—')}</td><td>${r.temperature_c===null?'—':esc(r.temperature_c)} / ${r.rh_pct===null?'—':esc(r.rh_pct)}</td><td>${esc(labels[r.status])}${r.reasons.length?'<p class="note">'+esc(r.reasons.join(' · '))+'</p>':''}</td></tr>`)]);
    const nights=p.nights.length?table(['<tr><th>Дата вечера</th><th>Часовые цели до → после</th><th>Нет из 14</th><th>Слоты 5 мин: нет из 168</th></tr>',...p.nights.map(n=>`<tr><td>${esc(n.night)}</td><td>${n.hourly_before} → ${n.hourly_after}</td><td>${n.hourly_missing}</td><td>${n.slots_missing}</td></tr>`)]):'<p>Завершённых ночей выбранного периода пока нет.</p>';
    return summary+`<details open><summary>Полнота завершённых ночей после загрузки</summary><p class="note">С учётом уже принятых строк этого датчика. Часовые цели ±5 минут, пятиминутные слоты ±1 минута; это наличие записей, а не проверка точности или допуск. Для оценки GFS также нужны сохранённый до ночи прогноз, подтверждённый паспорт и журнал защиты.</p>${nights}</details><details><summary>Разбор строк · показано ${p.rows_shown} из ${p.rows_total}</summary><p class="note">Вначале показаны проблемные строки. Проверены все строки файла; отображение ограничено 200 строками.</p>${rows}</details><p class="import-hash note">SHA-256 исходного CSV: ${esc(p.source_sha256)}</p>`;
  }
  if(typeof module!=='undefined')module.exports={previewHTML};
  if(typeof document==='undefined')return;
  const $=id=>document.getElementById(id);
  function setup(options){
    let data=null,checked=null,body=null,busy=false,dirty=false,request=null;
    function status(t){$('import-status').textContent=t;}
    function invalidate(){checked=null;body=null;request=null;$('import-preview-result').hidden=true;$('import-confirm').hidden=true;dirty=Boolean($('import-file').files.length);}
    function clear(){invalidate();$('measurement-import-form').reset();dirty=false;status('Выбор CSV очищен. Журнал не изменён.');}
    function hasChanges(){return dirty||busy;}
    function render(d){
      data=d;checked=null;body=null;request=null;dirty=false;$('measurement-import-form').reset();$('import-preview-result').hidden=true;$('import-confirm').hidden=true;
      const scope=d.plan.scope;const enabled=Boolean(d.plan.version_id&&scope.start&&scope.end&&scope.reference_sensor_id);
      $('import-fields').disabled=!enabled;
      $('import-context').textContent=enabled?`Версия ${d.plan.version_id} · ${d.selected.profile_name} / ${d.selected.zone_name} · опорный датчик ${scope.reference_sensor_id} · вечера ${scope.start} — ${scope.end}. Ночные окна 19:00–09:00 МСК.`:'Импорт станет доступен после сохранения настройки с периодом и зарегистрированным опорным датчиком. Датчики и измерения автоматически не создаются.';
      status('Файл не выбран. Измерения не загружены.');
    }
    async function post(path,payload){
      const tokenResponse=await fetch('/api/land/status');const token=await tokenResponse.json();if(!tokenResponse.ok)throw Error('Обновите страницу перед сохранением');
      const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Field-Token':token.token},body:JSON.stringify(payload)});const result=await response.json();if(!response.ok)throw Error(result.error||'Не удалось обработать CSV');return result;
    }
    function toggle(){ $('import-commit').disabled=busy||!checked?.can_commit||!$('import-actor').value.trim()||!$('import-attestation').checked; }
    $('measurement-import-form').addEventListener('submit',async e=>{
      e.preventDefault();if(busy)return;
      if(options.isPlanDirty()){status('Сначала сохраните или сбросьте изменения настройки. CSV проверяется по сохранённой версии.');return;}
      const file=$('import-file').files[0];if(!file){status('Выберите CSV с реальными измерениями');return;}
      if(!file.size||file.size>2_000_000){status('CSV должен быть непустым, не больше 2 МБ');return;}
      if(!$('import-unit').value){status('Выберите единицы температуры в исходном файле');return;}
      invalidate();dirty=true;busy=true;$('import-fields').disabled=true;status('Проверяем CSV без сохранения…');
      try{
        const text=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer());
        body={profile_id:data.selected.profile_id,zone_id:data.selected.zone_id,version:data.plan.version_id,csv:text,filename:file.name,unit:$('import-unit').value,time_basis:$('import-time').value,delimiter:$('import-delimiter').value==='tab'?'\t':$('import-delimiter').value};
        checked=await post('/api/site-validation/measurements/preview',body);$('import-preview-result').innerHTML=previewHTML(checked);$('import-preview-result').hidden=false;$('import-confirm').hidden=!checked.can_commit;$('import-actor').value='';$('import-attestation').checked=false;
        status('Предварительная проверка завершена. В журнал ничего не записано.');
      }catch(e){checked=null;body=null;status(e instanceof TypeError&&/encoded|encoding/i.test(e.message)?'Не удалось прочитать UTF-8. Пересохраните CSV в UTF-8 и повторите.':e.message);}
      finally{busy=false;$('import-fields').disabled=false;toggle();}
    });
    $('import-commit').addEventListener('click',async()=>{
      if(busy||!checked?.can_commit)return;
      if(options.isPlanDirty()){status('Сначала сохраните или сбросьте изменения настройки периода.');return;}
      if(!$('import-actor').value.trim()||!$('import-attestation').checked){status('Укажите автора и подтвердите источник');return;}
      request=request||{...body,preview_sha256:checked.preview_sha256,request_id:crypto.randomUUID().replaceAll('-',''),actor:$('import-actor').value.trim(),confirmed_source:true};
      busy=true;$('import-fields').disabled=true;status('Сохраняем только проверенные новые строки…');
      try{const saved=await post('/api/site-validation/measurements/commit',request);const version=body.version;dirty=false;busy=false;clear();const refreshed=await options.onSaved(version);status(`Пакет ${saved.batch_id} сохранён. ${saved.replayed?'Повтор запроса не создал дубль.':'Записано '+saved.imported_rows+' измерений.'} ${refreshed?'Отчёт обновлён.':'Импорт сохранён, но отчёт не загрузился; обновите его.'} Автоматического допуска нет.`);}
      catch(e){status(e.message+' Исходный файл и ввод сохранены в форме; при конфликте повторите предварительную проверку.');}
      finally{busy=false;$('import-fields').disabled=false;toggle();}
    });
    for(const id of ['import-file','import-unit','import-time','import-delimiter'])$(id).addEventListener('change',()=>{invalidate();status('Входные данные изменились. Выполните предварительную проверку.');});
    for(const id of ['import-actor','import-attestation'])$(id).addEventListener('input',()=>{request=null;toggle();});
    $('import-reset').addEventListener('click',clear);
    addEventListener('beforeunload',e=>{if(hasChanges()){e.preventDefault();e.returnValue='';}});
    return {render,hasChanges};
  }
  window.ValidationMeasurementImport={setup};
})();
