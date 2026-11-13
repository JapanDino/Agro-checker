'use strict';
function scoutingBasemap(map,bundle){
  map.createPane('baseImages');map.getPane('baseImages').style.zIndex='200';map.createPane('thematicImages');map.getPane('thematicImages').style.zIndex='300';
  map.createPane('wideImages');map.getPane('wideImages').style.zIndex='190';
  if(bundle.wide_basemap)L.imageOverlay('/scouting/assets/wide-basemap.jpg',bundle.wide_basemap.bounds,{pane:'wideImages'}).addTo(map);
  L.imageOverlay('/scouting/assets/basemap.jpg',bundle.basemap.bounds,{pane:'baseImages',attribution:'Esri, Vantor, Earthstar Geographics, GIS User Community. Дата мозаики неизвестна.'}).addTo(map);
}

window.ScoutingMap={create({bundle,onSelect,onSite,onLayer}){
  const map=L.map('geo-map',{zoomControl:false,attributionControl:true,minZoom:10,maxZoom:18,zoomSnap:.25});
  map.attributionControl.setPrefix(false);
  scoutingBasemap(map,bundle);
  L.control.scale({imperial:false,position:'topright',maxWidth:110}).addTo(map);
  const shapes=new Map(),sites=L.layerGroup().addTo(map);let selected='R11',period='recent2026',layer='ndmi',raster=null,pin=null,focused=false,comparison={rows:[]},initialized=false,viewportWidth=window.innerWidth;
  const geo=L.geoJSON(bundle.geometry,{style:()=>({color:'#fff',weight:1.2,fillOpacity:.08}),onEachFeature(feature,node){
    const id=feature.properties.id;shapes.set(id,node);node.bindTooltip(id,{permanent:true,direction:'center',className:'area-label',interactive:false});
    node.on('click',()=>onSelect(id));node.on('mouseover',()=>{if(id!==selected)node.setStyle({weight:2,color:'#f2f2df'});});node.on('mouseout',paint);
  }}).addTo(map);
  function padding(){
    const mobile=matchMedia('(max-width:1000px)').matches;
    const dock=document.querySelector('.map-control-dock'),card=document.querySelector('.selection-card');
    const top=(dock?.getBoundingClientRect().height||160)+32;
    const listOpen=document.body.classList.contains('map-list-open');
    const covered=card?Math.max(0,document.getElementById('geo-map').getBoundingClientRect().bottom-card.getBoundingClientRect().top):196;
    const a=mobile?[20,top]:[listOpen?290:24,top],b=mobile?[20,covered+18]:[(card?.getBoundingClientRect().width||320)+44,108];
    const size=map.getSize(),budgets=[Math.max(0,size.x-120),Math.max(0,size.y-140)];
    for(let axis=0;axis<2;axis++){const total=a[axis]+b[axis];if(total>budgets[axis]&&total>0){const scale=budgets[axis]/total;a[axis]*=scale;b[axis]*=scale;}}
    return {paddingTopLeft:a,paddingBottomRight:b};
  }
  function fitAll(){document.body.classList.remove('map-focused');focused=false;map.invalidateSize({pan:false});if(map.getSize().x<1||map.getSize().y<1)return;initialized=true;map.fitBounds(geo.getBounds(),{...padding(),animate:false});}
  function focus(id){document.body.classList.add('map-focused');focused=true;map.invalidateSize({pan:false});map.fitBounds(shapes.get(id).getBounds(),{...padding(),maxZoom:16,animate:false});}
  function paint(){
    for(const[id,node]of shapes){
      const p=comparison.rows.find(r=>r.id===id),value=p?.[layer]?.base;
      let fill='#e8eee2',opacity=.05;
      if(['ndvi','ndmi'].includes(layer)){
        fill=typeof value!=='number'?'#8a9390':value<-.05?'#c58d69':value>.05?'#8cbb8a':'#c7c8a5';opacity=typeof value==='number'?.38:.18;
      }
      node.setStyle({color:id===selected?'#f5e6a4':'#e8eee1',weight:id===selected?3:1.1,fillColor:fill,fillOpacity:opacity});
      const tooltip=node.getTooltip();if(tooltip?.getElement()){
        tooltip.getElement().classList.toggle('active',id===selected);
        tooltip.getElement().style.opacity=id===selected||map.getZoom()>=12.5||node.feature.properties.traced_area_ha>180?'1':'0';
      }
    }
    shapes.get(selected)?.bringToFront();
  }
  function drawSites(){sites.clearLayers();if(layer!=='sensors')return;
    for(const site of bundle.features[selected]?.candidate_sites||[]){
      const text=site.kind==='low'?'Нижняя часть DSM':'Верхняя часть DSM';
      L.circleMarker([site.latitude,site.longitude],{radius:8,color:'#fff',weight:2,fillColor:site.kind==='low'?'#eac17e':'#b9d9b4',fillOpacity:1}).addTo(sites)
        .bindTooltip(text,{permanent:true,direction:'top',className:'site-label'}).on('click',()=>onSite(site));
    }
  }
  function setLayer(value){layer=value;if(raster){map.removeLayer(raster);raster=null;}
    if(bundle.layers[layer]){const spec=bundle.layers[layer];raster=L.imageOverlay('/scouting/assets/'+layer+'.png',spec.bounds,{pane:'thematicImages',opacity:.8}).addTo(map);}
    paint();drawSites();
    const note=document.getElementById('layer-note');note.replaceChildren();
    const descriptions={ndmi:'Цвет по ΔNDMI площади: <−0,05 / ±0,05 / >+0,05. Общая пара дат. Серый — нет пригодного сравнения.',ndvi:'Цвет по ΔNDVI площади: <−0,05 / ±0,05 / >+0,05. Общая пара дат. Серый — нет пригодного сравнения.',terrain:'Copernicus DSM · 0–100 м · исходная сетка около 30 м. Поверхность с деревьями и зданиями.',clay:'Глина · 15–55% · SoilGrids, 0–5 см, сетка 250 м. Внешняя модель, не анализ почвы.',ph:'pH · 5,5–8,5 · SoilGrids, 0–5 см, сетка 250 м. Внешняя модель, не анализ почвы.',sensors:'Места для предварительного осмотра по контрасту DSM. Не подтверждённые точки монтажа.',base:'Спутниковая мозаика. Дата съёмки неизвестна, контуры восстановлены приблизительно.'};
    if(['ndmi','ndvi'].includes(layer)){for(const color of ['#c58d69','#c7c8a5','#8cbb8a']){const dot=document.createElement('i');dot.style.background=color;note.append(dot);}}
    else if(bundle.layers[layer]){const ramp=document.createElement('i');ramp.className='layer-ramp '+layer;note.append(ramp);}
    const span=document.createElement('span');span.textContent=descriptions[layer];note.append(span);onLayer?.(layer);
  }
  document.getElementById('map-layer').addEventListener('change',e=>setLayer(e.target.value));
  map.on('zoomend',paint);
  const observer=new ResizeObserver(()=>{if(document.body.dataset.stage==='1')map.invalidateSize({pan:false});});observer.observe(document.getElementById('geo-map'));
  requestAnimationFrame(()=>{fitAll();});
  return {setComparison(value){comparison=value;paint();},select(id,p){selected=id;period=p||period;paint();drawSites();},focus,fitAll,zoom(direction){map.zoomIn(direction);},
    resize(){requestAnimationFrame(()=>{const changed=viewportWidth!==window.innerWidth;viewportWidth=window.innerWidth;map.invalidateSize({pan:false});if(!initialized||changed){if(focused)focus(selected);else fitAll();}});},
    clearSite(){if(pin){map.removeLayer(pin);pin=null;}},
    showSite(site){if(pin)map.removeLayer(pin);pin=L.circleMarker([site.latitude,site.longitude],{radius:10,color:'#fff',weight:3,fillColor:'#1e5140',fillOpacity:1}).addTo(map);},
    setLayer(value){document.getElementById('map-layer').value=value;setLayer(value);},
    showAll(){fitAll();}};
},pointPicker({container,bundle,onPick,onOutside}){
  const map=L.map(container,{minZoom:10,maxZoom:18,zoomSnap:.25,scrollWheelZoom:false});
  map.attributionControl.setPrefix(false);
  scoutingBasemap(map,bundle);
  const outline=L.geoJSON(null,{style:{color:'#ffe18a',weight:3,fillColor:'#ffe18a',fillOpacity:.12},
    bubblingMouseEvents:false}).addTo(map);
  const pin=L.circleMarker([0,0],{radius:7,color:'#fff',weight:2,fillColor:'#176b47',fillOpacity:1,
    interactive:false});
  outline.on('click',event=>onPick(event.latlng));
  map.on('click',onOutside);
  return {
    showArea(id){
      outline.clearLayers();
      outline.addData(bundle.geometry.features.find(feature=>feature.properties.id===id));
      map.invalidateSize({pan:false});
      map.fitBounds(outline.getBounds(),{padding:[24,24],maxZoom:17,animate:false});
    },
    showPoint(latitude,longitude){pin.setLatLng([latitude,longitude]).addTo(map);}
  };
}};
