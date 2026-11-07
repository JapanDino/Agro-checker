'use strict';
window.ScoutingPlans={create({api,getData,onRefresh,onOpenTask,onChange}){
  const $=id=>document.getElementById(id),chosen=new Set();let request=crypto.randomUUID().replaceAll('-',''),report=null,loading=0,saving=false;
  const node=(tag,text,parent,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;parent?.append(n);return n;};
  function positions(){const order=[...chosen];for(const e of document.querySelectorAll('.plan-task-option')){const i=order.indexOf(e.querySelector('input').value);e.querySelector('.plan-order').textContent=i>=0?'Место '+(i+1)+' в плане':'';}}
  const number=v=>typeof v==='number'&&Number.isFinite(v)?v.toFixed(3).replace('.',','):'нет пригодных данных';
  const day=s=>new Date(s).toLocaleDateString('ru-RU',{timeZone:'Europe/Moscow'});
  const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function count(){ $('plan-count').textContent=chosen.size+' / 3';$('save-plan').disabled=saving||chosen.size<1||chosen.size>3;onChange?.(); }
  function render(){
    const data=getData(),tasks=data.tasks.slice().reverse(),target=$('plan-task-options');target.replaceChildren();
    for(const id of chosen)if(!tasks.some(t=>t.id===id))chosen.delete(id);
    if(!tasks.length)node('p','Сначала выберите место на снимках и сохраните свой вопрос. Здесь появится задача для плана.',target,'empty-plan-note');
    for(const t of tasks){
      const p=t.payload,label=node('label',undefined,target,'plan-task-option'),input=node('input',undefined,label);input.type='checkbox';input.value=t.id;input.checked=chosen.has(t.id);
      const text=node('span',undefined,label);node('small','',text,'plan-order');node('strong',p.place.fragment+' · '+p.objective,text);
      node('small',p.place.latitude.toFixed(5)+', '+p.place.longitude.toFixed(5)+' · '+p.actor,text);
      node('small',day(p.evidence.before.at)+' → '+day(p.evidence.after.at)+' · окно '+p.metric.toUpperCase()+' '+number(p.place_evidence?.[p.metric]),text);
      input.addEventListener('change',()=>{
        if(input.checked&&chosen.size>=3){input.checked=false;$('plan-status').textContent='В один план можно включить до трёх мест.';return;}
        if(input.checked&&[...chosen].some(id=>data.tasks.find(x=>x.id===id)?.payload.place.id===p.place.id)){input.checked=false;$('plan-status').textContent='Это место уже включено через другую задачу.';return;}
        input.checked?chosen.add(t.id):chosen.delete(t.id);$('plan-status').textContent='';count();positions();
      });
    }
    const saved=$('saved-plans');saved.replaceChildren();
    if(!(data.plans||[]).length)node('p','Планов пока нет. Реальных посещений наличие плана не подтверждает.',saved,'muted');
    for(const plan of (data.plans||[]).slice().reverse()){
      const b=node('button',undefined,saved,'saved-plan-item');b.type='button';b.setAttribute('aria-pressed',String(report?.plan.id===plan.id));node('strong',plan.payload.title,b);node('small',plan.payload.task_ids.length+(plan.payload.task_ids.length===1?' место · ':' места · ')+plan.payload.purpose_label,b);
      b.addEventListener('click',()=>open(plan.id));
    }
    count();positions();purposeNote();
  }
  function purposeNote(){
    $('plan-purpose-note').textContent=$('plan-purpose').value==='sensor_survey'?'В лист добавятся вопросы о поверхности, доступе, связи и монтаже. Это подготовка обследования, пригодность установки пока неизвестна.':'Сохраните вопросы для проверки изменений поверхности. Причину и необходимость действий оценивает агроном.';
  }
  $('plan-purpose').addEventListener('change',purposeNote);
  $('plan-form').addEventListener('submit',async e=>{
    e.preventDefault();if(saving||!chosen.size||chosen.size>3)return;saving=true;count();$('plan-status').textContent='Сохраняем основание плана…';
    try{
      const plan=await api('/api/scouting/plans',{request_id:request,title:$('plan-title').value,task_ids:[...chosen],purpose:$('plan-purpose').value});
      onRefresh(await api('/api/scouting/state'));request=crypto.randomUUID().replaceAll('-','');chosen.clear();$('plan-form').reset();$('plan-status').textContent='План сохранён. Он не является записью посещения.';await open(plan.id);render();
    }catch(error){$('plan-status').textContent=error.message;}finally{saving=false;count();}
  });
  async function open(id){
    const run=++loading;$('plan-status').textContent='Открываем сохранённый лист…';
    try{const data=await api('/api/scouting/plans/export?'+new URLSearchParams({id}));if(run!==loading)return;report=data;renderReport();$('plan-status').textContent='';render();}
    catch(error){if(run===loading)$('plan-status').textContent=error.message;}
  }
  function renderReport(){
    $('plan-report').hidden=!report;if(!report)return;
    $('report-title').textContent=report.plan.payload.title;$('report-purpose').textContent=report.plan.payload.purpose_label+' · план человека, границы уточняются';
    const target=$('report-places');target.replaceChildren();
    report.tasks.forEach((t,i)=>{
      const p=t.payload,card=node('div',undefined,target,'report-place');node('span',String(i+1).padStart(2,'0')+' / '+p.place.fragment,card,'small-label');node('h4',p.objective,card);
      node('p',p.place.latitude.toFixed(5)+', '+p.place.longitude.toFixed(5)+' · окно 60 м',card);
      node('p',day(p.evidence.before.at)+' → '+day(p.evidence.after.at)+' · '+p.metric.toUpperCase()+' места '+number(p.place_evidence?.[p.metric])+' · '+p.metric.toUpperCase()+' площади '+number(p.evidence.metrics[p.metric].base),card,'muted');
      node('p','Назначено: '+p.actor+(p.planned_date?' · '+p.planned_date:' · дата не назначена'),card,'muted');
      const b=node('button','Открыть задачу и историю →',card,'text-button');b.type='button';b.addEventListener('click',()=>onOpenTask(t));
    });
    if(report.plan.payload.site_checks.length){node('h4','Что проверить перед монтажом',target);for(const prompt of report.plan.payload.site_checks)node('p','□ '+prompt+' — пока не проверено',target,'muted');}
  }
  function download(body,mime,ext){
    const url=URL.createObjectURL(new Blob([body],{type:mime})),a=document.createElement('a');a.href=url;a.download='agropulse-plan-'+report.plan.id.slice(0,8)+'.'+ext;a.click();setTimeout(()=>URL.revokeObjectURL(url),5000);
  }
  $('download-plan-json').addEventListener('click',()=>{if(report)download(JSON.stringify(report,null,2),'application/json;charset=utf-8','json');});
  $('download-plan-geojson').addEventListener('click',()=>{
    if(!report)return;const features=report.tasks.map((t,i)=>{const p=t.payload;return {type:'Feature',geometry:{type:'Point',coordinates:[p.place.longitude,p.place.latitude]},properties:{order:i+1,place_id:p.place.id,task_id:t.id,objective:p.objective,before:p.evidence.before.at,after:p.evidence.after.at,confirmed_boundary:false,measured_gps:false,route_optimized:false}};});
    download(JSON.stringify({type:'FeatureCollection',features},null,2),'application/geo+json;charset=utf-8','geojson');
  });
  $('download-plan-html').addEventListener('click',()=>{
    if(!report)return;const p=report.plan.payload;
    const cards=report.tasks.map((t,i)=>{const v=t.payload,e=v.evidence,m=v.place_evidence;return `<article><p class="eyebrow">${i+1} / ${escape(v.place.fragment)} · ${escape(v.place.id)}</p><h2>${escape(v.objective)}</h2><p>${v.place.latitude.toFixed(6)}, ${v.place.longitude.toFixed(6)} · опора окна 60 м, не GPS</p><p>Ответственный: ${escape(v.actor)} · плановая дата: ${escape(v.planned_date||'не назначена')}</p><table><tr><th>Архив</th><td>${escape(day(e.before.at))} → ${escape(day(e.after.at))} (${Math.round(e.days)} дн.)</td></tr><tr><th>ΔNDVI / ΔNDMI места</th><td>${number(m?.ndvi)} / ${number(m?.ndmi)}</td></tr><tr><th>ΔNDVI / ΔNDMI площади</th><td>${number(e.metrics.ndvi.base)} / ${number(e.metrics.ndmi.base)}</td></tr><tr><th>Общее покрытие</th><td>место ${m?Math.round(m.coverage*100)+'%':'нет данных'} / площадь ${Math.round(e.coverage*100)}%</td></tr></table><details><summary>Съёмки и версия данных</summary><p>${escape(e.before.id)} → ${escape(e.after.id)}</p><p>Обработка: ${escape(e.before.baseline)} / ${escape(e.after.baseline)}</p><p>Версия архива: ${escape(e.version)}</p></details><p class="note">Причина изменения, культура и фаза не установлены. Спутниковый индекс не подтверждает болезнь. Архивное изменение не означает срочную тревогу сегодня.</p>${p.site_checks.map(q=>'<p>□ '+escape(q)+' — предстоит проверить</p>').join('')}<h3>Заполнить после реального посещения</h3><p>Дата / автор: ____________________________________</p><p>Фактические наблюдения: ___________________________</p><p>Фото / следующий шаг: _____________________________</p></article>`;}).join('');
    const html='<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+escape(p.title)+'</title><style>body{font:15px/1.6 "Segoe UI",Arial,sans-serif;max-width:900px;margin:40px auto;padding:0 24px;color:#263c30}h1{font-size:32px;line-height:1.2}.eyebrow{font-size:12px;color:#6a786b}article{border-top:2px solid #315c44;padding:20px 0;margin:28px 0;break-inside:avoid}table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #dce3da;text-align:left;padding:8px;vertical-align:top}th{width:40%;font-weight:500}.note{color:#647363;font-size:13px}details p{overflow-wrap:anywhere;font-size:11px}@media print{body{margin:0;font-size:11px}h1{font-size:23px}details{display:none}}</style><p class="eyebrow">AGROPULSE / ONLYFARMS · ПЛАН ОБСЛЕДОВАНИЯ</p><h1>'+escape(p.title)+'</h1><p>'+escape(p.purpose_label)+'</p><p class="note">'+escape(report.notice)+' Границы приблизительные. Пустые строки ниже не являются наблюдениями.</p>'+cards+'<footer class="note">Составлен '+escape(day(report.plan.recorded_at_utc))+' · план '+escape(report.plan.id)+' · источники: Sentinel-2 L2A; геометрия восстановлена по скриншоту. Полные источники и SHA256 — в JSON-выгрузке. Запись посещения в продукте требует отдельного ввода человеком.</footer></html>';
    download(html,'text/html;charset=utf-8','html');
  });
  return {render,canSave:()=>!saving&&chosen.size>0&&chosen.size<=3,addTask(id){const data=getData(),t=data.tasks.find(x=>x.id===id);if(chosen.size<3&&t&&![...chosen].some(i=>data.tasks.find(x=>x.id===i)?.payload.place.id===t.payload.place.id))chosen.add(id);}};
}};
