/* Descriptive evidence, not a diagnosis. Dates and unavailable values remain explicit. */
(function(){
  'use strict';
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num=v=>typeof v==='number'&&Number.isFinite(v)?v.toLocaleString('ru-RU',{minimumFractionDigits:3,maximumFractionDigits:3}):'—';
  const date=v=>new Date(v).toLocaleString('ru-RU',{timeZone:'Europe/Moscow',day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'});
  function placeLabel(p){return `${p.centre.latitude.toFixed(5)}, ${p.centre.longitude.toFixed(5)} · 60×60 м`;}
  function chart(d){
    const scenes=d.scenes;if(!scenes.length)return '<p>В выбранном архиве нет съёмок.</p>';
    const begin=d.catalogue_window?.start||scenes[0].acquired_at_utc,end=d.catalogue_window?.end||scenes.at(-1).acquired_at_utc;
    const lo=Date.parse(begin),hi=Date.parse(end);
    const x=t=>65+790*(Date.parse(t)-lo)/Math.max(hi-lo,1),y=v=>145-100*v;
    const horizontal=[-1,-.5,0,.5,1].map(v=>`<line x1="60" x2="865" y1="${y(v)}" y2="${y(v)}" class="history-grid"/><text x="8" y="${y(v)+4}">${v.toFixed(1)}</text>`).join('');
    const points=scenes.map(s=>s.usable?['ndvi','ndmi'].map(k=>`<g class="history-${k}"><title>${esc(date(s.acquired_at_utc))} МСК · ${k.toUpperCase()} ${num(s[k].median)} · ${s.valid_cells}/9</title><line x1="${x(s.acquired_at_utc)}" x2="${x(s.acquired_at_utc)}" y1="${y(s[k].spatial_range[0])}" y2="${y(s[k].spatial_range[1])}"/><circle cx="${x(s.acquired_at_utc)}" cy="${y(s[k].median)}" r="4"/></g>`).join(''):`<text x="${x(s.acquired_at_utc)}" y="260" class="history-gap"><title>${esc(date(s.acquired_at_utc))} · ${esc(s.reason)}</title>×</text>`).join('');
    const visits=d.visits.map((r,i)=>{const t=Date.parse(r.payload.observed_at_utc);return t<lo||t>hi?'':`<g class="history-visit"><title>Осмотр №${i+1}: ${esc(date(r.payload.observed_at_utc))} МСК</title><line x1="${x(r.payload.observed_at_utc)}" x2="${x(r.payload.observed_at_utc)}" y1="35" y2="247"/><text x="${x(r.payload.observed_at_utc)+3}" y="${20+(i%2)*12}">№${i+1}</text></g>`;}).join('');
    return `<div class="place-chart-scroll"><svg viewBox="0 0 900 300" role="img" aria-label="NDVI и NDMI выбранного места по датам съёмок; вертикальные линии — введённые осмотры">${horizontal}${visits}${points}<text x="65" y="288">${esc(date(begin))}</text><text x="650" y="288">${esc(date(end))}</text></svg></div>`;
  }
  function view(d){
    const good=d.scenes.filter(s=>s.usable).length;
    return `<h4>Динамика выбранного места</h4><p>${esc(placeLabel(d.place))} · осмотров: ${d.visits.length}. Пригодных съёмок: ${good}/${d.scenes.length}.</p><p class="note">Один закреплённый архив: ${d.inspection_reference.period==='recent'?'последние 90 дней':'весна 2024'} · версия ${esc(d.inspection_reference.observation_version.slice(0,12))}. Это история данного окна, а не значения у ориентира. Старые записи осмотров не переписаны.</p>${chart(d)}<p class="note history-legend"><span>● NDVI</span> <span>● NDMI</span> · штриховые линии — осмотры № · × — пропуск съёмки у этого места.</p><p class="note">Точки — медианы, отрезки — минимум–максимум пригодных пикселей, не доверительный интервал. Между съёмками значений нет. Осмотры за пределами периода видны в журнале ниже; на график они не перенесены.</p><details><summary>Все даты и пропуски этого места</summary><div class="table-wrap"><table><caption>Спутниковые наблюдения выбранного окна · время МСК</caption><thead><tr><th>Съёмка</th><th>NDVI</th><th>NDMI</th><th>Пиксели</th><th>Качество</th><th>Версия обработки</th></tr></thead><tbody>${d.scenes.map(s=>`<tr><td>${esc(date(s.acquired_at_utc))}</td><td>${num(s.ndvi?.median)}</td><td>${num(s.ndmi?.median)}</td><td>${s.valid_cells}/9</td><td>${s.usable?'Пригодно':esc(s.reason)}</td><td>${esc(s.processing_baseline||'Не указана')}</td></tr>`).join('')}</tbody></table></div></details>`;
  }
  function between(d,index){
    if(!index)return '<p class="note">Первая запись этого места. Предыдущего посещения для сравнения нет.</p>';
    const pair=d.intervals[index-1],a=d.visits[index-1].payload,b=d.visits[index].payload;
    const source=(s,label)=>`<p>${label}: ${s?`${esc(date(s.acquired_at_utc))} МСК · отставание от осмотра ${s.lag_days.toFixed(1)} дн.${s.within_lag_limit?'':' · старше предела 7 дней'}`:'нет пригодной съёмки до посещения'}</p>`;
    return `<aside class="visit-difference"><h5>Между посещениями №${index} → №${index+1}</h5><p>По времени автора: ${pair.days_between.toFixed(1)} дн. ${pair.days_between===0?'Это могут быть дополнения или исправления одной записи.':''}</p><p>Заметки: ${a.notes===b.notes?'текст совпадает':'текст отличается; обе записи приведены в хронологии'}. Фотографий: ${a.photos.length} → ${b.photos.length}; содержимое автоматически не оценивалось.</p>${source(pair.before,'Снимок A перед первым')}${source(pair.after,'Снимок B перед вторым')}<p>${esc(pair.reason)}</p>${pair.status==='paired'?`<div class="value-grid">${['ndvi','ndmi'].map(k=>`<div><small>Изменение ${k.toUpperCase()} B − A</small><strong>${num(pair[k].median)}</strong><span>Разброс пикселей: ${pair[k].spatial_range.map(num).join('…')} · общих ${pair.paired_cells}/9</span></div>`).join('')}</div>`:''}<p class="note">Берётся последняя пригодная съёмка не позже каждого осмотра и не старше 7 дней. Снимки после посещения не подставляются. Это описательное сопоставление, не причина изменений и не оценка состояния растений. Архивная съёмка могла быть опубликована или загружена позже: доступность в день осмотра этим не подтверждена.</p></aside>`;
  }
  const api={view,between,placeLabel,chart};
  if(typeof module!=='undefined')module.exports=api;
  if(typeof window!=='undefined')window.LandPlaceHistory=api;
})();
