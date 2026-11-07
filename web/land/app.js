/* Read-only landmark maps. Missing observations remain null at every display layer. */
(function () {
  'use strict';
  const labels = {clay:'Глина',sand:'Песок',silt:'Пылеватая фракция',phh2o:'pH в воде',soc:'Органический углерод'};
  const sclLabels = {0:'нет данных',1:'дефектный пиксель',2:'тёмная поверхность',3:'тень облака',4:'растительность',5:'открытая поверхность',6:'вода',7:'неопределённый класс',8:'облака средней вероятности',9:'облака высокой вероятности',10:'перистые облака',11:'снег / лёд'};
  function finite(v) { return typeof v === 'number' && Number.isFinite(v); }
  function fmt(v, n=3) { return finite(v) ? v.toLocaleString('ru-RU',{minimumFractionDigits:n,maximumFractionDigits:n}) : '—'; }
  function date(v, time=false) { if (!v) return 'Не указана'; return new Date(v).toLocaleString('ru-RU',{timeZone:'Europe/Moscow',day:'2-digit',month:'2-digit',year:'numeric',...(time ? {hour:'2-digit',minute:'2-digit'} : {})}); }
  function gapReason(s) {
    if (s.extraction_status==='error') return 'Пропуск: ошибка получения или проверки данных';
    if (s.point_window_usable) return 'Пригодное наблюдение';
    if ([8,9,10].includes(s.point_scl)) return 'Пропуск: облака у ориентира';
    return 'Пропуск: поверхность или соседство не прошли фильтр';
  }
  function observation(s, key) {
    return s.extraction_status!=='error' && s.point_window_usable && finite(s[`point_60m_${key}_median`]) ? s[`point_60m_${key}_median`] : null;
  }
  function pointInCell(point, polygon) {
    let inside=false;
    for (let i=0,j=polygon.length-1;i<polygon.length;j=i++) {
      const [xi,yi]=polygon[i], [xj,yj]=polygon[j];
      if (((yi>point[1])!==(yj>point[1])) && point[0]<(xj-xi)*(point[1]-yi)/(yj-yi)+xi) inside=!inside;
    }
    return inside;
  }
  function polygon(g,r,c) { return [[g.east[r][c],g.north[r][c]],[g.east[r][c+1],g.north[r][c+1]],[g.east[r+1][c+1],g.north[r+1][c+1]],[g.east[r+1][c],g.north[r+1][c]]]; }
  function inspectionCandidate(block,key,threshold,comparable=true) {
    const v=block[key];
    return Boolean(comparable&&block.paired_cells===9&&v.shift_scenarios===9&&v.shift_range&&
      ((v.shift_range[0]>=threshold&&v.positive_cells>=8)||(v.shift_range[1]<=-threshold&&v.negative_cells>=8)));
  }
  const helpers={finite,observation,gapReason,pointInCell,polygon,inspectionCandidate};
  if (typeof module !== 'undefined') module.exports=helpers;
  if (typeof document === 'undefined') return;
  const $=id=>document.getElementById(id);
  let data, rows=[], archiveSatellite, period='recent', snapshot=null, token=null, serverNow=null, polling=null, historyRequest=0;
  let selected=0, soilRow=4, soilCol=4, index='ndvi', currentMap=null, request=0, contextExtent=1500;
  let changeData=null,changeRequest=0,changeMetric='ndvi',activeWindow=null,journal=null;
  const maps=new Map();
  const palettes={terrain:[[228,230,181],[171,185,120],[104,125,87]],soil:[[240,227,195],[189,152,96],[118,83,51]],ndvi:[[242,223,175],[146,185,119],[35,90,60]],ndmi:[[238,224,189],[136,184,181],[35,92,118]],delta:[[120,89,155],[242,241,233],[181,131,50]]};
  function color(v,lo,hi,kind) {
    if (!finite(v)) return '#d8dcd5';
    const t=Math.max(0,Math.min(1,(v-lo)/(hi-lo||1)))*2, k=Math.min(1,Math.floor(t)), f=t-k;
    return `rgb(${palettes[kind][k].map((a,i)=>Math.round(a+(palettes[kind][k+1][i]-a)*f)).join(',')})`;
  }
  const size=640, margin=53, span=size-2*margin;
  function transform(extent) { return {x:v=>margin+(v+extent)/(2*extent)*span,y:v=>margin+(extent-v)/(2*extent)*span}; }
  function background(canvas) { const ctx=canvas.getContext('2d'); ctx.clearRect(0,0,size,size);ctx.fillStyle='#f5f7f1';ctx.fillRect(0,0,size,size);return ctx; }
  function paintCells(ctx,grid,values,extent,lo,hi,kind) {
    const p=transform(extent);
    ctx.save();ctx.beginPath();ctx.rect(margin,margin,span,span);ctx.clip();
    for(let r=0;r<grid.height;r++) for(let c=0;c<grid.width;c++) {
      const q=polygon(grid,r,c);ctx.beginPath();q.forEach(([x,y],i)=>i?ctx.lineTo(p.x(x),p.y(y)):ctx.moveTo(p.x(x),p.y(y)));ctx.closePath();
      ctx.fillStyle=color(values[r][c],lo,hi,kind);ctx.fill();
      // Small shared-edge stroke avoids canvas antialiasing seams, not raster resampling.
      ctx.lineWidth=.35;ctx.strokeStyle=ctx.fillStyle;ctx.stroke();
    }ctx.restore();
  }
  function frame(ctx,extent) {
    const p=transform(extent),fontScale=Math.max(1,Math.min(1.8,size/(ctx.canvas.clientWidth||size)));ctx.font=`${13*fontScale}px Segoe UI`;ctx.textAlign='center';ctx.textBaseline='middle';
    for(let v=-1000;v<=1000;v+=500) {
      ctx.strokeStyle='#314d3b22';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(p.x(v),margin);ctx.lineTo(p.x(v),size-margin);ctx.moveTo(margin,p.y(v));ctx.lineTo(size-margin,p.y(v));ctx.stroke();
      ctx.fillStyle='#536755';ctx.fillText(String(v/1000).replace('.',','),p.x(v),size-margin+20);ctx.fillText(String(v/1000).replace('.',','),28,p.y(v));
    }
    ctx.strokeStyle='#9caa96';ctx.strokeRect(margin,margin,span,span);
    ctx.fillStyle='#4d654f';ctx.font=`${12*fontScale}px Segoe UI`;ctx.fillText('Восток от ориентира, км',size/2,size-13);ctx.textAlign='left';ctx.fillText('Север ↑ · км',margin,22);
    ctx.textAlign='right';ctx.fillText('Сетка UTM 37N',size-margin,22);
    const x=p.x(0),y=p.y(0);ctx.strokeStyle='white';ctx.lineWidth=6;ctx.beginPath();ctx.moveTo(x-9,y);ctx.lineTo(x+9,y);ctx.moveTo(x,y-9);ctx.lineTo(x,y+9);ctx.stroke();ctx.strokeStyle='#253e32';ctx.lineWidth=2;ctx.stroke();
    // A true metric scale bar in the common projected display coordinates.
    const bx=margin+15,by=size-margin-19,bw=p.x(500)-p.x(0);ctx.fillStyle='#ffffffdf';ctx.fillRect(bx-6,by-22,bw+12,32);ctx.strokeStyle='#334d3a';ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(bx,by-5);ctx.lineTo(bx,by);ctx.lineTo(bx+bw,by);ctx.lineTo(bx+bw,by-5);ctx.stroke();ctx.fillStyle='#334d3a';ctx.font=`${11*fontScale}px Segoe UI`;ctx.textAlign='center';ctx.fillText('500 м',bx+bw/2,by-12);
  }
  function drawTerrain() {
    const t=data.terrain,ctx=background($('terrain-map'));
    paintCells(ctx,t.geometry,t.values,contextExtent,...t.range_m,'terrain');frame(ctx,contextExtent);
    $('terrain-min').textContent=`${fmt(t.range_m[0],1)} м`;$('terrain-max').textContent=`${fmt(t.range_m[1],1)} м`;
    $('terrain-values').innerHTML=`В ячейке ориентира: <strong>${fmt(t.landmark_height_m,1)} м</strong><br>В окрестности: ${fmt(t.range_m[0],1)}–${fmt(t.range_m[1],1)} м. Десятые доли не означают такую полевую точность.`;
  }
  function drawSoil() {
    const prop=$('soil-property').value,depth=$('soil-depth').value,l=data.soil.layers[prop][depth],g=data.soil.geometry;
    const valid=l.mean.flat().filter(finite),lo=Math.min(...valid),hi=Math.max(...valid),ctx=background($('soil-map'));
    paintCells(ctx,g,l.mean,contextExtent,lo,hi,'soil');frame(ctx,contextExtent);
    const p=transform(contextExtent),q=polygon(g,soilRow,soilCol);ctx.beginPath();q.forEach(([x,y],i)=>i?ctx.lineTo(p.x(x),p.y(y)):ctx.moveTo(p.x(x),p.y(y)));ctx.closePath();ctx.strokeStyle='#fff';ctx.lineWidth=6;ctx.stroke();ctx.strokeStyle='#263e31';ctx.lineWidth=2;ctx.stroke();
    const unit=prop==='phh2o'?'':prop==='soc'?' г/кг':' %';
    $('soil-min').textContent=fmt(lo,1)+unit;$('soil-max').textContent=fmt(hi,1)+unit;
    const where=soilRow===4&&soilCol===4?'Ячейка ориентира':'Выбранная ячейка окрестности';
    $('soil-values').innerHTML=`${where} · ${labels[prop]}<br><strong>${fmt(l.mean[soilRow][soilCol],1)}${unit}</strong> · 5–95%: ${fmt(l['Q0.05'][soilRow][soilCol],1)}–${fmt(l['Q0.95'][soilRow][soilCol],1)}${unit}`;
  }
  function escape(s) { return String(s).replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch])); }
  function chart() {
    if(!rows.length){$('history-chart').textContent='В выбранном периоде пока нет сохранённых съёмок.';return;}
    const width=1100,height=300,left=55,right=25,top=18,bottom=225;
    const ts=rows.map(r=>Date.parse(r.acquired_at_utc)),minT=ts[0]-43200000,maxT=ts.at(-1)+43200000;
    const all=rows.flatMap(r=>Object.values(r.spatial_range).flat()).filter(finite);
    const lo=Math.floor(Math.min(0,...all)*10)/10-.05,hi=Math.ceil(Math.max(.4,...all)*10)/10+.05;
    const x=t=>left+(t-minT)/(maxT-minT)*(width-left-right),y=v=>bottom-(v-lo)/(hi-lo)*(bottom-top);
    let svg=`<svg viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg" aria-label="NDVI и NDMI по датам, с пропусками">`;
    for(let v=Math.ceil(lo*10)/10;v<=hi+.0001;v+=.1) svg+=`<line x1="${left}" x2="${width-right}" y1="${y(v)}" y2="${y(v)}" stroke="#e3e8de"/><text x="${left-12}" y="${y(v)+4}" text-anchor="end" font-size="11" fill="#64755f">${fmt(v,1)}</text>`;
    svg+=`<line x1="${x(ts[selected])}" x2="${x(ts[selected])}" y1="${top}" y2="${bottom+27}" stroke="#a4b299" stroke-dasharray="4 4"/>`;
    rows.forEach((r,i)=>{
      for(const [key,col,off] of [['ndvi','#326d49',-3],['ndmi','#a76229',3]]) {
        const v=observation(r,key);if(v===null)continue;
        const [a,b]=r.spatial_range[key],xx=x(ts[i])+off;
        svg+=`<line x1="${xx}" x2="${xx}" y1="${y(a)}" y2="${y(b)}" stroke="${col}" stroke-width="2" opacity=".55"/>`;
        if(key==='ndvi') svg+=`<circle cx="${xx}" cy="${y(v)}" r="4" fill="${col}"/>`;
        else svg+=`<path d="M${xx},${y(v)-4.5}l4.5,4.5l-4.5,4.5l-4.5,-4.5Z" fill="${col}"/>`;
      }
      if(!r.point_window_usable) svg+=`<text x="${x(ts[i])}" y="${bottom+22}" text-anchor="middle" fill="#879080" font-size="16">×</text>`;
      const prev=i?x(ts[i-1]):left-14,next=i<rows.length-1?x(ts[i+1]):width-right+14;
      svg+=`<rect class="date-target" data-scene="${i}" x="${(x(ts[i])+prev)/2}" y="${top}" width="${(next-prev)/2}" height="${bottom-top+29}" fill="transparent" role="button" tabindex="0" aria-label="${escape(date(r.acquired_at_utc)+': '+gapReason(r))}" aria-pressed="${i===selected}"><title>${escape(date(r.acquired_at_utc,true)+' · '+gapReason(r))}</title></rect>`;
    });
    for(let t=minT;t<=maxT;t+=(maxT-minT)/6) {
      const day=new Date(t).toLocaleDateString('ru-RU',{timeZone:'Europe/Moscow',day:'2-digit',month:'2-digit'});
      svg+=`<text x="${x(t)}" y="${height-20}" text-anchor="middle" font-size="12" fill="#64755f">${day}</text>`;
    }
    $('history-chart').innerHTML=svg+'</svg>';
    $('history-chart').querySelectorAll('[data-scene]').forEach(el=>{
      const choose=()=>selectScene(Number(el.dataset.scene));el.addEventListener('click',choose);
      el.addEventListener('keydown',e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();choose();}});
    });
  }
  function drawSatellite() {
    const ctx=background($('satellite-map')),extent=1250;
    if(currentMap) {
      const m=currentMap,p=transform(extent),a=m[index],cell=span/(2*extent)*m.cell_m;
      for(let r=0;r<m.height;r++) for(let c=0;c<m.width;c++) {
        ctx.fillStyle=color(a[r][c],-1,1,index);ctx.fillRect(p.x(m.west_m+c*20),p.y(m.north_m-r*20),cell+.1,cell+.1);
      }
      // The audited 3×3 window is centred on the containing pixel, not snapped to the landmark.
      const west=m.west_m+(m.width/2-1)*20,north=m.north_m-(m.height/2-1)*20;
      ctx.strokeStyle='#fff';ctx.lineWidth=5;ctx.strokeRect(p.x(west),p.y(north),cell*3,cell*3);ctx.strokeStyle='#273e32';ctx.lineWidth=2;ctx.strokeRect(p.x(west),p.y(north),cell*3,cell*3);
    }
    frame(ctx,extent);
    $('index-gradient').className='gradient '+index;
    $('satellite-map').setAttribute('aria-label',rows[selected]?`Карта ${index.toUpperCase()} · ${date(rows[selected].acquired_at_utc)}. Серый — исключённые пиксели.`:'Карта не загружена: нет выбранной съёмки');
  }
  async function selectScene(i) {
    selected=i;const s=rows[i],ticket=++request;
    $('scene-date').value=String(i);$('previous-scene').disabled=i===0;$('next-scene').disabled=i===rows.length-1;
    $('scene-title').textContent=date(s.acquired_at_utc,true)+' МСК';
    $('scene-quality').textContent=gapReason(s);$('scene-quality').className='quality'+(s.point_window_usable?'':s.extraction_status==='error'?' error':' gap');
    $('scene-values').innerHTML='<div class="value-grid">'+['ndvi','ndmi'].map(k=>`<div><small>${k.toUpperCase()} · медиана 60 м</small><strong>${fmt(observation(s,k))}</strong><span>${s.spatial_range[k]?'Разброс пикселей: '+s.spatial_range[k].map(v=>fmt(v)).join('–'):'Наблюдение отсутствует'}</span></div>`).join('')+'</div>';
    $('scene-support').textContent=s.extraction_status==='error'?'Пиксели не получены или не прошли проверку. Это технический пропуск, а не доказательство облачности.':`Пригодно ${s.valid_land_cells_in_point_60m_window} из 9 ячеек окна. Нужны не менее 5 и пригодная центральная ячейка. Вся показанная окрестность: ${fmt(s.context_valid_land_fraction*100,0)}% пригодной поверхности после фильтра.`;
    $('scene-local-class').textContent=s.extraction_status==='error'?'Класс поверхности не установлен.':`Класс центрального пикселя SCL: ${sclLabels[s.point_scl]||'неизвестен'}. Отступ от исключённых пикселей может убрать и соседние ячейки.`;
    $('scene-metadata').innerHTML=[['Съёмка, МСК',date(s.acquired_at_utc,true)],['Обработка, МСК',date(s.processing_generated_at_utc,true)],['Каталог, МСК',date(s.catalogue_created_at_utc,true)],['Получено в архив, МСК',date(s.retrieved_at_utc,true)],['Версия обработки',s.processing_baseline],['Облачность всей плитки',fmt(s.cloud_cover_tile_percent,1)+'%']].map(([k,v])=>`<dt>${escape(k)}</dt><dd>${escape(v)}</dd>`).join('');
    $('scene-table').querySelectorAll('tr').forEach((tr,j)=>tr.classList.toggle('selected',i===j));
    chart();currentMap=null;drawSatellite();$('scene-map-status').textContent='Загружаем карту выбранной даты…';
    if(s.extraction_status==='error'){$('scene-map-status').textContent='Карта недоступна: ошибка получения или проверки. Прежняя съёмка не подставлена.';return;}
    try {
      const key=period+':'+(s.map_key||s.id);let m=maps.get(key);
      if(!m){const response=await fetch((period==='recent'?'/api/land/recent/scene/':'/api/land/scene/')+encodeURIComponent(s.map_key||s.id));if(!response.ok)throw Error('Не удалось получить карту выбранной съёмки.');m=await response.json();maps.set(key,m);}
      if(ticket!==request)return;
      currentMap=m;drawSatellite();$('scene-map-status').textContent=`${index.toUpperCase()} · ${date(s.acquired_at_utc)} · ${s.point_window_usable?'у ориентира есть наблюдение':'у ориентира пропуск'}`;
    } catch(err) {if(ticket===request){$('scene-map-status').textContent=err.message+' Предыдущий снимок не подставлен.';}}
  }
  function resetComparison(message) {
    ++changeRequest;changeData=null;activeWindow=null;$('change-result').hidden=true;$('change-status').textContent=message;$('comparison-return').hidden=true;
  }
  function comparisonChoices() {
    const good=rows.filter(s=>s.point_window_usable&&s.extraction_status!=='error');
    const options=good.map(s=>`<option value="${escape(s.map_key||s.id)}">${escape(date(s.acquired_at_utc,true))} · ${fmt(s.context_valid_land_fraction*100,0)}% окрестности</option>`).join('');
    $('change-before').innerHTML=options;$('change-after').innerHTML=options;
    for(const id of ['change-before','change-after','compare-scenes'])$(id).disabled=good.length<2;
    if(good.length<2){resetComparison('Нужны минимум две пригодные съёмки в выбранном периоде.');return;}
    const b=good.at(-1),prior=good.slice(0,-1).filter(s=>Date.parse(s.acquired_at_utc)<Date.parse(b.acquired_at_utc));
    const a=prior.findLast(s=>s.context_valid_land_fraction>=.8&&s.processing_baseline===b.processing_baseline)||prior.at(-1);
    if(!a){resetComparison('Нужны две разные даты съёмки.');return;}
    $('change-before').value=a.map_key||a.id;$('change-after').value=b.map_key||b.id;
    $('change-default').textContent='Начальная пара: последняя пригодная съёмка и ближайшая предыдущая с покрытием окрестности ≥80% и той же версией обработки, если такая есть. Все пригодные съёмки доступны в списках; отбор не зависит от величины изменения индексов.';
    loadComparison();
  }
  async function loadComparison() {
    resetComparison('Сопоставляем пиксели и проверяем чувствительность к сдвигу…');const ticket=changeRequest;
    $('compare-scenes').disabled=true;
    const query=new URLSearchParams({period,before:$('change-before').value,after:$('change-after').value});
    try {
      const r=await fetch('/api/land/compare?'+query);const b=await r.json();if(!r.ok)throw Error(b.error||'Сравнение недоступно.');if(ticket!==changeRequest)return;
      changeData=b;$('change-result').hidden=false;$('change-status').textContent=`${date(b.before.acquired_at_utc,true)} → ${date(b.after.acquired_at_utc,true)} МСК · ${fmt(b.days_between,1)} дн. между съёмками`;
      $('change-a-title').textContent=`A · ${date(b.before.acquired_at_utc,true)}`;$('change-b-title').textContent=`B · ${date(b.after.acquired_at_utc,true)}`;
      $('change-warnings').innerHTML=b.warnings.map(v=>`<li>${escape(v)}</li>`).join('');
      renderComparison();
    }catch(err){if(ticket===changeRequest)$('change-status').textContent=err.message+' Предыдущее сравнение не подставлено.';}
    finally{if(ticket===changeRequest)$('compare-scenes').disabled=false;}
  }
  function changeWindows() {
    if(!changeData)return [];
    return changeData.blocks.filter(b=>inspectionCandidate(b,changeMetric,Number($('change-threshold').value),changeData.processing_comparable))
      .sort((a,b)=>Math.abs(b[changeMetric].median)-Math.abs(a[changeMetric].median)||a.id.localeCompare(b.id));
  }
  function outlineWindow(ctx,b,extent,colour,width=2) {
    const p=transform(extent);ctx.strokeStyle=colour;ctx.lineWidth=width;ctx.strokeRect(p.x(b.west_m),p.y(b.north_m),60*span/(extent*2),60*span/(extent*2));
  }
  function drawComparison() {
    if(!changeData)return;
    const d=changeData,p=transform(1250),cell=20*span/2500,layer=$('change-layer').value;
    for(const [name,m] of [['a',d.maps.before],['b',d.maps.after]]) {
      const ctx=background($('change-map-'+name));
      for(let r=0;r<m.height;r++)for(let c=0;c<m.width;c++){
        ctx.fillStyle=color(m[changeMetric][r][c],-1,1,changeMetric);ctx.fillRect(p.x(m.west_m+c*20),p.y(m.north_m-r*20),cell+.1,cell+.1);
      }
      frame(ctx,1250);if(activeWindow){outlineWindow(ctx,activeWindow,1250,'white',5);outlineWindow(ctx,activeWindow,1250,'#283f32',2);}
      $('change-map-'+name).setAttribute('aria-label',`${changeMetric.toUpperCase()} · ${date(d[name==='a'?'before':'after'].acquired_at_utc)} · серые пиксели исключены`);
    }
    const ctx=background($('change-map')),g=d.grid;const coverageColours=['#b8cba8','#c7b184','#9dbdc9','#d8dcd5'];
    for(let r=0;r<g.height;r++)for(let c=0;c<g.width;c++) {
      ctx.fillStyle=layer==='coverage'?coverageColours[d.coverage[r][c]]:layer==='inspection'?(d.coverage[r][c]===0?'#e4e8df':'#cfd5cc'):color(d.delta[changeMetric][r][c],-.3,.3,'delta');
      ctx.fillRect(p.x(g.west_m+c*20),p.y(g.north_m-r*20),cell+.1,cell+.1);
    }
    const windows=changeWindows();
    if(layer!=='coverage')for(const b of windows){const col=b[changeMetric].median<0?'#78599b':'#b58332';if(layer==='inspection'){ctx.fillStyle=col;ctx.fillRect(p.x(b.west_m),p.y(b.north_m),cell*3,cell*3);}outlineWindow(ctx,b,1250,layer==='delta'?'#253c32':col,1.5);}
    frame(ctx,1250);if(activeWindow){outlineWindow(ctx,activeWindow,1250,'white',6);outlineWindow(ctx,activeWindow,1250,'#253c32',2.5);}
    const titles={delta:`Δ${changeMetric.toUpperCase()} · B − A`,coverage:'Пригодность в обе даты',inspection:`Окна осмотра · ${changeMetric.toUpperCase()}`};
    $('change-map-title').textContent=titles[layer];
    $('change-legend').innerHTML=layer==='coverage'?'<span class="legend-key key-paired"></span>Обе даты <span class="legend-key key-a"></span>Только A <span class="legend-key key-b"></span>Только B <span class="legend-key key-gap"></span>Ни одна':layer==='delta'?'<div class="change-gradient"></div><div class="change-legend-scale"><span>≤−0,30 · снижение</span><span>0</span><span>≥+0,30 · рост</span></div><p>Цвет насыщается за пределами ±0,30; значения не обрезаны. Серый — нет общей пары.</p>':'<span class="legend-key key-down"></span>Снижение индекса <span class="legend-key key-up"></span>Рост индекса. Цвет не означает повреждение или улучшение состояния растений.';
  }
  function renderWindowDetail() {
    const b=activeWindow;
    $('inspect-here').disabled=!b;
    $('history-here').disabled=!b;
    if(!b){$('inspection-detail').textContent='Выберите окно на карте или в таблице. Отсутствие выделенных окон не подтверждает отсутствие проблем.';return;}
    const qualifies=inspectionCandidate(b,changeMetric,Number($('change-threshold').value),changeData.processing_comparable);
    const range=v=>v?v.map(x=>fmt(x)).join('…'):'Недостаточно данных';
    $('inspection-detail').innerHTML=`<p>Приблизительный центр: <strong>${fmt(b.centre.latitude,5)}, ${fmt(b.centre.longitude,5)}</strong></p><p>${qualifies?'Прошло фильтр мест для осмотра. Причину изменения нужно установить на месте.':'Не прошло текущий фильтр осмотра. Это не заключение о состоянии растений.'}</p><p>Общих пригодных пикселей: ${b.paired_cells}/9.</p>`+['ndvi','ndmi'].map(k=>`<dl><dt>Δ${k.toUpperCase()} · медиана парных разностей</dt><dd>${fmt(b[k].median)}</dd><dt>Разброс парных разностей по пикселям</dt><dd>${escape(range(b[k].spatial_range))}</dd><dt>Медианы при сдвигах B ±20 м</dt><dd>${escape(range(b[k].shift_range))} · ${b[k].shift_scenarios}/9 сценариев</dd><dt>Пикселей с одним направлением</dt><dd>Рост ${b[k].positive_cells}/9 · снижение ${b[k].negative_cells}/9</dd></dl>`).join('');
  }
  function chooseWindow(id) {activeWindow=changeData?.blocks.find(b=>b.id===id)||null;drawComparison();renderWindowDetail();}
  function renderComparison() {
    if(!changeData)return;
    const d=changeData,s=d.statistics,windows=changeWindows();
    if(!activeWindow&&windows.length)activeWindow=windows[0];
    $('change-metrics').innerHTML=`<div><strong>${fmt(s.paired_fraction_before*100,1)}%</strong><span>общих пригодных пикселей от вырезки A</span></div><div><strong>${s.paired_pixels}</strong><span>парных ячеек 20 м</span></div><div><strong>${windows.length}</strong><span>окон 60×60 м по выбранному фильтру</span></div>`;
    $('change-point').textContent=d.point.usable?`У ориентира, на одинаковых пикселях: ΔNDVI ${fmt(d.point.ndvi)}, ΔNDMI ${fmt(d.point.ndmi)}; ${d.point.paired_cells}/9 пар. Это не сводка по саду.`:`У ориентира недостаточно общих пикселей (${d.point.paired_cells}/9); разность окна 60 м не рассчитана.`;
    $('inspection-summary').textContent=windows.length?`Выделено ${windows.length} окон по фильтру. Рост и снижение индекса — повод уточнить причины, а не готовый диагноз.`:'По текущему фильтру окна не выделены. Это не подтверждение отсутствия изменений или проблем; проверьте покрытие, порог и ограничения.';
    $('inspection-table').innerHTML=windows.slice(0,12).map((b,i)=>`<tr><td><button class="window-link" data-window="${b.id}">Окно ${i+1}</button></td><td>${fmt(b.centre.latitude,5)}, ${fmt(b.centre.longitude,5)}</td><td>${fmt(b[changeMetric].median)}</td><td>${b[changeMetric].shift_range.map(v=>fmt(v)).join('…')}</td></tr>`).join('');
    $('inspection-table').querySelectorAll('[data-window]').forEach(button=>button.addEventListener('click',()=>chooseWindow(button.dataset.window)));
    drawComparison();renderWindowDetail();
  }
  function bindComparison() {
    $('comparison-return').addEventListener('click',()=>loadHistory());
    $('inspect-here').addEventListener('click',()=>journal.prepare(window.LandJournal.inspectionTarget(changeData,activeWindow,changeMetric,Number($('change-threshold').value))));
    $('history-here').addEventListener('click',()=>journal.showPlace(window.LandJournal.inspectionTarget(changeData,activeWindow,changeMetric,Number($('change-threshold').value))));
    $('compare-scenes').addEventListener('click',loadComparison);
    for(const id of ['change-before','change-after'])$(id).addEventListener('change',()=>{resetComparison('Даты изменены. Нажмите «Сравнить».');$('compare-scenes').disabled=false;});
    for(const k of ['ndvi','ndmi'])$('change-'+k).addEventListener('click',()=>{changeMetric=k;for(const n of ['ndvi','ndmi'])$('change-'+n).setAttribute('aria-pressed',String(n===k));activeWindow=null;renderComparison();});
    for(const id of ['change-layer','change-threshold'])$(id).addEventListener('change',renderComparison);
    $('change-map').addEventListener('click',e=>{
      if(!changeData)return;const b=e.currentTarget.getBoundingClientRect(),x=((e.clientX-b.left)*640/b.width-margin)/span*2500-1250,y=1250-((e.clientY-b.top)*640/b.height-margin)/span*2500;
      const block=changeData.blocks.find(v=>x>=v.west_m&&x<v.west_m+60&&y<=v.north_m&&y>v.north_m-60);if(block)chooseWindow(block.id);
    });
    $('change-map').addEventListener('keydown',e=>{
      if(!changeData||!['ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(e.key))return;e.preventDefault();
      const all=changeData.blocks;if(!activeWindow){chooseWindow(all[0]?.id);return;}
      const x=activeWindow.west_m+(e.key==='ArrowRight'?60:e.key==='ArrowLeft'?-60:0),y=activeWindow.north_m+(e.key==='ArrowUp'?60:e.key==='ArrowDown'?-60:0);
      const next=all.find(v=>Math.abs(v.west_m-x)<.01&&Math.abs(v.north_m-y)<.01);if(next)chooseWindow(next.id);
    });
  }
  function bind() {
    journal=window.LandJournal.setup({onOpenSaved:(d,record)=>{
      ++changeRequest;changeData=d;activeWindow=d.blocks.find(b=>b.id===record.payload.selected_window.id)||null;changeMetric=record.payload.metric;
      $('change-threshold').value=String(record.payload.display_threshold.toFixed(2));
      for(const k of ['ndvi','ndmi'])$('change-'+k).setAttribute('aria-pressed',String(k===changeMetric));
      for(const [id,s] of [['change-before',d.before],['change-after',d.after]]){
        $(id).innerHTML=`<option>${escape(date(s.acquired_at_utc,true))} · сохранено в записи</option>`;$(id).disabled=true;
      }
      $('compare-scenes').disabled=true;$('comparison-return').hidden=false;$('change-result').hidden=false;
      $('change-status').textContent=`Сохранённое сравнение из осмотра ${date(record.payload.observed_at_utc,true)} МСК. Новые снимки его не заменяют.`;
      $('change-a-title').textContent='A · '+date(d.before.acquired_at_utc,true);$('change-b-title').textContent='B · '+date(d.after.acquired_at_utc,true);
      $('change-warnings').innerHTML=d.warnings.map(v=>`<li>${escape(v)}</li>`).join('');renderComparison();$('changes').scrollIntoView({behavior:'smooth',block:'start'});
    }});
    bindComparison();
    $('history-period').addEventListener('change',()=>loadHistory());
    $('refresh-satellite').addEventListener('click',refreshSatellite);
    for(const id of ['soil-property','soil-depth'])$(id).addEventListener('change',drawSoil);
    $('soil-reset').addEventListener('click',()=>{soilRow=4;soilCol=4;drawSoil();});
    $('soil-map').addEventListener('click',e=>{
      const b=e.currentTarget.getBoundingClientRect(),xx=(e.clientX-b.left)*size/b.width,yy=(e.clientY-b.top)*size/b.height;
      const point=[(xx-margin)/span*2*contextExtent-contextExtent,contextExtent-(yy-margin)/span*2*contextExtent];
      const g=data.soil.geometry;for(let r=0;r<g.height;r++)for(let c=0;c<g.width;c++)if(pointInCell(point,polygon(g,r,c))){soilRow=r;soilCol=c;drawSoil();return;}
    });
    $('soil-map').addEventListener('keydown',e=>{if(!['ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(e.key))return;e.preventDefault();soilRow=Math.max(0,Math.min(8,soilRow+(e.key==='ArrowDown'?1:e.key==='ArrowUp'?-1:0)));soilCol=Math.max(0,Math.min(8,soilCol+(e.key==='ArrowRight'?1:e.key==='ArrowLeft'?-1:0)));drawSoil();});
    $('scene-date').addEventListener('change',e=>selectScene(Number(e.target.value)));
    $('previous-scene').addEventListener('click',()=>selectScene(selected-1));$('next-scene').addEventListener('click',()=>selectScene(selected+1));
    for(const key of ['ndvi','ndmi'])$('show-'+key).addEventListener('click',()=>{index=key;for(const k of ['ndvi','ndmi'])$('show-'+k).setAttribute('aria-pressed',String(k===key));drawSatellite();if(currentMap)$('scene-map-status').textContent=`${index.toUpperCase()} · ${date(rows[selected].acquired_at_utc)} · ${rows[selected].point_window_usable?'у ориентира есть наблюдение':'у ориентира пропуск'}`;});
  }
  function renderFreshness(sat) {
    const ordered=sat.scenes, good=ordered.filter(s=>s.point_window_usable),last=ordered.at(-1),lastGood=good.at(-1);
    const checked=sat.snapshot?.checked_at_utc;
    function age(t){if(!t||!serverNow)return '';const days=(Date.parse(serverNow)-Date.parse(t))/86400000;return days<0?'Дата позже времени проверки':`Возраст: ${fmt(days,1)} дн. на ${date(serverNow)}`;}
    $('freshness').innerHTML=[['Последняя съёмка',last?date(last.acquired_at_utc,true)+' МСК':'Нет в сохранённом каталоге',last?age(last.acquired_at_utc):''],['Последняя пригодная у ориентира',lastGood?date(lastGood.acquired_at_utc,true)+' МСК':'Нет пригодного наблюдения',lastGood?age(lastGood.acquired_at_utc):'Облака и технические пропуски показаны в истории'],['Проверка каталога',period==='recent'?(checked?date(checked,true)+' МСК':'Ещё не выполнена'):'Архив весны 2024',period==='recent'?'Все сцены в окне последних 90 дней':'Фиксированная архивная выборка']].map(([k,v,n])=>`<div><small>${escape(k)}</small><strong>${escape(v)}</strong><small>${escape(n)}</small></div>`).join('');
  }
  async function loadHistory(keepId=null) {
    resetComparison('Загружаем список пригодных съёмок…');
    for(const id of ['change-before','change-after','compare-scenes'])$(id).disabled=true;
    $('change-before').innerHTML='';$('change-after').innerHTML='';
    const ticket=++historyRequest;period=$('history-period').value;++request;currentMap=null;rows=[];selected=0;
    $('scene-title').textContent='';$('scene-values').textContent='';$('scene-quality').textContent='';$('scene-support').textContent='';$('scene-local-class').textContent='';$('scene-metadata').textContent='';$('history-chart').textContent='Загружаем выбранный период…';
    $('scene-date').innerHTML='';$('scene-table').innerHTML='';$('quality-summary').textContent='';$('usable-count').textContent='—';$('gap-count').textContent='—';$('history-empty').hidden=true;
    $('previous-scene').disabled=true;$('next-scene').disabled=true;$('scene-date').disabled=true;$('scene-map-status').textContent='Карта пока не загружена';drawSatellite();
    try {
      let sat=archiveSatellite;
      if(period==='recent'){const r=await fetch('/api/land/recent');const b=await r.json();if(!r.ok)throw Error(b.error||'Не удалось прочитать свежую историю.');sat=b;}
      if(ticket!==historyRequest)return;
      rows=sat.scenes;snapshot=sat.snapshot||null;const st=sat.statistics;
      $('history-period-label').textContent=period==='recent'?(snapshot?`${date(snapshot.query_start_utc)}–${date(snapshot.query_end_utc)} · Sentinel-2 L2A`:'Последние 90 дней · Sentinel-2 L2A'):'Март–май 2024 · Sentinel-2 L2A';
      $('usable-count').textContent=`${st.usable_point_dates} из ${st.catalogue_scenes}`;
      $('gap-count').textContent=finite(st.longest_gap_between_usable_acquisitions_days)?`${fmt(st.longest_gap_between_usable_acquisitions_days,0)} дней`:'—';
      renderFreshness(sat);
      comparisonChoices();
      $('quality-summary').textContent=`Не прошли фильтр поверхности и облаков: ${st.rejected_point_dates||0}. Ошибок получения или проверки: ${st.extraction_error_dates||0}. Пропуски не заполнены.`;
      $('scene-date').innerHTML=rows.map((r,i)=>`<option value="${i}">${escape(date(r.acquired_at_utc,true))} · ${r.point_window_usable?'пригодно':gapReason(r).replace('Пропуск: ','')}</option>`).join('');
      $('scene-table').innerHTML=rows.map((r,i)=>`<tr><td><button type="button" class="date-link" data-row="${i}">${escape(date(r.acquired_at_utc,true))}</button></td><td>${escape(gapReason(r))}</td><td>${finite(r.valid_land_cells_in_point_60m_window)?r.valid_land_cells_in_point_60m_window+'/9':'—'}</td><td>${fmt(observation(r,'ndvi'))}</td><td>${fmt(observation(r,'ndmi'))}</td></tr>`).join('');
      $('scene-table-count').textContent=`В каталоге ${rows.length} съёмок. Технических ошибок: ${st.extraction_error_dates||0}.`;
      $('scene-table').querySelectorAll('[data-row]').forEach(b=>b.addEventListener('click',()=>{selectScene(Number(b.dataset.row));$('scene-date').focus();}));
      if(!rows.length){chart();$('history-empty').hidden=false;$('history-empty').textContent=snapshot?'Каталог проверен: в этом периоде съёмок не найдено. Это не оценка состояния растений.':'Свежий архив пока не загружен. Нажмите «Проверить новые снимки». Архив 2024 доступен в выборе периода.';return;}
      $('scene-date').disabled=false;await selectScene(keepId?Math.max(0,rows.findIndex(r=>r.id===keepId)):period==='recent'?rows.length-1:0);
    } catch(err){if(ticket===historyRequest){$('history-empty').hidden=false;$('history-empty').textContent=err.message+' История другого периода не подставлена.';}}
  }
  async function pollStatus(reloadWhenDone=false) {
    try {
      const r=await fetch('/api/land/status');if(!r.ok)throw Error('Не удалось проверить состояние обновления.');
      const b=await r.json();token=b.token;serverNow=b.server_now_utc;
      $('refresh-status').textContent=b.job.status==='idle'?'Сохранённая история доступна. Проверить новые поступления можно кнопкой.':b.job.message;
      $('refresh-satellite').disabled=b.job.status==='running';
      if(b.job.status==='running') {clearTimeout(polling);polling=setTimeout(()=>pollStatus(true),2000);}
      else if(reloadWhenDone&&period==='recent')await loadHistory(rows[selected]?.id);
    } catch(err){$('refresh-status').textContent=err.message+' Сохранённая история остаётся доступной.';$('refresh-satellite').disabled=!token;}
  }
  async function refreshSatellite() {
    if(!token)return;$('refresh-satellite').disabled=true;
    try {
      const r=await fetch('/api/land/refresh',{method:'POST',headers:{'Content-Type':'application/json','X-Field-Token':token},body:'{}'});const b=await r.json();
      if(!r.ok)throw Error(b.error||'Не удалось начать обновление.');$('refresh-status').textContent=b.job.message;await pollStatus(true);
    }catch(err){$('refresh-status').textContent=err.message+' При обновлении страницы соединение можно восстановить.';$('refresh-satellite').disabled=false;}
  }
  async function start() {
    try {
      const response=await fetch('/api/land');const body=await response.json();if(!response.ok)throw Error(body.error||'Не удалось загрузить архив карт.');
      data=body;archiveSatellite=body.satellite;bind();
      contextExtent=Math.ceil(Math.max(...data.soil.geometry.east.flat().map(Math.abs),...data.soil.geometry.north.flat().map(Math.abs),...data.terrain.geometry.east.flat().map(Math.abs),...data.terrain.geometry.north.flat().map(Math.abs))/250)*250;
      $('extraction-date').textContent=`Рельеф, почвы и архив весны 2024 получены ${date(data.extracted_at_utc)}; для свежих сцен даты получения указаны отдельно. Контроль исходных файлов выполнен перед загрузкой. Карты не использованы для переобучения погодных моделей.`;
      $('land-content').hidden=false;$('load-status').hidden=true;drawTerrain();drawSoil();
      window.addEventListener('resize',()=>{drawTerrain();drawSoil();drawSatellite();drawComparison();});
      await pollStatus();await loadHistory();await journal.load();
      if(['#context','#history','#changes','#land-journal','#sources'].includes(location.hash)) {
        requestAnimationFrame(()=>document.getElementById(location.hash.slice(1))?.scrollIntoView({behavior:'instant',block:'start'}));
      }
    }catch(err){$('load-status').className='error';$('load-status').textContent=err.message+' Попробуйте обновить страницу после проверки архива. Карты не заменяются демонстрационными.';}
  }
  start();
})();
