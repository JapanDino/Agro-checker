'use strict';
(async()=>{
const $=id=>document.getElementById(id),ns='http://www.w3.org/2000/svg';
const status=$('status');
try {
 const response=await fetch('/farm-reconstruction/data.json');if(!response.ok)throw Error('Данные реконструкции недоступны');
 const data=await response.json(),s=data.summary;
 if(data.features.length!==s.fragment_count)throw Error('Неполный набор фрагментов');
 $('count').textContent=s.fragment_count;$('alignment').textContent=`≈ ${Math.round(s.relative_alignment_p95_m)} м`;
 $('registration-description').textContent=`${s.training_count} совпадение использовано для привязки, ${s.reserved_count} — для отдельной проверки в отложенных пространственных блоках. Для 95% этих проверочных совпадений отклонение не превышает ${s.relative_alignment_p95_m.toFixed(1).replace('.',',')} м.`;
 const svg=$('map'),defaults=[285,575,780,1395];let view=[...defaults],selected=null,drag=null,moved=false;
 const shape=(tag,attrs,parent)=>{const n=document.createElementNS(ns,tag);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,v);parent.appendChild(n);return n};
 const pointString=p=>p.map(x=>x.join(',')).join(' '),nodes=new Map();
 const renderView=()=>{svg.setAttribute('viewBox',view.join(' '));const r=svg.getBoundingClientRect(),metres=64*Math.max(view[2]/r.width,view[3]/r.height)*s.ground_m_per_source_pixel;$('scale-label').textContent=metres>=1000?`≈ ${(metres/1000).toFixed(1)} км`:`≈ ${Math.round(metres/10)*10} м`;};
 function select(item){
  selected=item;
  for(const [id,n]of nodes){n.poly.classList.toggle('selected',id===item.id);n.label.classList.toggle('selected-label',id===item.id);n.button.setAttribute('aria-pressed',String(id===item.id));}
  $('selected-title').textContent=item.id;$('selected-count').textContent=`${item.id} / ${s.fragment_count}`;
  const body=$('detail-body');body.replaceChildren();
  const add=(t)=>{const p=document.createElement('p');p.textContent=t;body.appendChild(p)};
  add(item.icon_occluded?'Край частично скрыт булавкой или указателем. Линия под значком восстановлена предположительно.':'Край обведён по видимой цветной области. Внутренние границы полей могут быть неразличимы.');
  add(`Условная полоса ±${item.working_band_m} м. Это запас для проверки, без статистической гарантии.`);
  add(`Внутренняя опорная точка: ${item.latitude.toFixed(5)}° N, ${item.longitude.toFixed(5)}° E. Это не координаты датчика.`);
  add('Номер поля, культура и принадлежность не установлены.');$('focus').disabled=false;
  document.dispatchEvent(new CustomEvent('fragment-selected',{detail:item.id}));
 }
 for(const item of data.features){
  for(const rings of item.bands){const d=rings.map(r=>'M'+r.map(p=>p.join(' ')).join('L')+'Z').join('');shape('path',{d,class:'band'},$('band-layer'))}
  const poly=shape('polygon',{points:pointString(item.pixels),fill:item.fill,stroke:item.fill},$('area-layer'));
  const title=shape('title',{},poly);title.textContent=`${item.id} · границы не подтверждены · полоса ${item.working_band_m} м`;
  const label=shape('text',{x:item.label[0],y:item.label[1]},$('label-layer'));label.textContent=item.id;
  const b=document.createElement('button');b.textContent=item.id;b.style.setProperty('--colour',item.fill);b.setAttribute('aria-pressed','false');b.setAttribute('aria-label',`Выбрать фрагмент ${item.id}`);$('fragment-list').appendChild(b);
  b.addEventListener('click',()=>select(item));poly.addEventListener('click',()=>{if(!moved)select(item)});label.addEventListener('click',()=>{if(!moved)select(item)});
  nodes.set(item.id,{poly,label,button:b});
 }
 for(const p of data.occlusions)shape('polygon',{points:pointString(p),class:'occlusion'},$('occlusion-layer'));
 $('bands').addEventListener('change',e=>$('band-layer').setAttribute('display',e.target.checked?'inline':'none'));
 $('occlusions').addEventListener('change',e=>$('occlusion-layer').setAttribute('display',e.target.checked?'inline':'none'));
 for(const base of ['satellite','screenshot'])$(base).addEventListener('click',()=>{
  $('base').setAttribute('href',`/farm-reconstruction/${base==='satellite'?'satellite-aligned':'screenshot'}.jpg`);
  $('base-title').textContent=base==='satellite'?'Спутниковая подложка · дата съёмки неизвестна':'Ваш скриншот · неизвестные даты съёмки и захвата';
  for(const id of ['satellite','screenshot'])$(id).setAttribute('aria-pressed',String(id===base));
 });
 const zoom=f=>{const w=Math.min(defaults[2]*1.5,Math.max(95,view[2]*f)),h=w*view[3]/view[2];view=[view[0]+(view[2]-w)/2,view[1]+(view[3]-h)/2,w,h];renderView()};
 $('zoom-in').addEventListener('click',()=>zoom(.7));$('zoom-out').addEventListener('click',()=>zoom(1/.7));$('reset').addEventListener('click',()=>{view=[...defaults];renderView()});
 $('focus').addEventListener('click',()=>{if(!selected)return;const xs=selected.pixels.map(p=>p[0]),ys=selected.pixels.map(p=>p[1]);let w=Math.max(...xs)-Math.min(...xs)+90,h=Math.max(...ys)-Math.min(...ys)+90;const ratio=defaults[2]/defaults[3];w=Math.max(w,h*ratio);h=w/ratio;view=[(Math.min(...xs)+Math.max(...xs)-w)/2,(Math.min(...ys)+Math.max(...ys)-h)/2,w,h];renderView()});
 const local=e=>{const p=svg.createSVGPoint();p.x=e.clientX;p.y=e.clientY;return p.matrixTransform(svg.getScreenCTM().inverse())};
 svg.addEventListener('pointerdown',e=>{if(e.button!==0)return;moved=false;drag={x:e.clientX,y:e.clientY,start:local(e),view:[...view]};svg.classList.add('dragging')});
 svg.addEventListener('pointermove',e=>{if(!drag)return;if(Math.hypot(e.clientX-drag.x,e.clientY-drag.y)<4&&!moved)return;moved=true;const p=local(e);view[0]+=drag.start.x-p.x;view[1]+=drag.start.y-p.y;renderView()});
 const end=()=>{drag=null;svg.classList.remove('dragging')};window.addEventListener('pointerup',end);svg.addEventListener('pointerleave',end);
 document.addEventListener('fragment-request',e=>{const item=data.features.find(f=>f.id===e.detail);if(item)select(item)});
 $('workspace').hidden=false;status.hidden=true;renderView();window.addEventListener('resize',renderView);
}catch(e){status.textContent=`Не удалось открыть карту: ${e.message}. Приближённые данные не заменены другими контурами.`}
})();
