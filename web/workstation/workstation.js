/* The server's CSP forbids inline style attributes (style-src 'self'). Markup carries per-element styles as
   data-style, and this applies them through the CSSOM, which the policy allows. */
(function(){
  const apply=el=>{if(el.nodeType!==1)return;if(el.hasAttribute('data-style'))el.style.cssText=el.getAttribute('data-style');el.querySelectorAll('[data-style]').forEach(x=>{x.style.cssText=x.getAttribute('data-style');});};
  new MutationObserver(list=>list.forEach(m=>m.addedNodes.forEach(apply))).observe(document.documentElement,{childList:true,subtree:true});
  apply(document.documentElement);
})();
(function(){
'use strict';
/* ═════════ Dates: day index 0 = 1 April 2024 ═════════ */
const Y=2024, D0=Date.UTC(2024,3,1), DAY=864e5;
const MON=['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря'];
const MON_S=['янв','фев','мар','апр','мая','июн','июл','авг','сен','окт','ноя','дек'];
const dt=i=>new Date(D0+i*DAY);
const ix=(m,d)=>Math.round((Date.UTC(Y,m-1,d)-D0)/DAY);
const ixISO=s=>Math.round((Date.parse(s+'T00:00:00Z')-D0)/DAY);
const iso=i=>dt(i).toISOString().slice(0,10);
const fmt=i=>{const d=dt(i);return d.getUTCDate()+' '+MON[d.getUTCMonth()]+(d.getUTCFullYear()!==Y?' '+d.getUTCFullYear():'');};
const fmtS=i=>{const d=dt(i);return d.getUTCDate()+' '+MON_S[d.getUTCMonth()]+(d.getUTCFullYear()!==Y?' '+d.getUTCFullYear():'');};
const SEASON=61;          /* 1 Apr … 31 May */
const DATA_END=ix(5,20);  /* the example's ERA5 ends on 20 May — the prototype's "today" */
const MIN_D=ix(4,10);     /* first date with a complete 10-day window */
const TODAY=DATA_END;
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=(x,p=2)=>x.toFixed(p).replace('.',',');
const cap=s=>s.charAt(0).toUpperCase()+s.slice(1);
const plural=(n,a,b,c)=>{const m=n%10,h=n%100;return m===1&&h!==11?a:m>=2&&m<=4&&(h<10||h>=20)?b:c;};
const days=n=>n+' '+plural(n,'день','дня','дней');
function rel(i,ref){const d=i-ref;return d===0?'сегодня':d===1?'завтра':d===-1?'вчера':d>0?'через '+days(d):days(-d)+' назад';}

/* ═════════ Rules: config/field_risk_rules.json (draft-2026-11-08) ═════════ */
const RULES={crops:['озимая пшеница','озимый ячмень'],phases:['кущение','выход в трубку','флаговый лист','колошение','цветение','налив'],
  lookback:10,partial:.5,protDays:14,washMm:10,endingSoon:3,focusMemory:365,
  diseases:[{id:'rust',name:'Бурая ржавчина',gen:'бурой ржавчины',short:'ржавчина',wet:6,tmin:15,tmax:25,from:'выход в трубку',after:10},
            {id:'sept',name:'Септориоз листьев',gen:'септориоза',short:'септориоз',wet:12,tmin:10,tmax:25,from:'выход в трубку',after:14}]};
const LATENT=7; /* assumed latent period before symptoms (agronomist's estimate, not in the rules file) */
const phIdx=p=>RULES.phases.indexOf(p);

/* ═════════ Example farm ═════════
   ERA5 is one 0.25° cell for the whole farm, so weather is shared; fields differ by phase, protection and the node on R07.
   wet = wet hours in the day (RH ≥ 90 % or rain ≥ 0.2 mm), t = mean °C over those hours, rain = mm, rh = hour of the rain. */
function seeded(s){return ()=>{s=(s*16807)%2147483647;return (s-1)/2147483646;};}
const rnd=seeded(4207);
const WX=[];
for(let i=0;i<=DATA_END;i++){const base=Math.floor(rnd()*3);WX.push({wet:base,t:Math.round(9+i*.17+rnd()*2),rain:0,rh:0});}
const setW=(m,d,o)=>Object.assign(WX[ix(m,d)],o);
setW(4,8,{wet:7,t:11,rain:3,rh:4});setW(4,9,{wet:5,t:11});
setW(4,24,{wet:8,t:16,rain:2,rh:2});setW(4,25,{wet:3,t:15});
setW(5,2,{wet:13,t:14,rain:6,rh:22});setW(5,3,{wet:4,t:14});
setW(5,4,{wet:6,t:13,rain:14,rh:3});
setW(5,9,{wet:4,t:17});setW(5,10,{wet:2,t:16});
setW(5,16,{wet:9,t:18,rain:4,rh:5});setW(5,17,{wet:2,t:18});setW(5,18,{wet:3,t:13});setW(5,19,{wet:1,t:19});setW(5,20,{wet:0,t:19});
/* the node on R07 (a hollow by the shelter belt): leaf wetness from 6 May, offline on 12 May */
const NODE={fid:'R07',from:ix(5,6),gaps:[ix(5,12)]};
const SENSOR={};
for(let i=NODE.from;i<=DATA_END;i++){if(NODE.gaps.includes(i))continue;const w=WX[i];SENSOR[i]={wet:Math.min(18,w.wet+(w.wet>=4?4:1)),t:w.t-1,rain:w.rain,rh:w.rh};}
SENSOR[ix(5,16)]={wet:13,t:16,rain:4,rh:5};
const SAT=[[4,3,80],[4,8,6],[4,13,35],[4,18,4],[4,23,100],[4,28,12],[5,3,0],[5,8,55],[5,13,9],[5,18,3]].map(([m,d,c])=>({i:ix(m,d),cloud:c}));
const NEXT_PASS=ix(5,23);
/* Example 3-day forecast issued on 20 May (a real build would take GFS/ICON; the repo already studies GEFS).
   wet/t = expected wet hours and their °C for the day; per = 4-hour blocks [from,to,wind m/s,rain mm,°C]. */
const FC=[
 {i:ix(5,21),wet:8,t:16,per:[[0,4,1.5,0,12],[4,8,1.8,0,11],[8,12,2.6,0,17],[12,16,5.2,0,22],[16,20,4.1,0,20],[20,24,1.9,0,15]]},
 {i:ix(5,22),wet:3,t:18,per:[[0,4,2.2,0,14],[4,8,2.8,0,13],[8,12,3.4,0,19],[12,16,6.5,4,21],[16,20,5.8,2,18],[20,24,3.0,0,16]]},
 {i:ix(5,23),wet:11,t:15,per:[[0,4,1.2,0,13],[4,8,1.6,0,12],[8,12,3.1,0,18],[12,16,4.6,0,23],[16,20,3.9,1,19],[20,24,2.1,0,15]]}
];
const SPRAY={wind:4,tmin:8,tmax:25};
function sprayBlocks(){const out=[];FC.forEach((d,k)=>d.per.forEach((p,j)=>{const nx=j<5?d.per[j+1]:(FC[k+1]?FC[k+1].per[0]:null);
  const why=p[2]>SPRAY.wind?'ветер '+num(p[2],1)+' м/с':p[3]>0?'дождь '+p[3]+' мм':nx&&nx[3]>0?'дождь следом':p[4]<SPRAY.tmin||p[4]>SPRAY.tmax?p[4]+' °C':'';
  out.push({i:d.i,from:p[0],to:p[1],ok:!why,why,wind:p[2],t:p[4]});}));return out;}
function sprayWindows(){const w=[];sprayBlocks().forEach(b=>{if(!b.ok)return;const l=w[w.length-1];if(l&&l.i===b.i&&l.to===b.from)l.to=b.to;else w.push({i:b.i,from:b.from,to:b.to});});return w;}
const winText=w=>w?fmtS(w.i)+', '+String(w.from).padStart(2,'0')+'–'+String(w.to).padStart(2,'0'):'нет в ближайшие 3 дня';
/* Example field passport: predecessor and sowing date (sowing dates of R01–R23 follow farm_records_example.csv) */
const PASSPORT={R01:{pred:'подсолнечник',sown:'2023-09-28'},R02:{pred:'горох',sown:'2023-10-08'},R07:{pred:'подсолнечник',sown:'2023-10-05'},R09:{pred:'соя',sown:'2023-10-25'},R10:{pred:'озимая пшеница',sown:'2024-04-28'},R11:{pred:'озимая пшеница',sown:'2023-10-12'},R23:{pred:'озимая пшеница',sown:'2023-10-20'}};
(function(){const r=seeded(57),preds=['подсолнечник','кукуруза на зерно','горох','соя','озимая пшеница','подсолнечник'];for(let n=1;n<=27;n++){const id='R'+String(n).padStart(2,'0');if(!PASSPORT[id])PASSPORT[id]={pred:preds[Math.floor(r()*preds.length)],sown:'2023-10-'+String(1+Math.floor(r()*24)).padStart(2,'0')};}})();
/* Harmful objects: the first three are the most frequent for the crop and come first in the list */
const OBJECTS={'озимая пшеница':['Септориоз листьев','Бурая ржавчина','Мучнистая роса','Жёлтая ржавчина','Пиренофороз','Клоп вредная черепашка','Пьявица','Злаковые тли','Другое'],
  'озимый ячмень':['Сетчатая пятнистость','Карликовая ржавчина','Мучнистая роса','Ринхоспориоз','Тёмно-бурая пятнистость','Пьявица','Злаковые тли','Другое']};
const PRODUCTS=['Пропиконазол + ципроконазол','Тебуконазол','Азоксистробин + ципроконазол','Протиоконазол + тебуконазол'];
const satKind=c=>c<=20?'clear':c<=60?'part':'cloud';

/* Fields: R01, R02, R07, R09, R10, R11, R23 follow config/farm_records_example.csv; the rest are illustrative. */
const P=(m,d)=>ix(m,d);
const FIELDS=[
 {id:'R01',crop:'озимый ячмень',ha:88,phases:[['колошение',P(5,1)]],treat:[P(5,6)]},
 {id:'R02',crop:'озимая пшеница',ha:71,phases:[['флаговый лист',P(5,6)]],treat:[P(5,9)]},
 {id:'R07',crop:'озимая пшеница',ha:96,phases:[['выход в трубку',P(4,18)],['колошение',P(5,12)]],treat:[P(5,2)],sensor:true},
 {id:'R09',crop:'озимая пшеница',ha:77,phases:[['кущение',P(4,2)],['выход в трубку',P(5,17)]],treat:[]},
 {id:'R10',crop:'кукуруза',ha:58,phases:[],treat:[]},
 {id:'R11',crop:'озимая пшеница',ha:84,phases:[['выход в трубку',P(4,22)],['флаговый лист',P(5,8)]],treat:[]},
 {id:'R23',crop:'озимая пшеница',ha:64,phases:[['кущение',P(4,5)],['выход в трубку',P(5,18)]],treat:[]}
];
const FOCI=[{fid:'R23',i:ixISO('2023-06-02'),disease:'Септориоз листьев',src:'учёт'}];
(function genRest(){
  const r=seeded(91);const have=new Set(FIELDS.map(f=>f.id));
  for(let n=1;n<=27;n++){
    const id='R'+String(n).padStart(2,'0');if(have.has(id))continue;
    const barley=['R04','R13','R19','R25'].includes(id);
    const tr=P(4,15)+Math.floor(r()*8), fl=tr+16+Math.floor(r()*4);
    const ph=[['кущение',P(4,1)],['выход в трубку',tr],['флаговый лист',fl]];
    if(barley)ph.push(['колошение',fl+6]);
    const t1=P(4,25)+Math.floor(r()*4), t2=P(5,11)+Math.floor(r()*4);
    FIELDS.push({id,crop:barley?'озимый ячмень':'озимая пшеница',ha:52+Math.round(r()*50),phases:ph,treat:id==='R14'?[P(5,3),P(5,12)]:[t1,t2]});
  }
  FIELDS.sort((a,b)=>a.id.localeCompare(b.id));
})();
const NDVI={};
FIELDS.forEach(f=>{
  const r=seeded(parseInt(f.id.slice(1),10)*97),barley=f.crop==='озимый ячмень',corn=f.crop==='кукуруза';
  NDVI[f.id]={};
  SAT.forEach(s=>{if(s.cloud>60)return;let v;
    if(corn){if(s.i<ix(5,8))return;v=.14+(s.i-ix(5,8))*.006;}
    else v=.48+Math.min(1,s.i/50*1.25)*.27-(barley?Math.max(0,s.i-ix(5,8))*.004:0)+(r()-.5)*.02;
    if(f.id==='R11'&&s.i>=ix(5,13))v-=s.i===ix(5,18)?.07:.01;
    NDVI[f.id][s.i]=+v.toFixed(2);});
});
/* Real farm map: field outlines from web/scouting/assets/public-context.json (approximate, traced from a screenshot),
   projected onto a crop of the Esri World Imagery basemap (Web Mercator). ERA5 sector = the 0.25° cell around the grid node. */
const MAPGEO={"w":969,"h":1500,"fields":{"R01":{"pts":[[434.4,116.1],[645.8,232.7],[644.6,295.8],[602.1,257.0],[574.1,257.0],[552.3,266.7],[528.0,276.4],[467.2,240.0],[502.5,175.6],[435.7,147.7],[434.4,116.1]],"c":[543.0,217.3],"ll":[45.10766,37.95567],"ha":236.1},"R02":{"pts":[[435.6,309.2],[432.0,295.8],[455.1,246.0],[470.9,246.0],[517.0,275.2],[548.6,265.5],[565.6,264.3],[571.7,258.2],[598.4,260.6],[642.2,298.3],[656.7,301.9],[683.5,316.5],[716.3,314.1],[751.5,350.5],[752.7,405.2],[699.2,405.2],[698.0,480.5],[643.4,480.5],[642.2,420.9],[546.2,420.9],[546.2,359.0],[470.9,359.0],[470.9,412.4],[435.6,412.4],[435.6,309.2]],"c":[590.7,351.3],"ll":[45.09205,37.96354],"ha":689.6},"R03":{"pts":[[470.3,360.7],[471.2,388.9],[545.1,386.0],[544.1,357.8],[475.1,357.8],[470.3,360.7]],"c":[507.2,372.7],"ll":[45.08956,37.94975],"ha":36.8},"R04":{"pts":[[755.9,363.6],[755.9,404.4],[832.7,403.5],[832.7,397.6],[829.8,397.6],[817.1,383.1],[795.8,381.1],[782.2,371.4],[768.6,374.3],[755.9,363.6]],"c":[787.0,390.2],"ll":[45.08751,37.99596],"ha":32.0},"R05":{"pts":[[470.9,390.6],[546.2,390.6],[546.2,420.9],[470.9,420.9],[470.9,390.6]],"c":[508.5,405.8],"ll":[45.0857,37.94997],"ha":38.5},"R06":{"pts":[[882.2,398.6],[857.0,400.6],[858.9,476.3],[855.0,480.2],[830.7,482.2],[830.7,554.1],[834.6,557.0],[880.3,556.0],[885.1,552.1],[885.2,439.4],[882.2,398.6]],"c":[862.5,491.7],"ll":[45.07569,38.00842],"ha":105.3},"R07":{"pts":[[699.2,406.4],[856.0,406.4],[856.0,478.0],[830.4,478.0],[830.4,481.7],[699.2,481.7],[699.2,406.4]],"c":[777.1,443.7],"ll":[45.08127,37.99432],"ha":197.1},"R08":{"pts":[[470.9,420.9],[639.7,420.9],[639.7,484.1],[470.9,484.1],[470.9,420.9]],"c":[555.3,452.5],"ll":[45.08025,37.95769],"ha":179.5},"R09":{"pts":[[643.4,481.7],[698.0,481.7],[698.0,555.8],[644.6,555.8],[643.4,481.7]],"c":[671.0,518.6],"ll":[45.07255,37.9768],"ha":67.4},"R10":{"pts":[[699.2,482.9],[830.4,482.9],[830.4,554.6],[699.2,554.6],[699.2,482.9]],"c":[764.8,518.7],"ll":[45.07253,37.99229],"ha":158.3},"R11":{"pts":[[470.9,487.7],[553.5,487.7],[553.5,530.3],[487.9,530.2],[487.9,564.3],[470.9,564.3],[470.9,487.7]],"c":[507.5,514.4],"ll":[45.07303,37.94981],"ha":68.9},"R12":{"pts":[[554.7,487.7],[572.9,487.7],[572.9,564.3],[554.7,564.3],[554.7,487.7]],"c":[563.8,526.0],"ll":[45.07168,37.9591],"ha":23.5},"R13":{"pts":[[575.3,487.7],[642.1,487.7],[642.1,530.3],[575.3,530.3],[575.3,487.7]],"c":[608.7,509.0],"ll":[45.07366,37.96652],"ha":47.8},"R14":{"pts":[[575.2,532.7],[575.2,616.2],[578.1,619.2],[643.2,617.2],[642.2,531.7],[575.2,532.7]],"c":[608.9,575.3],"ll":[45.06593,37.96655],"ha":97.8},"R15":{"pts":[[554.8,533.7],[490.7,532.7],[489.7,563.8],[491.6,567.7],[553.8,564.7],[554.8,533.7]],"c":[521.6,549.7],"ll":[45.06892,37.95213],"ha":35.7},"R16":{"pts":[[414.9,625.9],[394.5,616.2],[377.9,612.3],[373.1,614.3],[372.1,676.5],[415.8,639.6],[419.7,631.8],[414.9,625.9]],"c":[390.0,639.0],"ll":[45.0585,37.93041],"ha":29.6},"R17":{"pts":[[371.1,675.5],[417.8,638.6],[462.5,684.3],[382.8,753.2],[419.7,797.0],[405.1,798.9],[383.8,795.0],[364.3,806.7],[349.8,797.0],[367.2,772.7],[371.1,675.5]],"c":[400.4,715.2],"ll":[45.04961,37.93211],"ha":131.7},"R18":{"pts":[[506.2,642.5],[540.2,662.9],[532.4,671.6],[516.9,660.0],[506.2,642.5]],"c":[524.4,658.8],"ll":[45.0562,37.95259],"ha":5.1},"R19":{"pts":[[382.8,753.2],[461.5,685.2],[502.3,731.9],[419.7,797.0],[382.8,753.2]],"c":[442.1,741.4],"ll":[45.04655,37.93901],"ha":105.0},"R20":{"pts":[[506.2,735.7],[492.6,742.6],[429.4,794.0],[441.1,813.5],[465.4,840.7],[491.6,833.9],[508.1,825.1],[536.3,821.3],[552.8,811.5],[559.6,799.9],[556.7,794.1],[542.1,784.3],[540.2,778.5],[534.4,778.5],[532.4,775.6],[517.8,778.5],[512.0,773.6],[511.0,757.1],[514.9,748.4],[506.2,735.7]],"c":[492.0,794.7],"ll":[45.04034,37.94724],"ha":120.8},"R21":{"pts":[[335.2,850.4],[327.4,853.3],[325.5,861.1],[313.8,873.7],[306.0,868.9],[297.3,868.9],[285.6,883.4],[289.5,893.1],[315.7,918.4],[315.7,923.3],[309.9,928.1],[314.8,938.8],[305.0,956.3],[326.4,976.7],[340.0,967.0],[385.7,916.5],[385.7,912.6],[377.0,904.8],[367.2,889.3],[367.2,880.5],[335.2,850.4]],"c":[335.6,910.5],"ll":[45.02682,37.92142],"ha":111.9},"R22":{"pts":[[450.8,947.6],[447.9,947.6],[432.3,965.1],[422.6,990.3],[382.8,1030.1],[384.7,1037.9],[410.9,1062.2],[417.8,1060.3],[431.4,1043.8],[434.3,1036.0],[433.3,1027.2],[437.2,1026.3],[445.9,996.1],[453.7,982.5],[450.8,947.6]],"c":[422.4,1012.2],"ll":[45.01495,37.93576],"ha":58.2},"R23":{"pts":[[223.4,1072.9],[229.3,1039.9],[252.6,1007.8],[303.1,978.6],[323.5,979.6],[367.2,1033.1],[364.3,1034.0],[364.3,1043.8],[360.4,1049.6],[332.2,1153.5],[330.3,1158.4],[273.9,1114.7],[223.4,1072.9]],"c":[299.2,1058.9],"ll":[45.0095,37.91541],"ha":261.3},"R24":{"pts":[[367.2,1033.1],[364.3,1034.0],[364.3,1043.8],[360.4,1049.6],[332.2,1153.5],[352.6,1174.9],[365.3,1169.1],[381.8,1153.5],[366.2,1133.1],[366.2,1123.4],[374.0,1122.5],[385.7,1138.0],[397.3,1139.9],[420.7,1122.5],[396.4,1077.8],[367.2,1033.1]],"c":[371.9,1110.4],"ll":[45.00349,37.92741],"ha":100.0},"R25":{"pts":[[225.4,1080.7],[217.6,1097.2],[211.8,1103.0],[225.4,1123.4],[228.3,1148.7],[239.0,1157.4],[230.2,1163.3],[231.2,1173.0],[241.9,1176.9],[243.8,1183.7],[267.1,1214.8],[284.6,1230.3],[307.0,1240.0],[329.3,1234.2],[347.8,1211.8],[308.9,1171.0],[303.1,1171.0],[302.1,1191.4],[297.3,1194.4],[291.4,1189.5],[291.4,1171.0],[274.9,1169.1],[275.9,1162.3],[284.6,1150.6],[267.1,1133.1],[267.1,1128.3],[273.0,1120.5],[231.2,1082.6],[225.4,1080.7]],"c":[273.6,1170.9],"ll":[44.99643,37.91117],"ha":136.1},"R26":{"pts":[[274.9,1120.5],[332.2,1158.4],[370.1,1191.4],[351.7,1210.9],[268.1,1135.1],[274.9,1120.5]],"c":[320.7,1166.7],"ll":[44.99692,37.91895],"ha":45.2},"R27":{"pts":[[416.8,1293.5],[409.0,1295.4],[383.7,1321.6],[372.1,1345.9],[374.0,1353.7],[410.9,1390.6],[433.3,1363.4],[443.0,1358.6],[458.5,1342.1],[453.7,1330.4],[416.8,1293.5]],"c":[413.4,1340.4],"ll":[44.97663,37.93426],"ha":79.0}},"sector":{"node":[811.5,1140.3],"west":54.5,"north":68.6,"grid":[45.0,38.0]},"kmpx":0.01299,"attribution":"Esri, Vantor, Earthstar Geographics, GIS User Community; дата съёмки мозаики неизвестна"};
const GEO={};Object.keys(MAPGEO.fields).forEach(id=>{const g=MAPGEO.fields[id];GEO[id]={pts:g.pts,cx:g.c[0],cy:g.c[1]};});
FIELDS.forEach(f=>{const g=MAPGEO.fields[f.id];if(g)f.ha=Math.round(g.ha);});
const ptOf=id=>({x:GEO[id].cx,y:GEO[id].cy});
const llOf=id=>MAPGEO.fields[id].ll;
function hav(a,b){const R=6371,r=Math.PI/180,dl=(b[0]-a[0])*r,dg=(b[1]-a[1])*r;const h=Math.sin(dl/2)**2+Math.cos(a[0]*r)*Math.cos(b[0]*r)*Math.sin(dg/2)**2;return 2*R*Math.asin(Math.sqrt(h));}
const dist=(a,b)=>hav(llOf(a),llOf(b))*1.3; /* ×1.3: field roads instead of a straight line */

/* ═════════ State ═════════ */
const KEY='agropulse:desk:v3';
let saved={};try{saved=JSON.parse(localStorage.getItem(KEY)||'{}')||{};}catch(e){saved={};}
const S={
  view:'today',D:TODAY,sel:'R11',filter:'all',tab:'why',mapLayer:'status',
  collapsed:Object.assign({normal:true,none:true},saved.collapsed||{}),
  marks:Array.isArray(saved.marks)?saved.marks:[{fid:'R02',i:ix(5,17),result:'clean',tier:'флаговый и F-1',text:'Флаговый лист и F-1 чистые.'}],
  trip:Array.isArray(saved.trip)?saved.trip:['R11','R07'],
  done:Array.isArray(saved.done)?saved.done:[],
  overrides:saved.overrides&&typeof saved.overrides==='object'?saved.overrides:{},
  autos:Object.assign({brief:true,forecast:true,protect:true,unmarked:true,snapshot:false,trip:false},saved.autos||{}),
  minPerField:saved.minPerField||45,
  tasks:Array.isArray(saved.tasks)?saved.tasks:[],tlCompact:!!saved.tlCompact,
  draft:{},imported:null,jf:'all',hlDay:null,playing:null,mapZoom:'farm'
};
function persist(){try{localStorage.setItem(KEY,JSON.stringify({marks:S.marks,trip:S.trip,done:S.done,overrides:S.overrides,autos:S.autos,collapsed:S.collapsed,minPerField:S.minPerField,tasks:S.tasks,tlCompact:S.tlCompact}));}catch(e){}}
const fieldById=id=>FIELDS.find(f=>f.id===id);
const originalFields=new Map(FIELDS.map(f=>[f.id,{phases:f.phases.slice(),treat:f.treat.slice()}]));
const originalFoci=FOCI.slice();
function applyOverrides(){
  for(const f of FIELDS){const original=originalFields.get(f.id);f.phases=original.phases.slice();f.treat=original.treat.slice();}
  FOCI.splice(0,FOCI.length,...originalFoci);
  Object.keys(S.overrides).forEach(id=>{const f=fieldById(id),o=S.overrides[id];if(!f)return;
  (o.phases||[]).forEach(([p,i])=>{if(!f.phases.some(x=>x[0]===p&&x[1]===i))f.phases.push([p,i]);});f.phases.sort((a,b)=>a[1]-b[1]);
  (o.treat||[]).forEach(i=>{if(!f.treat.includes(i))f.treat.push(i);});f.treat.sort((a,b)=>a-b);
  (o.foci||[]).forEach(x=>{if(!FOCI.some(y=>y.fid===id&&y.i===x.i))FOCI.push(Object.assign({fid:id,src:'учёт'},x));});});
}
applyOverrides();
document.getElementById('farm-sub').textContent='27 полей · '+FIELDS.reduce((a,f)=>a+f.ha,0).toLocaleString('ru-RU')+' га';

/* ═════════ Risk engine (same order as agro_checker/field_risk.py) ═════════ */
function wxOf(f,i){if(f.sensor&&SENSOR[i])return Object.assign({src:'node'},SENSOR[i]);return Object.assign({src:'era5'},WX[i]||{wet:0,t:0,rain:0,rh:0});}
function phaseAt(f,i){let p=null;f.phases.forEach(([n,d])=>{if(d<=i)p=n;});return p;}
function protections(f,D){
  return f.treat.filter(t=>t<=D).map(t=>{let mm=0,known=true;
    if(WX[t]&&WX[t].rh>=12)mm+=WX[t].rain;
    if(t+1>DATA_END)known=false;else if(WX[t+1].rh<12)mm+=WX[t+1].rain;
    return {t,until:t+RULES.protDays-1,mm,known,voided:known&&mm>=RULES.washMm};});
}
let VER=0;const CACHE=new Map();
function evaluate(f,D){
  const key=f.id+'|'+D+'|'+VER;if(CACHE.has(key))return CACHE.get(key);
  const out={status:'normal',due:null,from:null,kind:null,reasons:[],full:[],part:[],covered:[],unknown:[],prots:[],gaps:{},warns:[]};
  if(!RULES.crops.includes(f.crop)){out.status='none';out.reasons=[{t:'Для культуры «'+f.crop+'» правил риска нет — поле не участвует в очереди.',c:'none'}];CACHE.set(key,out);return out;}
  const prots=protections(f,D);out.prots=prots;
  const coverOf=i=>prots.filter(p=>!p.voided&&p.t<=i&&i<=p.until).sort((a,b)=>b.until-a.until)[0];
  for(let i=Math.max(0,D-RULES.lookback+1);i<=D;i++){
    const w=wxOf(f,i),ph=phaseAt(f,i);
    RULES.diseases.forEach(dz=>{
      const inR=w.t>=dz.tmin&&w.t<=dz.tmax,isF=inR&&w.wet>=dz.wet,isP=!isF&&inR&&w.wet>=Math.ceil(dz.wet*RULES.partial);
      const sus=ph&&phIdx(ph)>=phIdx(dz.from),c=coverOf(i);
      if(sus&&!c&&inR){const g=out.gaps[dz.id];if(!g||w.wet>g.h)out.gaps[dz.id]={h:w.wet,i,t:w.t};}
      if(!(isF||isP))return;
      const e={i,dz,h:w.wet,t:w.t,src:w.src,full:isF,cover:c};
      if(!ph){out.unknown.push(e);return;}
      if(!sus)return;
      if(c){if(isF)out.covered.push(e);return;}
      (isF?out.full:out.part).push(e);
    });
  }
  const unit=e=>e.src==='node'?'мокрого листа (узел)':'влажности';
  const lastProt=prots[prots.length-1];
  const protLine=()=>{
    if(!prots.length)return {t:'Обработок фунгицидом в учёте нет.',c:'none'};
    const p=lastProt;
    if(p.voided)return {t:'<b>Обработку '+fmt(p.t)+' смыл дождь</b>: '+num(p.mm,0)+' мм за сутки после неё (порог 10 мм).',c:'risk',day:p.t};
    if(p.until>=D)return {t:'Фунгицид '+fmt(p.t)+', защита до '+fmt(p.until)+(p.known?'.':' — смыв ещё неизвестен: нет погоды за следующие сутки.'),c:'normal',day:p.t};
    return {t:'Фунгицид '+fmt(p.t)+', защита закончилась '+fmt(p.until)+'.',c:'none',day:p.t};
  };
  if(out.full.length){
    out.status='risk';out.kind='event';
    const byD={};out.full.forEach(e=>{if(!byD[e.dz.id]||e.i<byD[e.dz.id].i)byD[e.dz.id]=e;});
    const firsts=Object.values(byD);
    out.due=Math.min.apply(null,firsts.map(e=>e.i+e.dz.after));
    out.from=Math.min.apply(null,firsts.map(e=>e.i))+LATENT;
    const seen=new Set();
    out.full.slice().sort((a,b)=>b.h-a.h||b.i-a.i).forEach(e=>{if(seen.has(e.i))return;seen.add(e.i);const n=out.full.filter(x=>x.i===e.i).map(x=>x.dz.gen).join(' и ');out.reasons.push({t:'<b>'+fmt(e.i)+': '+e.h+' ч '+unit(e)+' при '+e.t+' °C</b> — условия для '+n+'.',c:'risk',day:e.i});});
    out.reasons.push({t:'Фаза «'+phaseAt(f,D)+'» — восприимчива с выхода в трубку.',c:'none'});
    out.reasons.push(protLine());
  }else{
    const renewed=u=>prots.some(p=>!p.voided&&p.t<=D&&p.until>u); /* a newer treatment already extends the protection */
    const ending=out.covered.filter(e=>e.cover.until-D>=0&&e.cover.until-D<=RULES.endingSoon&&!renewed(e.cover.until));
    if(ending.length){
      const e=ending[0];out.status='check';out.kind='ending';out.due=e.cover.until;
      out.reasons.push({t:'<b>Защита заканчивается '+fmt(e.cover.until)+'</b>, а '+fmt(e.i)+' под фунгицидом '+fmt(e.cover.t)+' были условия для '+e.dz.gen+' ('+e.h+' ч при '+e.t+' °C).',c:'check',day:e.i});
      out.reasons.push({t:'После '+fmt(e.cover.until)+' поле открыто: следующая влажная ночь даст «Риск».',c:'none'});
    }else if(out.part.length){
      const byD={};out.part.forEach(e=>{if(!byD[e.dz.id]||e.i<byD[e.dz.id].i)byD[e.dz.id]=e;});
      const firsts=Object.values(byD);
      out.status='check';out.kind='partial';out.due=Math.min.apply(null,firsts.map(e=>e.i+e.dz.after));out.from=Math.min.apply(null,firsts.map(e=>e.i))+LATENT;
      firsts.forEach(e=>out.reasons.push({t:'<b>'+fmt(e.i)+': '+e.h+' ч '+unit(e)+' при '+e.t+' °C</b> — половина порога '+e.dz.gen+' ('+e.dz.wet+' ч).',c:'check',day:e.i}));
      out.reasons.push(protLine());
    }else if(out.unknown.length){
      const e=out.unknown[0];out.status='check';out.kind='unknown';
      out.reasons.push({t:'<b>Фаза на '+fmt(e.i)+' не указана в учёте</b>, а в этот день были условия для '+e.dz.gen+'. Уточните фазу — без неё правило не решает.',c:'check',day:e.i});
    }
  }
  const foci=FOCI.filter(x=>x.fid===f.id&&x.i<=D&&D-x.i<=RULES.focusMemory).concat(S.marks.filter(m=>m.fid===f.id&&m.result==='focus'&&m.i<=D&&D-m.i<=RULES.focusMemory).map(m=>({i:m.i,disease:m.disease,src:'осмотр'})));
  if(foci.length){
    const x=foci.sort((a,b)=>b.i-a.i)[0];
    const line={t:'<b>Очаг «'+x.disease+'» '+fmt(x.i)+'</b> '+(x.src==='учёт'?'в учёте хозяйства':'по отметке осмотра')+' — поле не опускается ниже «Проверить» '+RULES.focusMemory+' дней.',c:'check',day:x.i>=0?x.i:null};
    if(out.status==='normal'){out.status='check';out.kind='focus';out.reasons.unshift(line);}else out.reasons.push(line);
  }
  if(out.status==='normal'){
    const p=prots.find(p=>!p.voided&&p.t<=D&&D<=p.until);
    if(p)out.reasons.push({t:'<b>Защита действует до '+fmt(p.until)+'</b> (фунгицид '+fmt(p.t)+').',c:'normal',day:p.t});
    const ph=phaseAt(f,D);
    if(!ph||phIdx(ph)<1)out.reasons.push({t:'<b>Фаза «'+(ph||'не указана')+'»</b> — до выхода в трубку поле не восприимчиво.',c:'normal'});
    else if(!p)out.reasons.push({t:'<b>За 10 суток влажных часов меньше половины порогов</b> при подходящей температуре.',c:'normal'});
    if(!p&&lastProt)out.reasons.push(protLine());
  }
  if(f.crop==='озимая пшеница'&&PASSPORT[f.id]&&/пшениц/.test(PASSPORT[f.id].pred))out.warns.push({k:'warn',t:'Пшеница по пшенице: возбудитель септориоза зимует на стерне, риск выше. Правила это пока не учитывают.'});
  if(f.crop==='озимый ячмень')out.warns.push({k:'info',t:'Для ячменя правила используют пороги пшеничных болезней. Карликовая ржавчина, сетчатая пятнистость и ринхоспориоз не учитываются.'});
  const lastT=prots.filter(p=>!p.voided).slice(-1)[0],flag=f.phases.find(p=>p[0]==='флаговый лист');
  if(lastT&&flag&&flag[1]>lastT.t&&flag[1]<=D&&D<=lastT.until)out.warns.push({k:'warn',t:'Флаговый лист появился '+fmt(flag[1])+', после обработки '+fmt(lastT.t)+'. Правило считает его защищённым, но новый лист препарат не покрыл.'});
  if(lastProt&&!lastProt.known)out.warns.push({k:'warn',t:'Смыв обработки '+fmt(lastProt.t)+' неизвестен: погоды за следующие сутки ещё нет.'});
  if(f.sensor){const miss=NODE.gaps.filter(g=>g>D-RULES.lookback&&g<=D);if(miss.length)out.warns.push({k:'warn',t:'Узел не передавал данные '+miss.map(fmt).join(', ')+' — эти сутки посчитаны по ERA5.'});}
  const ls=SAT.filter(s=>s.i<=D);if(ls.length&&ls[ls.length-1].cloud>60){const lc=ls.filter(s=>s.cloud<=20).slice(-1)[0];out.warns.push({k:'info',t:'Последний снимок '+fmt(ls[ls.length-1].i)+' закрыт облаками ('+ls[ls.length-1].cloud+' %). Чистый — '+(lc?fmt(lc.i):'нет')+'.'});}
  CACHE.set(key,out);return out;
}
/* forecast: would the field get infection conditions in the next 3 days without protection? */
function plannedFor(id){return S.tasks.filter(t=>t.fid===id&&t.status==='plan').sort((a,b)=>a.plan-b.plan);}
function forecastRisk(f){
  if(!RULES.crops.includes(f.crop))return null;
  const prots=protections(f,TODAY),plans=plannedFor(f.id);
  for(const d of FC){const ph=phaseAt(f,d.i);if(!ph)continue;
    const cov=prots.find(p=>!p.voided&&p.t<=d.i&&d.i<=p.until);if(cov)continue;
    const dzs=RULES.diseases.filter(dz=>phIdx(ph)>=phIdx(dz.from)&&d.t>=dz.tmin&&d.t<=dz.tmax&&d.wet>=dz.wet);
    if(dzs.length)return {i:d.i,h:d.wet,t:d.t,dzs,plan:plans.find(p=>p.plan<=d.i&&d.i<=p.plan+RULES.protDays-1)||null};}
  return null;
}
const ORDER={risk:0,check:1,normal:2,none:3};
const LABEL={risk:'Риск',check:'Проверить',normal:'Норма',none:'Нет правил'};
const ev=(id,D)=>evaluate(fieldById(id),D===undefined?S.D:D);
const st=(id,D)=>ev(id,D).status;
function sortedIds(D){return FIELDS.map(f=>f.id).sort((a,b)=>{const A=ev(a,D),B=ev(b,D);return ORDER[A.status]-ORDER[B.status]||((A.from||A.due||9e9)-(B.from||B.due||9e9))||a.localeCompare(b);});}
const CC=new Map();
function counts(D){const k=D+'|'+VER;if(CC.has(k))return CC.get(k);const c={risk:0,check:0,normal:0,none:0};FIELDS.forEach(f=>c[st(f.id,D)]++);CC.set(k,c);return c;}
function bump(){VER++;CACHE.clear();CC.clear();}
function whenText(e,D){
  if(e.status==='none')return {lbl:'Срок',main:'не рассчитан',sub:''};
  if(e.kind==='ending')return {lbl:'Защита до',main:fmt(e.due),sub:rel(e.due,D)};
  if(e.kind==='focus')return {lbl:'Срок',main:'при объезде',sub:'прошлый очаг'};
  if(e.kind==='unknown')return {lbl:'Сначала',main:'уточнить фазу',sub:'в учёте'};
  if(e.due!=null){
    if(D<e.from)return {lbl:'Смотреть',main:fmtS(e.from)+' – '+fmtS(e.due),sub:'симптомы '+rel(e.from,D)};
    if(D<=e.due)return {lbl:'Смотреть сейчас',main:'до '+fmtS(e.due),sub:'осталось '+days(e.due-D)};
    return {lbl:'Срок прошёл',main:fmtS(e.due),sub:rel(e.due,D)};
  }
  return {lbl:'Срок',main:'плановый объезд',sub:''};
}
function changes(D){if(D-1<MIN_D)return [];return FIELDS.filter(f=>st(f.id,D)!==st(f.id,D-1)).map(f=>({id:f.id,from:st(f.id,D-1),to:st(f.id,D)}));}
function upcoming(D){
  const out=[];
  FIELDS.forEach(f=>{const e=ev(f.id,D);
    e.prots.forEach(p=>{if(!p.voided&&p.until>=D&&p.until-D<=5&&['risk','check'].includes(e.status))out.push({i:p.until,id:f.id,t:'защита '+f.id+' заканчивается'});});
    if(e.from&&e.from>D&&e.from-D<=7)out.push({i:e.from,id:f.id,t:'на '+f.id+' пора искать симптомы'});});
  if(NEXT_PASS>D&&NEXT_PASS-D<=7)out.push({i:NEXT_PASS,t:'проход Sentinel-2'});
  const seen=new Set();return out.sort((a,b)=>a.i-b.i).filter(x=>{const k=x.i+x.t;if(seen.has(k))return false;seen.add(k);return true;});
}

/* ═════════ Icons ═════════ */
const IC={
  today:'<path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4Z"/><path d="M10 21h4"/>',
  map:'<path d="m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3Z"/><path d="M9 3v15M15 6v15"/>',
  season:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M8 4v16M13 4v16M18 4v16"/>',
  trips:'<circle cx="6" cy="5" r="2"/><circle cx="18" cy="19" r="2"/><path d="M8 5h8a4 4 0 0 1 0 8H8a3 3 0 0 0 0 6h8"/>',
  journal:'<path d="M3 11a9 9 0 1 1 2 7M3 4v7h7"/><path d="M12 7v6l4 2"/>',
  data:'<path d="M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3Z"/><path d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
  plus:'<path d="M12 5v14M5 12h14"/>',check:'<path d="m5 12 5 5 9-10"/>',spot:'<circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="2"/>',
  up:'<path d="m6 15 6-6 6 6"/>',down:'<path d="m6 9 6 6 6-6"/>',x:'<path d="M6 6l12 12M18 6 6 18"/>',
  pin:'<path d="M12 21s-7-6.2-7-12a7 7 0 0 1 14 0c0 5.8-7 12-7 12Z"/><circle cx="12" cy="9" r="2.5"/>',
  route:'<path d="M4 19c4 0 4-6 8-6s4-6 8-6"/><circle cx="4" cy="19" r="1.5"/><circle cx="20" cy="7" r="1.5"/>',
  copy:'<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
  upload:'<path d="M12 16V4M7 9l5-5 5 5M4 20h16"/>',play:'<path d="M7 5v14l12-7Z"/>',pause:'<path d="M8 5v14M16 5v14"/>',
  left:'<path d="m15 6-6 6 6 6"/>',right:'<path d="m9 6 6 6-6 6"/>',
  alert:'<path d="M12 3 2 20h20Z"/><path d="M12 10v4M12 17.5v.01"/>',info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.01"/>',
  brief:'<path d="M5 4h14v16H5z"/><path d="M8 8h8M8 12h8M8 16h5"/>',bolt:'<path d="M13 2 4 14h7l-1 8 9-12h-7Z"/>',
  spray:'<path d="M4 21h7M7.5 21v-5M4 16h7l1-4H3Z"/><path d="M13 11l7-3M13 12.5h8M13 14l7 3"/>',
  cloud:'<path d="M7 18h10a4 4 0 0 0 .5-8 6 6 0 0 0-11.4 1.5A3.3 3.3 0 0 0 7 18Z"/>',
  ext:'<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>'
};
const icon=n=>'<svg class="i" viewBox="0 0 24 24" aria-hidden="true">'+IC[n]+'</svg>';

/* ═════════ Shell ═════════ */
const VIEWS=[{id:'today',label:'Сегодня'},{id:'map',label:'Карта'},{id:'season',label:'Сезон'},{id:'trips',label:'Выезд'},{id:'journal',label:'Журнал'},{id:'data',label:'Данные'}];
const $main=document.getElementById('main');
const fieldJournal=document.getElementById('field-journal');
const fieldJournalFrame=document.getElementById('field-journal-frame');
function openFieldJournal(fid){
  if(location.protocol==='file:')return;
  const url='/scouting/inspections'+(fid?'?field='+encodeURIComponent(fid):'');
  fieldJournal.showModal();
  if(fieldJournalFrame.getAttribute('src')!==url)fieldJournalFrame.src=url;
  else fieldJournalFrame.contentWindow?.dispatchEvent(new Event('focus'));
}
if(location.protocol!=='file:'){
  fieldJournalFrame.src='/scouting/inspections';
}
function fieldJournalLink(fid){
  if(location.protocol==='file:')return '';
  return '<button type="button" class="btn btn-primary" data-act="field-journal"'+(fid?' data-field="'+esc(fid)+'"':'')+'>Задачи и осмотры хозяйства</button>';
}
function renderRail(){
  const c=counts(S.D);
  document.getElementById('rail').innerHTML=VIEWS.map(v=>{let b='';
    if(v.id==='today'&&c.risk)b='<span class="badge" aria-label="'+c.risk+' в риске">'+c.risk+'</span>';
    if(v.id==='trips'&&S.trip.length)b='<span class="badge n" aria-label="'+S.trip.length+' в выезде">'+S.trip.length+'</span>';
    return (v.id==='data'?'<span class="spacer"></span>':'')+'<button type="button" data-nav="'+v.id+'"'+(S.view===v.id?' aria-current="page"':'')+'><span class="ic">'+icon(v.id)+'</span>'+v.label+b+'</button>';}).join('');
  const lc=SAT.filter(s=>s.i<=TODAY&&s.cloud<=20).slice(-1)[0];
  document.getElementById('fresh').innerHTML='<i></i><span>ERA5 по <b>'+fmtS(DATA_END)+'</b></span><span class="long">· снимок <b>'+(lc?fmtS(lc.i):'—')+'</b> · узел R07 <b>на связи</b></span>';
}
function focusKey(el){
  if(!el||el===document.body||el===$main)return null;
  if(el.id)return '#'+CSS.escape(el.id);
  let s=el.tagName.toLowerCase(),any=false;
  ['nav','filter','layer','jf','tab','move','dir','remove','act','field','grp'].forEach(a=>{if(el.dataset&&el.dataset[a]!=null){s+='[data-'+a+'="'+CSS.escape(el.dataset[a])+'"]';any=true;}});
  return any?s:null;
}
function snapshotRows(){const m={};document.querySelectorAll('.qrow').forEach(r=>{m[r.dataset.field]=r.getBoundingClientRect().top;});return m;}
function flip(prev){
  if(!prev||matchMedia('(prefers-reduced-motion: reduce)').matches)return;
  document.querySelectorAll('.qrow').forEach(r=>{const p=prev[r.dataset.field];if(p==null)return;const d=p-r.getBoundingClientRect().top;if(Math.abs(d)<2)return;if(r.animate)r.animate([{transform:'translateY('+d+'px)'},{transform:'none'}],{duration:300,easing:'cubic-bezier(.2,.7,.2,1)'});});
}
let qScroll=0,fScroll=0;
function render(opts){
  opts=opts||{};
  const fk=focusKey(document.activeElement);
  const prev=opts.flip?snapshotRows():null;
  const q=document.getElementById('qpane'),fp=document.getElementById('fpane');if(q)qScroll=q.scrollTop;if(fp)fScroll=fp.scrollTop;
  renderRail();
  const v=S.view;
  $main.innerHTML=v==='map'?viewMap():v==='season'?viewSeason():v==='trips'?viewTrips():v==='journal'?viewJournal():v==='data'?viewData():viewToday();
  const q2=document.getElementById('qpane'),f2=document.getElementById('fpane');if(q2)q2.scrollTop=qScroll;if(f2)f2.scrollTop=opts.resetField?0:fScroll;
  drawTimeline();
  if(v==='data')bindDrop();
  if(opts.reveal){const r=document.querySelector('.qrow[data-field="'+S.sel+'"]');if(r)r.scrollIntoView({block:'nearest'});}
  flip(prev);
  if(fk){const n=document.querySelector(fk);if(n&&!n.disabled)n.focus({preventScroll:true});}
  fitSticky();
}
function fitSticky(){const fp=document.getElementById('fpane');if(!fp||!fp.classList.contains('pane'))return;fp.classList.remove('stick');fp.classList.toggle('stick',innerWidth>1080&&fp.offsetHeight<$main.clientHeight-32);}
function go(view){
  stopPlay();S.view=view;
  if(location.hash!=='#'+view){try{history.replaceState(null,'','#'+view);}catch(e){location.hash=view;}}
  render();$main.scrollTop=0;
}
function setDate(D){D=Math.max(MIN_D,Math.min(DATA_END,D));if(D===S.D)return;S.D=D;render({flip:true});}

/* ═════════ Timeline ═════════ */
function timelineCard(){
  const c=counts(S.D),past=S.D!==TODAY;
  return '<section class="card tl" aria-label="Шкала сезона">'+
    '<div class="tl-head"><div class="tl-date"><strong>'+cap(fmt(S.D))+' 2024</strong><span>по 10 суткам '+fmtS(S.D-9)+' – '+fmtS(S.D)+' · риск '+c.risk+' · проверить '+c.check+'</span></div>'+
    (past?'<span class="tl-past">Пересчёт по сегодняшнему учёту<button type="button" data-act="today">К '+fmtS(TODAY)+' <kbd>T</kbd></button></span>':'')+
    '<div class="tl-ctrl"><button type="button" class="icon-btn" data-act="prev" aria-label="Предыдущий день" title="Предыдущий день (←)"'+(S.D<=MIN_D?' disabled':'')+'>'+icon('left')+'</button>'+
    '<button type="button" class="icon-btn" data-act="next" aria-label="Следующий день" title="Следующий день (→)"'+(S.D>=DATA_END?' disabled':'')+'>'+icon('right')+'</button>'+
    (S.tlCompact?'':'<button type="button" class="btn btn-sm btn-secondary" data-act="play" aria-pressed="'+(!!S.playing)+'" title="Проиграть сезон (P)">'+icon(S.playing?'pause':'play')+(S.playing?'Пауза':'Сезон')+'</button>')+
    '<button type="button" class="btn btn-sm btn-ghost" data-act="tl-toggle" aria-expanded="'+!S.tlCompact+'">'+(S.tlCompact?'Показать шкалу':'Свернуть')+'</button></div></div>'+
    (S.tlCompact?'</section>':'<div id="tl-svg"></div>'+
    '<div class="tl-legend"><span><i data-style="background:var(--red)"></i>влажность выше порога</span><span><i data-style="background:var(--amber)"></i>половина порога</span><span><i data-style="background:var(--rain);border-radius:50%"></i>дождь ≥ 10 мм</span><span><i data-style="background:var(--green);border-radius:50%"></i>снимок чистый</span><span><i data-style="border:2px solid var(--green);background:var(--paper);border-radius:50%"></i>частично</span><span><i data-style="border:2px dashed var(--planned);background:var(--paper);border-radius:50%"></i>облака</span><span><i data-style="background:var(--green-tint)"></i>защита '+S.sel+'</span><span><i data-style="border:2px dashed var(--check-strong)"></i>окно осмотра</span><span><i data-style="border:1.5px dashed var(--ink-2);background:var(--paper)"></i>прогноз</span><span><i data-style="background:repeating-linear-gradient(45deg,var(--line) 0 3px,var(--soft) 3px 6px)"></i>нет данных</span></div>'+
  '</section>');
}
/* growth stages as tiny plant glyphs (14×14): tillering, stem, flag leaf, ear */
function GLYPH(p){return p==='кущение'?'M7 13V8M7 13C6 9 3 8 2 6M7 13c1-4 4-5 5-7M7 8c0-3 1-5 1-6':
  p==='выход в трубку'?'M7 13V1M4.5 10h5M4.5 5.5h5':
  p==='флаговый лист'?'M7 13V3M7 8c2-4 5-5 6.5-5-1 3.5-3.5 5-6.5 5':
  p==='колошение'?'M7 13V8M7 8C5 6.5 5 2.5 7 1c2 1.5 2 5.5 0 7Z':'M7 13V5';}
let TL=null;
function drawTimeline(){
  const host=document.getElementById('tl-svg');if(!host)return;
  const W=Math.max(300,Math.floor(host.getBoundingClientRect().width)),L=W<560?64:112,R=6,n=SEASON,dw=(W-L-R)/n;
  const f=fieldById(S.sel),e=ev(S.sel);
  const x=i=>L+i*dw,cx=i=>L+(i+.5)*dw;
  const y1=20,h1=14,y2=y1+h1+7,h2=34,y3=y2+h2+7,h3=14,y4=y3+h3+8,h4=24,H=y4+h4+4;
  TL={L,dw,W,H};
  let s='<svg width="'+W+'" height="'+H+'" viewBox="0 0 '+W+' '+H+'" role="img" aria-label="Сезон с 1 апреля по 31 мая 2024: хозяйство, погода, снимки и события поля '+S.sel+'. Выбрана дата '+fmt(S.D)+'. Стрелки влево и вправо меняют дату.">';
  s+='<defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="6" fill="#F5F7F6"/><rect width="2.5" height="6" fill="#E3E8E5"/></pattern><pattern id="voidhatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="6" fill="#FCE4E3"/><rect width="2" height="6" fill="#EE9F9C"/></pattern></defs>';
  for(let i=0;i<n;i++){const d=dt(i).getUTCDate();if(d===1)s+='<text class="mon" x="'+(x(i)+2)+'" y="12">'+(dt(i).getUTCMonth()===3?'Апрель':'Май')+'</text>';else if(d%5===0&&dw>6&&d!==30)s+='<text x="'+cx(i)+'" y="12" text-anchor="middle">'+d+'</text>';}
  [['Хозяйство',y1,h1],['Погода'+(f.sensor?' · узел':''),y2,h2],['Снимки',y3,h3],[S.sel,y4,h4]].forEach(([lbl,y,h])=>{s+='<rect class="lane-bg" x="'+L+'" y="'+y+'" width="'+(W-L-R)+'" height="'+h+'" rx="4"/><text class="lane-l" x="0" y="'+(y+h/2+4)+'">'+(W<560&&lbl.length>8?lbl.slice(0,6)+'.':lbl)+'</text>';});
  s+='<rect fill="url(#hatch)" x="'+x(DATA_END+1)+'" y="'+y1+'" width="'+(x(n)-x(DATA_END+1))+'" height="'+(y2+h2-y1)+'" rx="4"/>';
  s+='<rect fill="url(#hatch)" x="'+x(0)+'" y="'+y1+'" width="'+(x(MIN_D)-x(0))+'" height="'+h1+'" rx="4"/>';
  for(let i=MIN_D;i<=DATA_END;i++){const c=counts(i),a=Math.min(h1,c.risk/27*h1*3),b=Math.min(h1-a,c.check/27*h1*3);
    if(a>0)s+='<rect class="farm-r" x="'+(x(i)+.5)+'" y="'+(y1+h1-a)+'" width="'+Math.max(1,dw-1)+'" height="'+a+'"/>';
    if(b>0)s+='<rect class="farm-c" x="'+(x(i)+.5)+'" y="'+(y1+h1-a-b)+'" width="'+Math.max(1,dw-1)+'" height="'+b+'"/>';}
  for(let i=0;i<=DATA_END;i++){const w=wxOf(f,i),hh=Math.min(h2-2,w.wet/14*(h2-2));let cls='';
    RULES.diseases.forEach(dz=>{if(w.t>=dz.tmin&&w.t<=dz.tmax){if(w.wet>=dz.wet)cls='f';else if(w.wet>=Math.ceil(dz.wet/2)&&cls!=='f')cls='p';}});
    if(hh>0)s+='<rect class="wx '+cls+'" x="'+(x(i)+1)+'" y="'+(y2+h2-hh)+'" width="'+Math.max(1,dw-2)+'" height="'+hh+'" rx="1.5"/>';
    if(w.rain>=RULES.washMm)s+='<circle class="rain" cx="'+cx(i)+'" cy="'+(y2+6)+'" r="3.5"/>';}
  FC.forEach(d=>{const hh=Math.min(h2-2,d.wet/14*(h2-2));let cls='';RULES.diseases.forEach(dz=>{if(d.t>=dz.tmin&&d.t<=dz.tmax){if(d.wet>=dz.wet)cls='f';else if(d.wet>=Math.ceil(dz.wet/2)&&cls!=='f')cls='p';}});
    s+='<rect class="wx fc '+cls+'" x="'+(x(d.i)+1.5)+'" y="'+(y2+h2-hh)+'" width="'+Math.max(1,dw-3)+'" height="'+hh+'" rx="1.5"/>';});
  SAT.forEach(p=>{const k=satKind(p.cloud);s+='<circle class="sat '+(k==='clear'?'':k)+'" cx="'+cx(p.i)+'" cy="'+(y3+h3/2)+'" r="5"/>';});
  s+='<circle class="sat next" cx="'+cx(NEXT_PASS)+'" cy="'+(y3+h3/2)+'" r="5"/>';
  protections(f,SEASON).forEach(p=>{s+='<rect '+(p.voided?'fill="url(#voidhatch)"':'class="prot"')+' x="'+x(p.t)+'" y="'+(y4+3)+'" width="'+(x(Math.min(n-1,p.until)+1)-x(p.t))+'" height="'+(h4-6)+'" rx="3"/>';});
  if(e.from&&e.due!=null)s+='<rect class="win" x="'+x(e.from)+'" y="'+(y4+1)+'" width="'+(x(Math.min(n-1,e.due)+1)-x(e.from))+'" height="'+(h4-2)+'" rx="4"/>';
  f.phases.forEach(([p,i])=>{if(i<0)return;const gx=x(i);
    s+='<line class="ph" x1="'+gx+'" x2="'+gx+'" y1="'+y4+'" y2="'+(y4+h4)+'"/><circle class="glyph-bg" cx="'+gx+'" cy="'+(y4+h4/2)+'" r="9"/><path class="glyph" transform="translate('+(gx-7)+' '+(y4+h4/2-7)+')" d="'+GLYPH(p)+'"/>'+
      (dw>7?'<text class="phl" x="'+(gx+12)+'" y="'+(y4+h4/2+4)+'">'+(p==='выход в трубку'?'трубка':p==='флаговый лист'?'флаг. лист':p)+'</text>':'');});
  f.treat.forEach(i=>{const c=cx(i),m=y4+h4/2;s+='<path class="trt" d="M'+c+' '+(m-6)+'L'+(c+6)+' '+m+'L'+c+' '+(m+6)+'L'+(c-6)+' '+m+'Z"/>';});
  S.marks.filter(m=>m.fid===S.sel&&m.i>=0).forEach(m=>{s+='<circle class="'+(m.result==='focus'?'mk-f':'mk-c')+'" cx="'+cx(m.i)+'" cy="'+(y4+h4/2)+'" r="5.5"/>';});
  s+='<line class="today-l" x1="'+cx(TODAY)+'" x2="'+cx(TODAY)+'" y1="'+(y1-2)+'" y2="'+(H-2)+'"/>';
  if(S.hlDay!=null&&S.hlDay>=0)s+='<rect class="hl" x="'+x(S.hlDay)+'" y="'+(y1-2)+'" width="'+dw+'" height="'+(H-y1)+'" rx="3"/>';
  s+='<rect class="hover" id="tl-hover" x="0" y="'+(y1-2)+'" width="'+dw+'" height="'+(H-y1)+'" rx="3" data-style="display:none"/>';
  s+='<line class="cursor" x1="'+cx(S.D)+'" x2="'+cx(S.D)+'" y1="'+(y1-5)+'" y2="'+(H-1)+'"/><circle class="cursor-h" cx="'+cx(S.D)+'" cy="'+(y1-5)+'" r="4.5"/>';
  host.innerHTML=s+'</svg>';
}
function dayAtX(clientX){const sv=document.querySelector('#tl-svg svg');if(!sv||!TL)return S.D;const r=sv.getBoundingClientRect();return Math.floor((clientX-r.left-TL.L)/TL.dw);}
function tipFor(i){
  const f=fieldById(S.sel),p=SAT.find(s=>s.i===i);
  let h='<b>'+cap(fmt(i))+'</b>';
  if(i>DATA_END)h+='<br>Погоды ещё нет'+(i===NEXT_PASS?'<br>Следующий проход Sentinel-2':'');
  else{const w=wxOf(f,i);h+='<br>'+(w.src==='node'?'Мокрый лист (узел): ':'Влажность ERA5: ')+w.wet+' ч при '+w.t+' °C'+(w.rain?' · дождь '+w.rain+' мм':'');
    if(i>=MIN_D){const c=counts(i);h+='<br>Хозяйство: риск '+c.risk+', проверить '+c.check+'<br>'+S.sel+': '+LABEL[st(S.sel,i)];}else h+='<br>Окно расчёта неполное';}
  if(p)h+='<br>Снимок: облачность '+p.cloud+' %';
  f.treat.filter(t=>t===i).forEach(()=>{h+='<br>Обработка фунгицидом';});
  f.phases.filter(q=>q[1]===i).forEach(q=>{h+='<br>Фаза: '+q[0];});
  S.marks.filter(m=>m.fid===S.sel&&m.i===i).forEach(m=>{h+='<br>Осмотр: '+(m.result==='focus'?'очаг':'чисто');});
  if(i>=MIN_D&&i<=DATA_END)h+='<br><span data-style="opacity:.7">Нажмите — пересчитать на эту дату</span>';
  return h;
}

/* ═════════ Today ═════════ */
function viewToday(){
  const c=counts(S.D),ch=changes(S.D),up=upcoming(S.D);
  const chip=(k,l)=>'<button type="button" class="'+k+'" data-filter="'+k+'" aria-pressed="'+(S.filter===k)+'"><i></i>'+l+' <b>'+(k==='all'?27:c[k])+'</b></button>';
  let dig='';
  if(ch.length)dig+='<span class="lbl">Со вчера</span>'+ch.map(x=>'<button type="button" data-act="select" data-field="'+x.id+'"><b>'+x.id+'</b> '+LABEL[x.from]+' → <span class="'+(ORDER[x.to]<ORDER[x.from]?'up':'dn')+'">'+LABEL[x.to]+'</span></button>').join('');
  else dig+='<span class="lbl">Со вчера</span><span class="muted" data-style="font-size:13px;margin-right:8px">статусы не менялись</span>';
  if(S.D===TODAY){
    const byDay={};FIELDS.forEach(f=>{if(st(f.id)==='risk')return;const r=forecastRisk(f);if(r&&!r.plan)(byDay[r.i]=byDay[r.i]||[]).push(f.id);});
    const days=Object.keys(byDay).sort((a,b)=>a-b);if(days.length)dig+='<span class="lbl fc-l" data-style="margin-left:8px">'+icon('cloud')+'Прогноз</span>';
    days.forEach(i=>{dig+='<button type="button" class="fc-chip" data-act="select" data-field="'+byDay[i][0]+'"><b>'+fmtS(+i)+'</b> условия заражения: '+byDay[i].join(', ')+'</button>';});
    const pl=S.tasks.filter(t=>t.status==='plan').sort((a,b)=>a.plan-b.plan);
    if(pl.length)dig+='<span class="lbl" data-style="margin-left:8px">План</span>'+pl.slice(0,3).map(t=>'<button type="button" data-act="select" data-field="'+t.fid+'">'+icon('spray')+'<b>'+t.fid+'</b> обработка '+fmtS(t.plan)+'</button>').join('');
  }
  if(up.length)dig+='<span class="lbl" data-style="margin-left:8px">Скоро</span>'+up.slice(0,3).map(x=>'<button type="button" '+(x.id?'data-act="select" data-field="'+x.id+'"':'data-act="noop"')+'><b>'+fmtS(x.i)+'</b> '+esc(x.t)+'</button>').join('');
  return '<section class="view fit" aria-labelledby="h-today">'+timelineCard()+
    '<div class="head"><h1 id="h-today">Очередь осмотра</h1><div class="filters" role="group" aria-label="Фильтр по статусу">'+chip('all','Все')+chip('risk','Риск')+chip('check','Проверить')+chip('normal','Норма')+chip('none','Нет правил')+'</div>'+
      '<div class="acts">'+fieldJournalLink()+'<button type="button" class="btn btn-sm btn-secondary" data-act="brief">'+icon('brief')+'Сводка</button><button type="button" class="btn btn-sm btn-soft" data-act="trip-auto">'+icon('bolt')+'Собрать выезд</button></div>'+
      '<div class="digest">'+dig+'</div></div>'+
    '<div class="panes"><div class="pane queue" id="qpane" aria-label="Поля по очереди">'+queueHTML()+'</div><div class="pane" id="fpane">'+fieldHTML(S.sel)+'</div></div>'+
  '</section>';
}
function queueHTML(){
  const ids=sortedIds(S.D).filter(id=>S.filter==='all'||st(id)===S.filter);
  let h='<div class="q-cols" aria-hidden="true"><span>Поле</span><span>Культура · фаза</span><span>Статус, 14 дней</span><span>Когда</span><span></span></div>';
  if(!ids.length)return h+'<div class="q-empty">Полей с этим статусом на '+fmt(S.D)+' нет.</div>';
  ['risk','check','normal','none'].forEach(g=>{
    const list=ids.filter(id=>st(id)===g);if(!list.length)return;
    const col=S.filter==='all'&&S.collapsed[g];
    h+='<button type="button" class="q-group" data-grp="'+g+'" aria-expanded="'+(!col)+'"><i data-style="background:var(--'+(g==='risk'?'red':g==='check'?'amber':g==='normal'?'green':'planned')+')"></i>'+LABEL[g]+' · '+list.length+(col?' — показать':'')+'<svg class="i chev" viewBox="0 0 24 24" aria-hidden="true">'+IC.down+'</svg></button>';
    if(!col)h+=list.map(qrow).join('');
  });
  return h;
}
function qrow(id){
  const f=fieldById(id),e=ev(id),w=whenText(e,S.D);
  let rib='';for(let i=S.D-13;i<=S.D;i++)rib+='<i class="'+(i<MIN_D?'':st(id,i))+(i===S.D?' cur':'')+'"></i>';
  const inTrip=S.trip.includes(id),m=S.marks.filter(x=>x.fid===id&&x.i<=S.D).sort((a,b)=>b.i-a.i)[0];
  const ph=phaseAt(f,S.D);
  return '<button type="button" class="qrow '+e.status+'" data-field="'+id+'" aria-current="'+(S.sel===id)+'" aria-label="'+id+', '+LABEL[e.status]+', '+esc(w.lbl+' '+w.main)+'">'+
    '<span class="id">'+id+'</span>'+
    '<span class="cp"><b>'+cap(f.crop)+'</b><span>'+(ph||(f.crop==='кукуруза'?'всходы':'фаза не указана'))+' · '+f.ha+' га'+(f.sensor?' · узел':'')+'</span></span>'+
    '<span class="ribbon" aria-hidden="true">'+rib+'</span>'+
    '<span class="due"><span>'+w.lbl+'</span><b>'+w.main+'</b></span>'+
    '<span class="flags">'+(plannedFor(id).length?'<span title="Обработка запланирована '+fmt(plannedFor(id)[0].plan)+'" data-style="color:var(--green-press)">'+icon('spray')+'</span>':'')+(inTrip?'<span title="В выезде">'+icon('route')+'</span>':'')+(m&&S.D-m.i<=7?'<span title="Осмотрено '+fmt(m.i)+'" data-style="color:var(--'+(m.result==='focus'?'red':'green')+')">'+icon(m.result==='focus'?'spot':'check')+'</span>':'')+'</span>'+
  '</button>';
}

/* ═════════ Field pane ═════════ */
function fieldHTML(id,compact){
  const f=fieldById(id);if(!f)return '<div class="card empty">Выберите поле.</div>';
  const e=ev(id),w=whenText(e,S.D),inTrip=S.trip.includes(id),ph=phaseAt(f,S.D);
  const nEv=S.marks.filter(m=>m.fid===id).length+f.treat.length+FOCI.filter(x=>x.fid===id).length;
  const tab=e.status==='none'&&S.tab==='wx'?'why':S.tab;
  const body=tab==='wx'?wxTab(f,e):tab==='sat'?satTab(f):tab==='hist'?histTab(f):whyTab(f,e);
  return '<article class="card field" aria-label="Поле '+id+'">'+
    '<div class="f-head"><div class="f-top"><span class="f-id">'+id+'</span>'+
      '<div class="f-meta"><span class="chip '+e.status+'">'+LABEL[e.status]+'</span><b>'+cap(f.crop)+' · '+(ph||'фаза не указана')+'</b></div>'+
      '<div class="f-when '+e.status+'"><span>'+w.lbl+'</span><strong>'+w.main+'</strong>'+(w.sub?'<small>'+w.sub+'</small>':'')+'</div>'+
      '<div class="f-sub">'+f.ha+' га · '+(f.sensor?'узел на поле + ERA5':'погода ERA5, ячейка 0,25°')+'</div></div>'+
      '<div class="f-acts">'+fieldJournalLink(f.id)+(e.status!=='none'?'<button type="button" class="btn btn-secondary" data-act="inspect">'+icon('check')+'Демо-осмотр<kbd>I</kbd></button>':'')+
        (e.status!=='none'?(()=>{const pl=plannedFor(id)[0];return pl?'<button type="button" class="btn btn-soft" data-act="goto-tasks">'+icon('spray')+'Обработка '+fmtS(pl.plan)+'</button>':'<button type="button" class="btn btn-secondary" data-act="plan" data-field="'+id+'">'+icon('spray')+'Обработка</button>';})():'')+
        '<button type="button" class="btn '+(inTrip?'btn-soft':'btn-secondary')+'" data-act="toggle-trip" data-field="'+id+'">'+icon(inTrip?'check':'plus')+(inTrip?'В выезде':'В выезд')+'</button>'+
        (compact?'<button type="button" class="btn btn-ghost" data-act="open-today" data-field="'+id+'">В очереди →</button>':'<button type="button" class="btn btn-ghost" data-act="open-map" data-field="'+id+'">'+icon('pin')+'На карте</button>')+'</div>'+
      '<div class="tabs" role="tablist" aria-label="Сведения о поле">'+[['why','Причины'],['wx','Погода'],['sat','Снимки'],['hist','История',nEv]].map(([k,l,n])=>'<button type="button" role="tab" data-tab="'+k+'" aria-selected="'+(tab===k)+'"'+(e.status==='none'&&k==='wx'?' disabled':'')+'>'+l+(n?'<span class="cnt">'+n+'</span>':'')+'</button>').join('')+'</div></div>'+
    '<div class="f-body" role="tabpanel">'+body+'</div></article>';
}
function forecastBox(f){
  if(S.D!==TODAY)return '';const r=forecastRisk(f);if(!r)return '';
  const w=sprayWindows().find(x=>x.i<=r.i);
  return '<div class="fcbox">'+icon('cloud')+'<div><b>Прогноз на '+fmt(r.i)+': '+r.h+' ч влажности при '+r.t+' °C</b> — условия для '+r.dzs.map(z=>z.gen).join(' и ')+(r.plan?'. ':', а защиты на эту дату нет. ')+
    (r.plan?'Запланированная обработка '+fmt(r.plan.plan)+' закроет эту ночь.':'Окно для опрыскивания до неё — '+winText(w)+'.')+
    (r.plan?'':'<div><button type="button" class="btn btn-sm btn-primary" data-act="plan" data-field="'+f.id+'">'+icon('spray')+'Запланировать обработку</button></div>')+'<small>Прогноз — пример: в рабочей версии он придёт из GFS или ICON.</small></div></div>';
}
function whyTab(f,e){
  let h=forecastBox(f)+'<ul class="reasons">'+e.reasons.map(r=>'<li class="'+r.c+'"'+(r.day!=null&&r.day>=0?' data-day="'+r.day+'"':'')+'>'+r.t+'</li>').join('')+'</ul>';
  if(e.status==='check'||e.status==='normal'){
    const ph=phaseAt(f,S.D),p=e.prots.find(p=>!p.voided&&p.t<=S.D&&S.D<=p.until);
    let txt;
    if(!ph||phIdx(ph)<1)txt='Поле станет восприимчивым с выхода в трубку. До этого влажные ночи статус не меняют.';
    else if(p)txt='Пока действует защита (до '+fmt(p.until)+'), влажные ночи не дают «Риск». После неё хватит одной ночи: 6 ч влажности при 15–25 °C.';
    else{const g=e.gaps.rust;txt='До «Риска» не хватило '+Math.max(0,6-(g?g.h:0))+' ч влажности при 15–25 °C. Самая влажная подходящая ночь за 10 суток — '+(g?fmt(g.i)+', '+g.h+' ч':'не было')+'.';}
    h+='<div class="whatif"><h3>Что переведёт поле в «Риск»</h3><p>'+txt+'</p>';
    if(ph&&phIdx(ph)>=1&&!p)RULES.diseases.forEach(dz=>{const g=e.gaps[dz.id],hv=g?g.h:0;h+='<div class="meter"><span>'+cap(dz.short)+'</span><span class="bar"><i class="'+(hv>=dz.wet?'full':'')+'" data-style="width:'+Math.min(100,hv/dz.wet*100)+'%"></i></span><b>'+hv+' из '+dz.wet+' ч</b></div>';});
    h+='</div>';
  }
  if(e.warns.length)h+='<div class="warns">'+e.warns.map(w=>'<div class="warn '+(w.k==='info'?'info':'')+'">'+icon(w.k==='info'?'info':'alert')+'<span>'+w.t+'</span></div>').join('')+'</div>';
  if(e.from)h+='<p class="hint">Окно осмотра начинается через '+LATENT+' дней после влажной ночи: столько обычно длится скрытый период до симптомов. Это допущение, его нужно проверить на пилоте.</p>';
  return h;
}
function wxTab(f,e){
  const Wd=640,H=214,L=34,R=104,T=16,B=40,max=16,n=10,bw=(Wd-L-R)/n,y=v=>T+(H-T-B)*(1-v/max);
  let s='<svg class="chart" viewBox="0 0 '+Wd+' '+H+'" role="img" aria-label="Влажные часы по дням, '+fmtS(S.D-9)+' – '+fmtS(S.D)+'">';
  [0,4,8,12,16].forEach(v=>{s+='<line class="grid" x1="'+L+'" x2="'+(Wd-R+4)+'" y1="'+y(v)+'" y2="'+y(v)+'"/><text x="'+(L-8)+'" y="'+(y(v)+4)+'" text-anchor="end">'+v+'</text>';});
  const all=e.full.concat(e.part,e.covered);
  for(let k=0;k<n;k++){const i=S.D-9+k;if(i<0)continue;const w=wxOf(f,i),xx=L+k*bw+bw*.18,ww=bw*.64;
    const evs=all.filter(z=>z.i===i),covd=e.prots.some(p=>!p.voided&&p.t<=i&&i<=p.until);
    const cls=evs.some(z=>z.full&&!z.cover)?'f':evs.some(z=>!z.cover)?'p':(covd?'cov':'');
    if(w.wet>0)s+='<rect class="bar '+cls+(S.hlDay===i?' hl':'')+'" data-day="'+i+'" x="'+xx.toFixed(1)+'" y="'+y(w.wet).toFixed(1)+'" width="'+ww.toFixed(1)+'" height="'+(y(0)-y(w.wet)).toFixed(1)+'" rx="4"><title>'+fmt(i)+': '+w.wet+' ч при '+w.t+' °C'+(w.src==='node'?' (узел)':'')+'</title></rect>';
    if(w.wet>=3)s+='<text class="lbl" x="'+(xx+ww/2).toFixed(1)+'" y="'+(y(w.wet)-6).toFixed(1)+'" text-anchor="middle">'+w.wet+'</text>';
    s+='<text x="'+(xx+ww/2).toFixed(1)+'" y="'+(H-B+16)+'" text-anchor="middle">'+dt(i).getUTCDate()+'</text>';
    if(w.rain>=1)s+='<circle class="raind" cx="'+(xx+ww/2).toFixed(1)+'" cy="'+(H-B+29)+'" r="'+Math.min(6,2+w.rain/4).toFixed(1)+'"><title>'+w.rain+' мм</title></circle>';}
  RULES.diseases.forEach(dz=>{s+='<line class="thr" x1="'+L+'" x2="'+(Wd-R+4)+'" y1="'+y(dz.wet)+'" y2="'+y(dz.wet)+'"/><text class="lbl" x="'+(Wd-R+10)+'" y="'+(y(dz.wet)+4)+'">'+dz.short+' '+dz.wet+' ч</text><text x="'+(Wd-R+10)+'" y="'+(y(dz.wet)+19)+'">'+dz.tmin+'–'+dz.tmax+' °C</text>';});
  s+='</svg>';
  const sum=[];for(let i=Math.max(0,S.D-9);i<=S.D;i++)sum.push(wxOf(f,i));
  const rain=sum.reduce((a,w)=>a+w.rain,0),wetN=sum.filter(w=>w.wet>=6).length;
  return '<h3>Влажные часы за 10 суток</h3>'+s+
    '<div class="legend"><span><i data-style="background:var(--red)"></i>порог достигнут, защиты нет</span><span><i data-style="background:var(--amber)"></i>половина порога</span><span><i data-style="background:#9ACFAE"></i>под защитой</span><span><i data-style="background:var(--rain);border-radius:50%"></i>дождь, мм</span></div>'+
    '<div class="stats"><div class="stat"><span>Ночей ≥ 6 ч</span><strong>'+wetN+'</strong></div><div class="stat"><span>Осадки</span><strong>'+num(rain,0)+' мм</strong></div><div class="stat"><span>Источник</span><strong data-style="font-size:16px">'+(f.sensor?'узел + ERA5':'ERA5')+'</strong><small>'+(f.sensor?'дни с узлом — мокрый лист':'RH ≥ 90 % или осадки ≥ 0,2 мм')+'</small></div></div>'+
    (S.D===TODAY?sprayGrid():'')+
    '<p class="hint">ERA5 — реанализ по ячейке 0,25°: одна погода на всё хозяйство. Это влажность воздуха, а не измерение листа; поля различаются фазой, защитой и данными узла. Прогноза в прототипе нет.</p>';
}
function sprayGrid(){
  const bl=sprayBlocks();let g='<div class="sgrid" role="table" aria-label="Окна для опрыскивания на 3 дня"><span></span>'+['00','04','08','12','16','20'].map(h=>'<span class="h">'+h+'</span>').join('')+'<span class="h">ночь, влажн.</span>';
  FC.forEach(d=>{g+='<span>'+fmtS(d.i)+'</span>'+bl.filter(b=>b.i===d.i).map(b=>'<i class="'+(b.ok?'ok':'')+'" title="'+String(b.from).padStart(2,'0')+'–'+String(b.to).padStart(2,'0')+': '+(b.ok?'можно опрыскивать':b.why)+' · ветер '+num(b.wind,1)+' м/с, '+b.t+' °C">'+(b.ok?'да':b.why.split(' ')[0])+'</i>').join('');
    const cls=RULES.diseases.some(z=>d.t>=z.tmin&&d.t<=z.tmax&&d.wet>=z.wet)?'f':RULES.diseases.some(z=>d.t>=z.tmin&&d.t<=z.tmax&&d.wet>=Math.ceil(z.wet/2))?'p':'';
    g+='<span class="n '+cls+'">'+d.wet+' ч · '+d.t+' °C</span>';});
  return '<h3>Прогноз и окна для опрыскивания · пример</h3>'+g+'</div><p class="hint">«Да» — ветер ≤ 4 м/с, без дождя в этом и следующем интервале, 8–25 °C. Красным — ночь с условиями заражения. Ближайшее окно: '+winText(sprayWindows()[0])+'.</p>';
}
function satTab(f){
  const pts=SAT.filter(s=>s.i<=S.D),nd=NDVI[f.id];
  const farm={};pts.forEach(p=>{const v=FIELDS.filter(g=>g.crop!=='кукуруза').map(g=>NDVI[g.id][p.i]).filter(v=>v!=null).sort((a,b)=>a-b);if(v.length)farm[p.i]=v[Math.floor(v.length/2)];});
  const Wd=640,H=200,L=40,R=16,T=14,B=40,x=i=>L+i/(SEASON-1)*(Wd-L-R),min=.1,max=.85,y=v=>T+(H-T-B)*(1-(v-min)/(max-min));
  let s='<svg class="chart" viewBox="0 0 '+Wd+' '+H+'" role="img" aria-label="NDVI поля '+f.id+' по снимкам Sentinel-2">';
  [.2,.4,.6,.8].forEach(v=>{s+='<line class="grid" x1="'+L+'" x2="'+(Wd-R)+'" y1="'+y(v)+'" y2="'+y(v)+'"/><text x="'+(L-8)+'" y="'+(y(v)+4)+'" text-anchor="end">'+num(v,1)+'</text>';});
  pts.forEach(p=>{if(p.cloud>60)s+='<rect class="cloud" x="'+(x(p.i)-7)+'" y="'+T+'" width="14" height="'+(H-T-B)+'" rx="5"><title>'+fmt(p.i)+': облачность '+p.cloud+' %, снимка нет</title></rect>';s+='<text x="'+x(p.i)+'" y="'+(H-B+16)+'" text-anchor="middle">'+dt(p.i).getUTCDate()+'</text>';});
  s+='<text x="'+x(0)+'" y="'+(H-6)+'">апрель</text><text x="'+x(30)+'" y="'+(H-6)+'">май</text>';
  const fp=pts.filter(p=>nd[p.i]!=null),mp=pts.filter(p=>farm[p.i]!=null);
  if(mp.length>1&&f.crop!=='кукуруза')s+='<path class="ln2" d="M'+mp.map(p=>x(p.i).toFixed(1)+','+y(farm[p.i]).toFixed(1)).join('L')+'"/>';
  if(fp.length>1)s+='<path class="ln" d="M'+fp.map(p=>x(p.i).toFixed(1)+','+y(nd[p.i]).toFixed(1)).join('L')+'"/>';
  fp.forEach(p=>{s+='<circle class="'+(p.cloud<=20?'pt':'pt2')+'" cx="'+x(p.i).toFixed(1)+'" cy="'+y(nd[p.i]).toFixed(1)+'" r="4.5"><title>'+fmt(p.i)+': NDVI '+num(nd[p.i])+' · облачность '+p.cloud+' %</title></circle>';});
  s+='</svg>';
  const clear=fp.filter(p=>p.cloud<=20);let stats='',note='';
  if(clear.length>=2){const a=clear[clear.length-2],b=clear[clear.length-1],d=nd[b.i]-nd[a.i],dm=(farm[b.i]||0)-(farm[a.i]||0);
    stats='<div class="stats"><div class="stat"><span>NDVI · '+fmtS(b.i)+'</span><strong>'+num(nd[b.i])+'</strong><small class="'+(d<-.03?'dn':d>.01?'up':'')+'">'+(d<0?'↓ ':'↑ ')+num(Math.abs(d))+' с '+fmtS(a.i)+'</small></div><div class="stat"><span>Медиана хозяйства</span><strong>'+num(farm[b.i]||0)+'</strong><small>'+(dm<0?'↓ ':'↑ ')+num(Math.abs(dm))+'</small></div><div class="stat"><span>Против хозяйства</span><strong>'+(d-dm>=0?'+':'−')+num(Math.abs(d-dm))+'</strong><small>за '+days(b.i-a.i)+'</small></div></div>';
    if(f.crop!=='кукуруза'&&d-dm<-.03){const ph=phaseAt(f,b.i);note='<div class="warn">'+icon('alert')+'<span>NDVI '+f.id+' упал на '+num(Math.abs(d))+', а у хозяйства '+(dm<0?'на '+num(Math.abs(dm)):'не упал')+'. '+(ph==='колошение'?'На колошении часть падения — естественное старение листа. ':'')+'Снимок не отличает болезнь от полегания или засухи: проверьте участок на месте.</span></div>';}}
  return '<h3>NDVI по снимкам Sentinel-2</h3>'+s+
    '<div class="legend"><span><i data-style="background:var(--green);border-radius:50%"></i>'+f.id+', чистый снимок</span><span><i data-style="border:2px solid var(--green);border-radius:50%;background:var(--paper)"></i>частично облачно</span><span><i data-style="border-top:2px dashed var(--planned);height:0;border-radius:0;width:14px"></i>медиана хозяйства</span><span><i data-style="background:var(--soft-2)"></i>облака</span></div>'+stats+note+
    '<p class="hint">Следующий проход — '+fmt(NEXT_PASS)+'. Сравнивайте только чистые снимки: облачность больше 20 % искажает индекс.</p>';
}
function histTab(f){
  const pp=PASSPORT[f.id]||{};
  const pass='<dl class="kv pass"><dt>Предшественник</dt><dd>'+esc(pp.pred||'не указан')+(f.crop==='озимая пшеница'&&/пшениц/.test(pp.pred||'')?' <span class="tag r">пшеница по пшенице</span>':'')+'</dd><dt>Сев</dt><dd>'+(pp.sown?fmt(ixISO(pp.sown)):'не указан')+'</dd><dt>Сорт</dt><dd>не указан в учёте <span class="hint">— добавьте, чтобы учитывать устойчивость</span></dd><dt>Площадь</dt><dd>'+f.ha+' га по обводке</dd></dl>';
  let strip='<div class="strip" data-style="grid-template-columns:repeat('+SEASON+',1fr)" role="img" aria-label="Статус '+f.id+' по дням сезона">';
  for(let i=0;i<SEASON;i++)strip+='<i class="'+(i<MIN_D||i>DATA_END?'nd':st(f.id,i))+(i===S.D?' cur':'')+'" title="'+fmtS(i)+(i<MIN_D||i>DATA_END?': нет расчёта':': '+LABEL[st(f.id,i)])+'"></i>';
  strip+='</div>';
  const items=[];
  f.phases.forEach(([p,i])=>items.push({i,c:'',t:'Фаза <b>'+p+'</b>'}));
  protections(f,SEASON).forEach(p=>items.push({i:p.t,c:'t',t:'<b>Фунгицид</b>'+(p.voided?' — смыт дождём '+num(p.mm,0)+' мм':', защита до '+fmt(p.until))}));
  S.marks.filter(m=>m.fid===f.id).forEach(m=>items.push({i:m.i,c:m.result==='focus'?'f':'c',t:'<b>Осмотр: '+(m.result==='focus'?'очаг'+(m.disease?' — '+esc(m.disease):''):'чисто')+'</b>'+(m.tier?' · '+esc(m.tier):'')+(m.result==='focus'&&m.sev!=null?' · '+m.sev+' %':'')+(m.decision?' · '+esc(m.decision):'')+(m.text?'<br>'+esc(m.text):'')}));
  FOCI.filter(x=>x.fid===f.id).forEach(x=>items.push({i:x.i,c:'f',t:'<b>Очаг «'+esc(x.disease)+'»</b> в учёте хозяйства'}));
  S.tasks.filter(t=>t.fid===f.id&&t.status==='plan').forEach(t=>items.push({i:t.plan,c:'p',t:'<b>Обработка запланирована</b> · '+esc(t.product)+', '+esc(t.rate)}));
  items.sort((a,b)=>b.i-a.i);
  return '<h3>Паспорт поля</h3>'+pass+'<h3>Статус по дням сезона</h3>'+strip+'<div class="legend"><span>1 апреля</span><span data-style="margin-left:auto">31 мая</span></div>'+
    '<p class="hint">Цвета пересчитаны по нынешнему учёту, а не взяты из того, что система показывала в тот день.</p>'+
    '<h3>События</h3><ul class="events">'+(items.length?items.map(x=>'<li><span class="d">'+fmtS(x.i)+'</span><span class="dot '+x.c+'"></span><span>'+x.t+'</span></li>').join(''):'<li><span></span><span></span><span>Событий нет.</span></li>')+'</ul>';
}

/* ═════════ Map ═════════ */
function mapView(o){
  const pad=o.focus?140:28;let x0=1e9,y0=1e9,x1=-1e9,y1=-1e9;
  const ids=o.focus?[S.sel]:Object.keys(GEO);
  ids.forEach(id=>GEO[id].pts.forEach(([x,y])=>{x0=Math.min(x0,x);y0=Math.min(y0,y);x1=Math.max(x1,x);y1=Math.max(y1,y);}));
  x0=Math.max(0,x0-pad);y0=Math.max(0,y0-pad);x1=Math.min(MAPGEO.w,x1+pad);y1=Math.min(MAPGEO.h,y1+pad);
  return {x:x0,y:y0,w:x1-x0,h:y1-y0};
}
function mapSVG(o){
  const trip=o.trip||[],v=mapView(o),fs=Math.max(9,v.w/30),sec=MAPGEO.sector;
  let s='<svg class="map'+(o.small?' small':'')+'" viewBox="'+[v.x,v.y,v.w,v.h].map(n=>n.toFixed(1)).join(' ')+'" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Спутниковый снимок хозяйства с обводками 27 полей'+(o.focus?', приближено к '+S.sel:'')+'">';
  s+='<defs><pattern id="nonehatch" width="10" height="10" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="10" height="10" fill="rgba(255,255,255,.18)"/><rect width="4" height="10" fill="rgba(255,255,255,.45)"/></pattern></defs>';
  s+='<image href="farm-basemap.jpg" x="0" y="0" width="'+MAPGEO.w+'" height="'+MAPGEO.h+'" preserveAspectRatio="none"/><rect class="veil" x="0" y="0" width="'+MAPGEO.w+'" height="'+MAPGEO.h+'"/>';
  /* ERA5 sector: west and north edges of the 0.25° cell, and its grid node */
  s+='<line class="sector" x1="'+sec.west+'" x2="'+sec.west+'" y1="0" y2="'+MAPGEO.h+'"/><line class="sector" x1="0" x2="'+MAPGEO.w+'" y1="'+sec.north+'" y2="'+sec.north+'"/>';
  s+='<g class="era5"><circle cx="'+sec.node[0]+'" cy="'+sec.node[1]+'" r="'+(fs*.45)+'"/><line x1="'+(sec.node[0]-fs)+'" x2="'+(sec.node[0]+fs)+'" y1="'+sec.node[1]+'" y2="'+sec.node[1]+'"/><line x1="'+sec.node[0]+'" x2="'+sec.node[0]+'" y1="'+(sec.node[1]-fs)+'" y2="'+(sec.node[1]+fs)+'"/></g>';
  if(!o.small)s+='<text class="era5-l" x="'+(sec.node[0]-fs*1.2)+'" y="'+(sec.node[1]-fs*1.1)+'" font-size="'+(fs*.78)+'" text-anchor="end">узел ERA5 45,00° · 38,00°</text>';
  let labels='';
  FIELDS.forEach(f=>{const g=GEO[f.id];if(!g)return;const status=st(f.id);let cls=status,style='';
    if(o.layer==='ndvi'){const last=SAT.filter(p=>p.i<=S.D&&NDVI[f.id][p.i]!=null&&p.cloud<=20).slice(-1)[0];const val=last?NDVI[f.id][last.i]:.1,t=Math.max(0,Math.min(1,(val-.1)/.65));cls='ndvi';style=' data-style="fill:rgb('+Math.round(236-t*170)+','+Math.round(226-t*40)+','+Math.round(178-t*90)+')"';}
    const dim=o.dim&&!trip.includes(f.id);if(dim)cls='dim';
    const pts=g.pts.map(p=>p.join(',')).join(' ');
    s+='<polygon class="fp '+cls+(S.sel===f.id&&!o.dim?' sel':'')+'" points="'+pts+'" data-field="'+f.id+'"'+(o.dim?' data-goto="map"':' tabindex="0" role="button" aria-label="'+f.id+', '+LABEL[status]+', '+f.ha+' га"')+style+'><title>'+f.id+' · '+cap(f.crop)+' · '+f.ha+' га · '+LABEL[status]+'</title></polygon>';
    if(!dim||o.small)labels+='<text class="fl'+(dim?' dim':'')+'" x="'+g.cx.toFixed(1)+'" y="'+(g.cy+fs*.35).toFixed(1)+'" font-size="'+(o.focus&&f.id!==S.sel?fs*.6:fs*(f.ha<20?.55:.8)).toFixed(1)+'">'+f.id+'</text>';
    if(f.sensor)labels+='<circle class="node" cx="'+(g.cx+fs*1.1).toFixed(1)+'" cy="'+(g.cy-fs*.9).toFixed(1)+'" r="'+(fs*.32).toFixed(1)+'"><title>Узел на поле '+f.id+'</title></circle>';});
  if(trip.length>1)s+='<path class="route" d="M'+trip.map(id=>{const p=ptOf(id);return p.x.toFixed(1)+','+p.y.toFixed(1);}).join('L')+'"/>';
  s+=labels;
  trip.forEach((id,i)=>{const p=ptOf(id);s+='<circle class="stop" cx="'+(p.x-fs*1.1).toFixed(1)+'" cy="'+(p.y-fs*.9).toFixed(1)+'" r="'+(fs*.62).toFixed(1)+'"/><text class="stop-t" x="'+(p.x-fs*1.1).toFixed(1)+'" y="'+(p.y-fs*.9+fs*.25).toFixed(1)+'" font-size="'+(fs*.7).toFixed(1)+'">'+(i+1)+'</text>';});
  /* scale bar */
  const km=v.w*MAPGEO.kmpx>6?2:v.w*MAPGEO.kmpx>2.5?1:.5,len=km/MAPGEO.kmpx,sx=v.x+fs*.8,sy=v.y+v.h-fs*.9;
  s+='<g class="scale"><rect x="'+(sx-fs*.4)+'" y="'+(sy-fs*1.15)+'" width="'+(len+fs*.8)+'" height="'+(fs*1.6)+'" rx="'+(fs*.3)+'"/><line x1="'+sx+'" x2="'+(sx+len)+'" y1="'+sy+'" y2="'+sy+'"/><text x="'+sx+'" y="'+(sy-fs*.35)+'" font-size="'+(fs*.55)+'">'+(km<1?'500 м':km+' км')+'</text></g>';
  return s+'</svg>';
}
function viewMap(){
  const legend=(S.mapLayer==='ndvi'
    ?'<span><i data-style="background:rgb(236,226,178)"></i>NDVI 0,1</span><span><i data-style="background:rgb(141,198,118)"></i>0,45</span><span><i data-style="background:rgb(66,186,88)"></i>0,75 · последний чистый снимок</span>'
    :'<span><i data-style="background:#E5534B"></i>Риск</span><span><i data-style="background:#F0A93B"></i>Проверить</span><span><i data-style="background:#5CC27E"></i>Норма</span><span><i class="nh"></i>Нет правил</span>')+
    '<span><i class="nd"></i>узел на поле</span><span><i class="sx"></i>граница сектора ERA5</span>';
  return '<section class="view" aria-labelledby="h-map">'+timelineCard()+
    '<div class="head"><h1 id="h-map">Карта полей</h1><div class="seg" role="group" aria-label="Слой карты"><button type="button" data-layer="status" aria-pressed="'+(S.mapLayer==='status')+'">Статус риска</button><button type="button" data-layer="ndvi" aria-pressed="'+(S.mapLayer==='ndvi')+'">NDVI</button></div>'+
    '<div class="seg" role="group" aria-label="Масштаб"><button type="button" data-act="zoom" data-field="farm" aria-pressed="'+(S.mapZoom!=='field')+'">Всё хозяйство</button><button type="button" data-act="zoom" data-field="field" aria-pressed="'+(S.mapZoom==='field')+'">К полю '+S.sel+'</button></div></div>'+
    '<div class="map-wrap"><div class="map-card">'+mapSVG({layer:S.mapLayer,trip:S.trip,focus:S.mapZoom==='field'})+'<div class="map-legend">'+legend+'</div>'+
      '<p class="map-note">Все 27 полей лежат в одной ячейке ERA5 0,25° (узел 45,00° с. ш., 38,00° в. д.): погода для них общая. Обводки приблизительные — обведены по скриншоту, площади по обводкам. Снимок: '+esc(MAPGEO.attribution)+'.</p></div>'+
    '<div id="fpane">'+fieldHTML(S.sel,true)+'</div></div></section>';
}

/* ═════════ Season matrix ═════════ */
function viewSeason(){
  const ids=sortedIds(S.D);
  let h='<table class="mx" aria-label="Статус полей по дням"><thead><tr><th class="f" scope="col">Поле</th>';
  for(let i=MIN_D;i<SEASON;i++){const d=dt(i).getUTCDate();h+='<th scope="col">'+(i===MIN_D||d===1?fmtS(i):d%5===0?d:'')+'</th>';}
  h+='</tr></thead><tbody>';
  ids.forEach(id=>{const f=fieldById(id);
    h+='<tr'+(id===S.sel?' class="sel"':'')+'><td class="f">'+id+'<small>'+(f.crop==='озимый ячмень'?'ячмень':f.crop==='кукуруза'?'кукуруза':'пшеница')+'</small></td>';
    for(let i=MIN_D;i<SEASON;i++){
      if(i>DATA_END){h+='<td class="c nd" title="'+fmtS(i)+': погоды ещё нет"></td>';continue;}
      const s=st(id,i),m=S.marks.find(x=>x.fid===id&&x.i===i);
      h+='<td class="c '+s+(i===S.D?' cur':'')+'" data-cell="'+id+'|'+i+'" title="'+id+' · '+fmtS(i)+': '+LABEL[s]+(m?' · осмотр: '+(m.result==='focus'?'очаг':'чисто'):'')+'">'+(m?'<span class="m'+(m.result==='focus'?' f':'')+'"></span>':'')+'</td>';}
    h+='</tr>';});
  h+='<tr><td class="f" data-style="font-size:11.5px;color:var(--ink-2)">в риске</td>';
  for(let i=MIN_D;i<SEASON;i++)h+='<td class="sum">'+(i<=DATA_END?(counts(i).risk||''):'')+'</td>';
  h+='</tr></tbody></table>';
  return '<section class="view" aria-labelledby="h-s"><div class="head"><div><div class="eyebrow">Весь сезон</div><h1 id="h-s">Поля × дни</h1><p class="lede">Клетка — статус поля в этот день по 10 суткам до него, кружок — отметка осмотра. Нажмите клетку, чтобы открыть поле на эту дату. Видно волны риска после влажных ночей 24 апреля, 2 и 16 мая.</p></div></div>'+
    '<div class="card mx-wrap">'+h+'<div class="legend" data-style="margin-top:12px"><span><i data-style="background:var(--red)"></i>Риск</span><span><i data-style="background:var(--amber)"></i>Проверить</span><span><i data-style="background:#A9D5B8"></i>Норма</span><span><i data-style="background:var(--soft-2)"></i>Нет правил</span><span><i data-style="outline:2px solid var(--ink)"></i>выбранная дата</span><span>Пересчёт по сегодняшнему учёту</span></div></div></section>';
}

/* ═════════ Trip ═════════ */
function routeStats(ids){let km=0;for(let k=1;k<ids.length;k++)km+=dist(ids[k-1],ids[k]);return {km,min:Math.round(km/30*60+ids.length*S.minPerField)};}
function optimise(ids){
  const out=[];let cur=null;
  const key=id=>{const e=ev(id);return e.due!=null?e.due:9e9;};
  const take=(list,byDue)=>{while(list.length){const md=byDue?Math.min.apply(null,list.map(key)):0;let bi=-1,bd=1e9;list.forEach((id,i)=>{if(byDue&&key(id)!==md)return;const d=cur?dist(cur,id):0;if(d<bd){bd=d;bi=i;}});const id=list.splice(bi,1)[0];out.push(id);cur=id;}};
  take(ids.filter(id=>st(id)==='risk'),true);take(ids.filter(id=>st(id)!=='risk'),false);return out;
}
function autoTrip(){
  let ids=optimise(Array.from(new Set(S.trip.filter(id=>st(id)!=='normal'&&st(id)!=='none').concat(FIELDS.filter(f=>st(f.id)==='risk').map(f=>f.id)))));
  FIELDS.filter(f=>st(f.id)==='check'&&!ids.includes(f.id)).forEach(f=>{const base=routeStats(ids).km,cand=optimise(ids.concat(f.id));if(routeStats(cand).km-base<=3)ids=cand;});
  return ids;
}
function yandexLink(ids){return 'https://yandex.ru/maps/?rtext='+encodeURIComponent('~'+ids.map(id=>llOf(id).join(',')).join('~'))+'&rtt=auto';}
function viewTrips(){
  const ids=S.trip,r=routeStats(ids),h=Math.floor(r.min/60),m=r.min%60;
  const sug=sortedIds(S.D).filter(id=>['risk','check'].includes(st(id))&&!ids.includes(id));
  const stops=ids.length?'<ol class="stops">'+ids.map((id,i)=>{const f=fieldById(id),e=ev(id),w=whenText(e,S.D),done=S.done.includes(id);
    return '<li class="stop'+(done?' done':'')+'"><span class="n">'+(done?'✓':i+1)+'</span><div><b>'+id+' <span class="chip '+e.status+'">'+LABEL[e.status]+'</span></b><span>'+cap(f.crop)+' · '+f.ha+' га · '+w.lbl.toLowerCase()+' '+w.main+'</span></div>'+
    '<div class="acts"><button type="button" class="icon-btn" data-move="'+id+'" data-dir="-1" aria-label="Поднять '+id+'"'+(i===0?' disabled':'')+'>'+icon('up')+'</button><button type="button" class="icon-btn" data-move="'+id+'" data-dir="1" aria-label="Опустить '+id+'"'+(i===ids.length-1?' disabled':'')+'>'+icon('down')+'</button>'+
    '<button type="button" class="btn btn-sm '+(done?'btn-soft':'btn-secondary')+'" data-act="inspect" data-field="'+id+'">'+(done?'Осмотрено':'Осмотр')+'</button><button type="button" class="icon-btn" data-remove="'+id+'" aria-label="Убрать '+id+' из выезда">'+icon('x')+'</button></div></li>';}).join('')+'</ol>':'<div class="empty">В выезде пока нет полей. Нажмите «Собрать автоматически».</div>';
  return '<section class="view" aria-labelledby="h-t"><div class="head"><div><div class="eyebrow">План на '+fmt(S.D)+'</div><h1 id="h-t">Выезд</h1><p class="lede">Сначала «Риск» по сроку, затем ближайшие. «Проверить» добавляется, если крюк не больше 3 км. В Яндекс Картах маршрут начнётся от вашего местоположения.</p></div>'+
    '<div class="acts">'+fieldJournalLink()+'<button type="button" class="btn btn-secondary" data-act="trip-auto">'+icon('bolt')+'Собрать автоматически</button><button type="button" class="btn btn-secondary" data-act="copy-route"'+(ids.length?'':' disabled')+'>'+icon('copy')+'Маршрутный лист</button>'+(ids.length?'<a class="btn btn-primary" href="'+yandexLink(ids)+'" target="_blank" rel="noopener">'+icon('ext')+'Открыть в Яндекс Картах</a>':'')+'</div></div>'+
    '<div class="two"><div data-style="display:grid;gap:16px;min-width:0"><div class="card block">'+
      '<div class="stats"><div class="stat"><span>Полей</span><strong>'+ids.length+'</strong><small>'+ids.reduce((a,id)=>a+fieldById(id).ha,0)+' га</small></div><div class="stat"><span>Путь, примерно</span><strong>'+num(r.km,1)+' км</strong><small>между полями, ×1,3 на дороги</small></div><div class="stat"><span>Время</span><strong>'+(ids.length?(h?h+' ч ':'')+m+' мин':'—')+'</strong><small>30 км/ч + осмотр</small></div></div>'+
      '<label class="fld" for="mpf">Минут на осмотр поля — обход конвертом, 5–10 точек<span class="range-row"><input id="mpf" type="range" min="20" max="90" step="5" value="'+S.minPerField+'"><span id="mpf-v">'+S.minPerField+' мин</span></span></label>'+stops+'</div>'+
      tasksCard()+
      (sug.length?'<div class="card block"><h3>Ещё в очереди</h3>'+sug.map(id=>{const e=ev(id),w=whenText(e,S.D);return '<div class="sug"><div><b>'+id+'</b> <span class="chip '+e.status+'">'+LABEL[e.status]+'</span> <span>'+w.lbl.toLowerCase()+' '+w.main+'</span></div><button type="button" class="btn btn-sm btn-soft" data-act="toggle-trip" data-field="'+id+'">'+icon('plus')+'В выезд</button></div>';}).join('')+'</div>':'')+
    '</div><div class="map-card">'+mapSVG({trip:ids,dim:true,small:true})+'</div></div></section>';
}
function tasksCard(){
  const pl=S.tasks.filter(t=>t.status==='plan').sort((a,b)=>a.plan-b.plan),dn=S.tasks.filter(t=>t.status==='done').sort((a,b)=>b.done-a.done);
  const w=sprayWindows();
  return '<div class="card block" id="tasks"><div class="block-head" data-style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;align-items:baseline"><h3>Обработки</h3><span class="hint">окна: '+(w.length?w.slice(0,2).map(winText).join('; '):'нет')+'</span></div>'+
    (pl.length?'<ul class="stops">'+pl.map(t=>{const f=fieldById(t.fid),bad=!sprayBlocks().some(b=>b.i===t.plan&&b.ok)&&t.plan<=FC[FC.length-1].i;
      return '<li class="stop task"><span class="n s">'+icon('spray')+'</span><div><b>'+t.fid+' <span class="tag g">'+esc(t.product)+'</span></b><span>'+cap(f.crop)+' · '+f.ha+' га · '+esc(t.rate)+' · из: '+esc(t.src)+(bad?' · <span data-style="color:var(--risk-ink);font-weight:700">в этот день окна нет</span>':'')+'</span></div>'+
      '<div class="acts"><label class="sr" for="td-'+t.id+'">Дата обработки '+t.fid+'</label><input class="tdate" id="td-'+t.id+'" type="date" min="'+iso(TODAY)+'" max="'+iso(TODAY+14)+'" value="'+iso(t.plan)+'" data-tdate="'+t.id+'">'+
      '<button type="button" class="btn btn-sm btn-primary" data-act="task-done" data-field="'+t.id+'">Внесено</button><button type="button" class="icon-btn" data-act="task-cancel" data-field="'+t.id+'" aria-label="Отменить обработку '+t.fid+'">'+icon('x')+'</button></div></li>';}).join('')+'</ul>'
     :'<p class="hint">Запланированных обработок нет. Их создают из осмотра (решение «обработать») или кнопкой «Обработка» в карточке поля.</p>')+
    (dn.length?'<p class="hint">Внесено: '+dn.slice(0,4).map(t=>t.fid+' '+fmtS(t.done)).join(', ')+' — защита уже в расчёте.</p>':'')+'</div>';
}
function routeText(){const r=routeStats(S.trip);return 'Выезд на '+fmt(S.D)+' 2024 · '+S.trip.length+' '+plural(S.trip.length,'поле','поля','полей')+' · ~'+num(r.km,1)+' км · ~'+num(r.min/60,1)+' ч\n'+S.trip.map((id,i)=>{const f=fieldById(id),e=ev(id),w=whenText(e,S.D),ll=llOf(id);return (i+1)+'. '+id+' — '+f.crop+', '+f.ha+' га — '+LABEL[e.status]+', '+w.lbl.toLowerCase()+' '+w.main+'\n   '+(e.reasons[0]?e.reasons[0].t.replace(/<[^>]+>/g,''):'')+'\n   '+ll.join(', ');}).join('\n');}

/* ═════════ Journal ═════════ */
function alarming(m){return m.result==='focus'&&(m.decision==='обработать'||(m.sev||0)>=10)||m.cond==='плохое';}
function viewJournal(){
  const flt=m=>S.jf==='all'||(S.jf==='alarm'?alarming(m):m.result===S.jf);
  const list=S.marks.slice().sort((a,b)=>b.i-a.i).filter(flt),foc=S.marks.filter(m=>m.result==='focus').length,al=S.marks.filter(alarming).length;
  const sig=FIELDS.filter(f=>['risk','check'].includes(st(f.id))),marked=sig.filter(f=>S.marks.some(m=>m.fid===f.id&&S.D-m.i<=14&&m.i<=S.D));
  return '<section class="view" aria-labelledby="h-j"><div class="head"><div><div class="eyebrow">История примера</div><h1 id="h-j">Журнал осмотров</h1><p class="lede">Тревожные — очаг с решением «обработать», развитие болезни от 10 % или плохое состояние посева. Осмотры пока не дообучают модель.</p></div>'+fieldJournalLink()+
    '<div class="acts"><div class="seg" role="group" aria-label="Фильтр журнала"><button type="button" data-jf="all" aria-pressed="'+(S.jf==='all')+'">Все · '+S.marks.length+'</button><button type="button" data-jf="alarm" aria-pressed="'+(S.jf==='alarm')+'">Тревожные · '+al+'</button><button type="button" data-jf="focus" aria-pressed="'+(S.jf==='focus')+'">Очаги · '+foc+'</button><button type="button" data-jf="clean" aria-pressed="'+(S.jf==='clean')+'">Чисто · '+(S.marks.length-foc)+'</button></div></div></div>'+
    '<div class="stats" data-style="max-width:820px"><div class="stat"><span>Сигналов на '+fmtS(S.D)+'</span><strong>'+sig.length+'</strong><small>риск и проверить</small></div><div class="stat"><span>С отметкой за 14 дней</span><strong>'+marked.length+'</strong><small>'+(sig.length?Math.round(marked.length/sig.length*100):0)+' % — метрика пилота</small></div><div class="stat"><span>Без отметки</span><strong>'+(sig.length-marked.length)+'</strong><small>'+(sig.filter(f=>!marked.includes(f)).map(f=>f.id).join(', ')||'—')+'</small></div></div>'+
    '<div class="card">'+(list.length?'<div class="table-wrap"><table class="t"><thead><tr><th>Дата</th><th>Поле</th><th>Итог</th><th>Фаза · состояние</th><th>Ярус · распр. · развитие</th><th>Решение</th><th>Комментарий</th><th>Статус на '+fmtS(S.D)+'</th></tr></thead><tbody>'+
    list.map(m=>{const s=st(m.fid);return '<tr'+(alarming(m)?' class="alarm"':'')+'><td>'+fmt(m.i)+'</td><td><button type="button" class="link" data-act="open-today" data-field="'+m.fid+'">'+m.fid+'</button></td><td><span class="tag '+(m.result==='focus'?'r':'g')+'">'+(m.result==='focus'?'очаг':'чисто')+'</span>'+(m.disease?'<br><span class="hint">'+esc(m.disease)+'</span>':'')+'</td><td>'+esc(m.phaseObs||'—')+(m.cond?' · '+esc(m.cond):'')+'</td><td>'+esc(m.tier||'—')+(m.result==='focus'?' · '+(m.inc!=null?m.inc+' %':'—')+' · '+(m.sev!=null?m.sev+' %':'—'):'')+'</td><td>'+esc(m.decision||'—')+'</td><td data-style="min-width:200px">'+esc(m.text||'—')+'</td><td><span class="chip '+s+'">'+LABEL[s]+'</span></td></tr>';}).join('')+
    '</tbody></table></div>':'<div class="empty">Записей нет'+(S.jf!=='all'?' с этим фильтром':'')+'. Отметьте осмотр в карточке поля.</div>')+'</div></section>';
}

/* ═════════ Data & automation ═════════ */
const HEADER='тип;обводка;поле_в_учёте;культура;дата_сева;фаза;дата_фазы;дата;препарат;вид;болезнь;широта;долгота';
const SAMPLE=HEADER+'\nполе;R11;ПРИМЕР · поле 11;озимая пшеница;2023-10-12;флаговый лист;2024-05-08;;;;;;\nобработка;R11;;;;;;2024-05-19;ПРИМЕР · фунгицид;фунгицид;;;\nобработка;R09;;;;;;2024-05-14;ПРИМЕР · фунгицид;фунгицид;;;\nочаг;R03;;;;;;2023-06-12;;;Бурая ржавчина;45.0740;37.9510\nполе;R44;ПРИМЕР · поле 44;озимая пшеница;2023-10-01;кущение;2024-04-02;;;;;;\nобработка;R02;;;;;;2024-02-31;ПРИМЕР · фунгицид;фунгицид;;;\nполе;R05;ПРИМЕР · поле 5;озимая пшеница;2023-10-01;трубка;2024-04-20;;;;;;';
function validate(text){
  const lines=text.replace(/\r/g,'').split('\n').filter(l=>l.trim());const res={ok:0,errors:[],kinds:{},rows:[]};
  if(!lines.length){res.errors.push({row:0,msg:'Файл пустой.'});return res;}
  const head=lines[0].split(';').map(s=>s.trim()),miss=HEADER.split(';').filter(c=>!head.includes(c));
  if(miss.length){res.errors.push({row:1,msg:'Нет колонок: '+miss.join(', ')+'. Скопируйте заголовок шаблона.'});return res;}
  const c=n=>head.indexOf(n);
  const okDate=s=>{if(!/^\d{4}-\d{2}-\d{2}$/.test(s))return false;const [y,m,d]=s.split('-').map(Number);const t=new Date(Date.UTC(y,m-1,d));return t.getUTCMonth()===m-1&&t.getUTCDate()===d;};
  lines.slice(1).forEach((l,k)=>{const v=l.split(';').map(s=>s.trim()),row=k+2,kind=v[c('тип')],fid=v[c('обводка')];
    if(!['поле','обработка','очаг'].includes(kind))return res.errors.push({row,msg:'Тип «'+esc(kind)+'» не знаю — нужен «поле», «обработка» или «очаг».'});
    if(!fieldById(fid))return res.errors.push({row,msg:'Обводки '+esc(fid)+' нет в хозяйстве (есть R01–R27).'});
    const dc=kind==='поле'?'дата_фазы':'дата';
    if(!okDate(v[c(dc)]))return res.errors.push({row,msg:'Дата «'+esc(v[c(dc)])+'» в колонке '+dc+' не существует. Формат ГГГГ-ММ-ДД.'});
    if(ixISO(v[c(dc)])>TODAY)return res.errors.push({row,msg:'Дата позже '+fmtS(TODAY)+' 2024. Для текущего сезона загрузите данные через «Очередь полей по риску и данные учёта» в /scouting.'});
    const ph=v[c('фаза')];
    if(kind==='поле'&&!RULES.phases.includes(ph))return res.errors.push({row,msg:'Фаза «'+esc(ph)+'» не из списка: '+RULES.phases.join(', ')+'.'});
    if(kind==='обработка'&&!/фунгицид/i.test(v[c('вид')]))return res.errors.push({row,msg:'В расчёт примера входят только обработки фунгицидом.'});
    if(kind==='очаг'&&!v[c('болезнь')])return res.errors.push({row,msg:'Для очага укажите болезнь.'});
    res.ok++;res.kinds[kind]=(res.kinds[kind]||0)+1;res.rows.push({kind,fid,phase:ph,date:v[c(dc)],what:v[c('вид')],disease:v[c('болезнь')]});});
  return res;
}
const AUTOS=[
  ['brief','Утренняя сводка в 6:30','Риск и проверить с окнами осмотра, что изменилось за сутки, свежесть погоды и снимков. Собирается из тех же причин, что в карточке поля.'],
  ['forecast','Прогноз заражения','За сутки до ночи с условиями заражения на поле без защиты — с ближайшим окном для опрыскивания.'],
  ['protect','Защита заканчивается','За 3 дня до конца защиты фунгицида и сразу, если обработку смыл дождь ≥ 10 мм.'],
  ['unmarked','Сигнал без отметки','Если поле 14 дней в «Риск» или «Проверить», а осмотра нет. Это метрика пилота.'],
  ['snapshot','Новый чистый снимок','Когда Sentinel-2 прошёл с облачностью ≤ 20 % и NDVI поля отстал от хозяйства.'],
  ['trip','Выезд собирается сам','Каждое утро в выезд попадают поля «Риск» и «Проверить» по пути. Вы только подтверждаете.']
];
function viewData(){
  const ri=S.imported,span=(a,b,cls)=>'<i class="'+(cls||'')+'" data-style="left:'+(a/SEASON*100).toFixed(2)+'%;width:'+((b-a+1)/SEASON*100).toFixed(2)+'%"></i>';
  const result=ri?'<div class="card block"><h3>Проверка «'+esc(ri.name)+'» до сохранения</h3><div>'+
    '<div class="check-row"><span class="ok">'+ri.res.ok+' '+plural(ri.res.ok,'строка готова','строки готовы','строк готовы')+'</span><span>'+Object.entries(ri.res.kinds).map(([k,v])=>k+': '+v).join(' · ')+'</span></div>'+
    ri.res.errors.map(e=>'<div class="check-row"><span class="bad">Строка '+e.row+'</span><span>'+e.msg+'</span></div>').join('')+'</div>'+
    '<div data-style="display:flex;gap:8px;flex-wrap:wrap"><button type="button" class="btn btn-primary" data-act="commit"'+(ri.res.ok?'':' disabled')+'>Сохранить '+ri.res.ok+' '+plural(ri.res.ok,'строку','строки','строк')+(ri.res.errors.length?' без ошибочных':'')+'</button><button type="button" class="btn btn-ghost" data-act="clear-import">Отменить</button></div></div>':'';
  return '<section class="view" aria-labelledby="h-d"><div class="head"><div><div class="eyebrow">Источники и автоматизация</div><h1 id="h-d">Данные хозяйства</h1><p class="lede">Что есть по датам, загрузка учёта и правила, по которым AgroPulse напоминает сам.</p></div></div>'+
  '<div class="card block"><h3>Покрытие сезона</h3><div class="cov">'+
    '<b>Погода ERA5</b><span class="lane">'+span(0,DATA_END)+'</span>'+
    '<b>Узел R07</b><span class="lane">'+span(NODE.from,DATA_END)+NODE.gaps.map(g=>span(g,g,'gap')).join('')+'</span>'+
    '<b>Sentinel-2</b><span class="lane">'+SAT.map(s=>span(s.i,s.i,s.cloud<=20?'':'c')).join('')+'</span>'+
    '<b>Обработки</b><span class="lane">'+Array.from(new Set(FIELDS.flatMap(f=>f.treat))).map(t=>span(t,t)).join('')+'</span>'+
    '<span></span><span class="ticks"><span>1 апр</span><span>15 апр</span><span>1 мая</span><span>15 мая</span><span>31 мая</span></span></div>'+
    '<p class="hint">ERA5 в примере — по '+fmt(DATA_END)+'. В реальной работе реанализ приходит с задержкой около 5 суток: утренняя очередь опирается на погоду, известную к этому дню. Красный — узел не передавал данные, серый — снимок закрыт облаками.</p></div>'+
  '<div class="two"><div data-style="display:grid;gap:16px;min-width:0"><div class="card block"><h3>Загрузить выгрузку учёта</h3>'+
    '<label class="drop" id="drop" for="csv">'+icon('upload')+'<b>Перетащите CSV или выберите файл</b><p>Фазы, обработки и прошлые очаги. Разделитель — точка с запятой, даты ГГГГ-ММ-ДД. Строки проверяются до сохранения.</p><input id="csv" type="file" accept=".csv,text/csv" class="sr"></label>'+
    '<div data-style="display:flex;gap:8px;flex-wrap:wrap"><button type="button" class="btn btn-secondary" data-act="sample">Проверить пример с ошибками</button><button type="button" class="btn btn-ghost" data-act="copy-header">'+icon('copy')+'Заголовок шаблона</button></div></div>'+result+'</div>'+
    '<div class="card block"><h3>Автоматизация</h3><div>'+AUTOS.map(([k,t,d])=>'<div class="auto"><b>'+t+'</b><button type="button" class="switch" role="switch" aria-checked="'+!!S.autos[k]+'" data-act="auto" data-field="'+k+'" aria-label="'+t+'"></button><p>'+d+'</p></div>').join('')+'</div>'+
    '<p class="hint">В прототипе ничего не отправляется: так выглядит настройка. Текст сводки открывается кнопкой «Сводка» на экране очереди.</p>'+
    '<button type="button" class="btn btn-ghost" data-act="reset" data-style="justify-self:start">Сбросить пример к исходному</button></div></div></section>';
}
function bindDrop(){
  const drop=document.getElementById('drop'),inp=document.getElementById('csv');if(!drop)return;
  const read=file=>{if(!file)return;const fr=new FileReader();fr.onload=()=>{S.imported={name:file.name,res:validate(String(fr.result))};render();};fr.onerror=()=>toast('Не удалось прочитать файл. Сохраните его как CSV в UTF-8.');fr.readAsText(file,'utf-8');};
  inp.addEventListener('change',()=>read(inp.files[0]));
  ['dragenter','dragover'].forEach(t=>drop.addEventListener(t,e=>{e.preventDefault();drop.classList.add('over');}));
  ['dragleave','drop'].forEach(t=>drop.addEventListener(t,e=>{e.preventDefault();drop.classList.remove('over');}));
  drop.addEventListener('drop',e=>read(e.dataTransfer.files[0]));
}

/* ═════════ Drawer: inspection, brief, shortcuts ═════════ */
const $dr=document.getElementById('drawer'),$scrim=document.getElementById('scrim');
let drawerFor=null,lastFocus=null;
const drawerField=()=>drawerFor&&/^(inspect|plan):/.test(drawerFor)?drawerFor.split(':')[1]:null;
function openDrawer(html,kind){if(!drawerFor)lastFocus=document.activeElement;$dr.innerHTML=html;$dr.classList.add('on');$scrim.classList.add('on');$dr.setAttribute('aria-hidden','false');drawerFor=kind;const f=$dr.querySelector('.pick button,.dr-foot .btn-primary,button');setTimeout(()=>{if(f)f.focus();},60);}
function closeDrawer(){$dr.classList.remove('on');$scrim.classList.remove('on');$dr.setAttribute('aria-hidden','true');drawerFor=null;if(lastFocus&&document.body.contains(lastFocus))lastFocus.focus({preventScroll:true});}
function inspectHTML(id){
  const f=fieldById(id),e=ev(id),d=S.draft[id],objs=OBJECTS[f.crop]||OBJECTS['озимая пшеница'];
  const seg=(key,opts,label)=>'<div class="fld">'+label+'<div class="seg" role="group" aria-label="'+label+'">'+opts.map(t=>'<button type="button" data-dset="'+key+'" data-v="'+t+'" aria-pressed="'+(d[key]===t)+'">'+t+'</button>').join('')+'</div></div>';
  const rng=(key,label,max,hint)=>'<label class="fld" for="'+key+'">'+label+'<span class="range-row"><input id="'+key+'" type="range" min="0" max="'+max+'" step="1" value="'+d[key]+'" data-d="'+key+'"><span id="'+key+'-v">'+d[key]+' %</span></span><span class="hint">'+hint+'</span></label>';
  return '<div class="dr-head"><div data-style="flex:1"><div class="eyebrow">'+id+' · '+cap(f.crop)+' · '+LABEL[e.status]+'</div><h2 id="dr-title">Отметить осмотр</h2></div><button type="button" class="icon-btn" data-act="close" aria-label="Закрыть">'+icon('x')+'</button></div>'+
  '<div class="dr-body"><div class="pick" role="group" aria-label="Итог осмотра">'+
    '<button type="button" class="clean" data-pick="clean" aria-pressed="'+(d.result==='clean')+'">'+icon('check')+'Чисто<span>Вредных объектов нет</span></button>'+
    '<button type="button" class="focus" data-pick="focus" aria-pressed="'+(d.result==='focus')+'">'+icon('spot')+'Нашёл очаг<span>Поле останется в «Проверить»</span></button></div>'+
    '<label class="fld" for="pho">Фаза на поле<select id="pho" data-d="phaseObs">'+RULES.phases.map(o=>'<option'+(d.phaseObs===o?' selected':'')+'>'+o+'</option>').join('')+'</select></label>'+
    seg('cond',['хорошее','среднее','плохое'],'Состояние посева')+
    (d.phaseObs!==phaseAt(f,d.date)?'<p class="hint">В учёте на эту дату — «'+(phaseAt(f,d.date)||'не указана')+'». Сохраним фазу с поля в учёт.</p>':'')+
    (d.result==='focus'?'<label class="fld" for="obj">Вредный объект<select id="obj" data-d="obj"><optgroup label="Частые для культуры">'+objs.slice(0,3).map(o=>'<option'+(d.obj===o?' selected':'')+'>'+o+'</option>').join('')+'</optgroup><optgroup label="Остальные">'+objs.slice(3).map(o=>'<option'+(d.obj===o?' selected':'')+'>'+o+'</option>').join('')+'</optgroup></select></label>'+
      seg('tier',['флаговый','F-1','F-2','ниже'],'Ярус')+
      rng('inc','Распространённость',100,'Доля растений с признаками на точках обхода')+
      rng('sev','Развитие болезни',50,'Средняя площадь поражения листа')+
      seg('decision',['наблюдать','обработать'],'Решение')+
      (d.decision==='обработать'?planFields(d):''):
     d.result==='clean'?seg('ctier',['флаговый','флаговый и F-1','до F-2'],'Осмотрены ярусы'):'')+
    '<label class="fld" for="mdate">Дата осмотра<input id="mdate" type="date" min="'+iso(MIN_D)+'" max="'+iso(TODAY)+'" value="'+iso(d.date)+'" data-d="date"></label>'+
    '<label class="fld" for="cm">Комментарий<textarea id="cm" data-d="text" placeholder="Где нашли, точки обхода, что с соседними участками">'+esc(d.text)+'</textarea></label>'+
    '<div class="fld">Фото и фактические наблюдения'+fieldJournalLink(id)+'</div>'+
    (e.from&&S.D<e.from?'<div class="warn">'+icon('info')+'<span>Симптомы после влажной ночи ожидаются не раньше '+fmt(e.from)+'. «Чисто» до этой даты ещё не значит, что заражения нет.</span></div>':'')+
  '</div>'+
  '<div class="dr-foot">'+(d.result?'<button type="button" class="btn btn-primary" data-act="save-mark" data-field="'+id+'">'+(d.decision==='обработать'&&d.result==='focus'?'Сохранить и запланировать':'Сохранить и к следующему')+'</button>':'<span class="hint" data-style="flex:1">Выберите итог осмотра, чтобы сохранить.</span>')+'<button type="button" class="btn btn-ghost" data-act="close">Отмена</button></div>';
}
function planFields(d){
  const w=sprayWindows();
  return '<div class="plan-f"><label class="fld" for="prod">Препарат (действующее вещество)<select id="prod" data-d="product">'+PRODUCTS.map(o=>'<option'+(d.product===o?' selected':'')+'>'+o+'</option>').join('')+'</select></label>'+
    '<div class="two-f"><label class="fld" for="rate">Норма<input id="rate" data-d="rate" value="'+esc(d.rate)+'"></label>'+
    '<label class="fld" for="pdate">Дата обработки<input id="pdate" type="date" min="'+iso(TODAY)+'" max="'+iso(TODAY+14)+'" value="'+iso(d.plan)+'" data-d="plan"></label></div>'+
    '<p class="hint">Окна для опрыскивания по прогнозу: '+(w.length?w.slice(0,3).map(winText).join('; '):'нет в ближайшие 3 дня')+'.</p></div>';
}
function planHTML(id){
  const f=fieldById(id),e=ev(id),d=S.draft[id],r=forecastRisk(f);
  return '<div class="dr-head"><div data-style="flex:1"><div class="eyebrow">'+id+' · '+cap(f.crop)+' · '+LABEL[e.status]+'</div><h2 id="dr-title">Запланировать обработку</h2></div><button type="button" class="icon-btn" data-act="close" aria-label="Закрыть">'+icon('x')+'</button></div>'+
  '<div class="dr-body">'+(r?'<div class="fcbox">'+icon('cloud')+'<div><b>'+fmt(r.i)+': '+r.h+' ч влажности при '+r.t+' °C</b><span>Обработка до этой даты закроет ночь с условиями для '+r.dzs.map(z=>z.gen).join(' и ')+'.</span></div></div>':'')+
    planFields(d)+sprayGrid()+
    '<p class="hint">После внесения отметьте задачу выполненной в разделе «Выезд» — защита на 14 дней появится в расчёте сама.</p></div>'+
  '<div class="dr-foot"><button type="button" class="btn btn-primary" data-act="save-plan" data-field="'+id+'">'+icon('spray')+'Запланировать</button><button type="button" class="btn btn-ghost" data-act="close">Отмена</button></div>';
}
function newDraft(id){const f=fieldById(id),w=sprayWindows()[0];return {result:null,obj:(OBJECTS[f.crop]||OBJECTS['озимая пшеница'])[0],tier:'флаговый',ctier:'флаговый и F-1',inc:10,sev:5,decision:'наблюдать',product:PRODUCTS[0],rate:'0,5 л/га',plan:w?w.i:TODAY+1,phaseObs:phaseAt(f,Math.min(S.D,TODAY))||'кущение',cond:'хорошее',text:'',date:Math.min(S.D,TODAY)};}
function openPlan(id){if(!fieldById(id)||st(id)==='none')return;S.sel=id;if(S.view==='today'||S.view==='map')render({resetField:true});if(!S.draft[id])S.draft[id]=newDraft(id);openDrawer(planHTML(id),'plan:'+id);}
function addTask(id,d,src){const t={id:Date.now()+Math.floor(Math.random()*1000),fid:id,product:d.product,rate:d.rate,plan:d.plan,status:'plan',src};S.tasks.push(t);return t;}
function openInspect(id,preset){
  if(!fieldById(id)||st(id)==='none')return;
  S.sel=id;if(S.view==='today'||S.view==='map')render({resetField:true});
  const d=S.draft[id]||(S.draft[id]=newDraft(id));
  if(preset)d.result=preset;
  openDrawer(inspectHTML(id),'inspect:'+id);
}
function briefText(){
  const D=S.D,ids=sortedIds(D),ch=changes(D),up=upcoming(D);
  const line=id=>{const e=ev(id),w=whenText(e,D);return '• '+id+' ('+fieldById(id).crop+') — '+w.lbl.toLowerCase()+' '+w.main+'. '+(e.reasons[0]?e.reasons[0].t.replace(/<[^>]+>/g,''):'');};
  const r=ids.filter(id=>st(id)==='risk'),c=ids.filter(id=>st(id)==='check'),lc=SAT.filter(s=>s.i<=D&&s.cloud<=20).slice(-1)[0];
  return 'AgroPulse · сводка на '+fmt(D)+' 2024 (данные-пример)\n\nРиск — '+r.length+':\n'+(r.map(line).join('\n')||'• нет')+'\n\nПроверить — '+c.length+':\n'+(c.map(line).join('\n')||'• нет')+
    '\n\nСо вчера: '+(ch.length?ch.map(x=>x.id+' '+LABEL[x.from]+' → '+LABEL[x.to]).join(', '):'без изменений')+'\nСкоро: '+(up.length?up.slice(0,4).map(x=>fmtS(x.i)+' — '+x.t).join('; '):'ничего')+
    (D===TODAY?'\nПрогноз: '+(FIELDS.map(f=>({f,r:st(f.id)!=='risk'&&forecastRisk(f)})).filter(x=>x.r&&!x.r.plan).map(x=>x.f.id+' '+fmtS(x.r.i)).join(', ')||'новых условий заражения нет')+'. Окно для опрыскивания: '+winText(sprayWindows()[0])+'.'+
      (S.tasks.some(t=>t.status==='plan')?'\nОбработки: '+S.tasks.filter(t=>t.status==='plan').map(t=>t.fid+' '+fmtS(t.plan)).join(', ')+'.':''):'')+
    '\nДанные: ERA5 по '+fmt(DATA_END)+', чистый снимок '+(lc?fmt(lc.i):'—')+', узел R07 на связи.\nСтатус — «осмотреть первым», не диагноз.';
}
function openBrief(){openDrawer('<div class="dr-head"><div data-style="flex:1"><div class="eyebrow">Автоматизация · утро</div><h2 id="dr-title">Сводка на '+fmt(S.D)+'</h2></div><button type="button" class="icon-btn" data-act="close" aria-label="Закрыть">'+icon('x')+'</button></div><div class="dr-body"><p class="hint">Такой текст AgroPulse отправит в 6:30, если включить «Утреннюю сводку» в разделе «Данные». Скопируйте его в Telegram или почту.</p><div class="brief">'+esc(briefText())+'</div></div><div class="dr-foot"><button type="button" class="btn btn-primary" data-act="copy-brief">'+icon('copy')+'Скопировать</button><button type="button" class="btn btn-ghost" data-act="close">Закрыть</button></div>','brief');}
function openKeys(){openDrawer('<div class="dr-head"><h2 id="dr-title" data-style="flex:1">Клавиши</h2><button type="button" class="icon-btn" data-act="close" aria-label="Закрыть">'+icon('x')+'</button></div><div class="dr-body"><dl class="kv">'+
  [['Ctrl K или /','поле, дата или команда'],['J / K','следующее / предыдущее поле'],['I','отметить осмотр'],['← / →','день назад / вперёд'],['Shift + ← / →','неделя'],['T','вернуться к '+fmt(TODAY)],['P','проиграть сезон'],['1–6','разделы'],['?','эта справка'],['Esc','закрыть']].map(([k,v])=>'<dt><kbd>'+k+'</kbd></dt><dd>'+v+'</dd>').join('')+'</dl></div>','keys');}

/* ═════════ Command palette ═════════ */
const $pal=document.getElementById('pal'),$pq=document.getElementById('pal-q'),$pl=document.getElementById('pal-list');
let palItems=[],palIdx=0;
function parseDate(q){const m=q.match(/(\d{1,2})\s*(?:[.\/-]\s*0?([45])\b|\s*(апр|ма[яй]))/i);if(!m)return null;const mm=m[2]?+m[2]:/апр/i.test(m[3])?4:5;const i=ix(mm,+m[1]);return i>=MIN_D&&i<=DATA_END?i:-1;}
function palCommands(q){
  const out=[],ql=q.trim().toLowerCase();
  const fm=ql.match(/^r?\s?(\d{1,2})(?![\d.\/-]|\s*(?:апр|ма))/);
  const fid=fm?'R'+String(+fm[1]).padStart(2,'0'):null;
  if(fid&&fieldById(fid)){
    if(/чист/.test(ql))out.push({sec:'Поле',t:fid+': отметить «Чисто»',k:'осмотр',run:()=>{if(S.view!=='today')go('today');openInspect(fid,'clean');}});
    if(/очаг|наш/.test(ql))out.push({sec:'Поле',t:fid+': отметить «Нашёл очаг»',k:'осмотр',run:()=>{if(S.view!=='today')go('today');openInspect(fid,'focus');}});
    out.push({sec:'Поле',t:'Открыть '+fid+' — '+cap(fieldById(fid).crop),k:LABEL[st(fid)],run:()=>{if(S.view!=='today')go('today');selectField(fid,true);}});
    out.push({sec:'Поле',t:fid+(S.trip.includes(fid)?': убрать из выезда':': в выезд'),k:'выезд',run:()=>toggleTrip(fid)});
    out.push({sec:'Поле',t:fid+' на карте',k:'карта',run:()=>{S.sel=fid;go('map');}});
  }
  const di=parseDate(ql);
  if(di!=null)out.push(di<0?{sec:'Дата',t:'На эту дату расчёта нет',k:'есть '+fmtS(MIN_D)+' – '+fmtS(DATA_END),run:()=>{}}:{sec:'Дата',t:'Перейти к '+fmt(di),k:'пересчёт',run:()=>{if(S.view!=='today'&&S.view!=='map')go('today');setDate(di);}});
  if(!ql)sortedIds(S.D).slice(0,4).forEach(id=>out.push({sec:'Первые в очереди',t:id+' — '+cap(fieldById(id).crop),k:LABEL[st(id)],run:()=>{if(S.view!=='today')go('today');selectField(id,true);}}));
  const acts=[
    ['Собрать выезд автоматически','выезд',()=>{S.trip=autoTrip();persist();go('trips');toast('Выезд собран: '+S.trip.join(' → ')+'.');},'риск выезд собрать маршрут авто'],
    ['Утренняя сводка','сводка',openBrief,'сводка бриф утро telegram'],
    ['Вернуться к '+fmt(TODAY),'дата',()=>setDate(TODAY),'сегодня дата вернуться'],
    ['Проиграть сезон','шкала',()=>{if(S.view!=='today'&&S.view!=='map')go('today');togglePlay();},'сезон проиграть анимация'],
    ['Клавиши','справка',openKeys,'клавиши горячие помощь справка']
  ].concat(VIEWS.map(v=>['Раздел «'+v.label+'»','раздел',()=>go(v.id),v.label.toLowerCase()+' раздел']));
  if(!fid||!fieldById(fid))acts.forEach(([t,k,run,kw])=>{if(!ql||kw.includes(ql)||t.toLowerCase().includes(ql)||ql.split(/\s+/).some(w=>w.length>2&&kw.includes(w)))out.push({sec:'Команды',t,k,run});});
  return out;
}
function renderPal(){
  palItems=palCommands($pq.value);palIdx=Math.min(palIdx,Math.max(0,palItems.length-1));
  let sec='',h='';
  palItems.forEach((it,i)=>{if(it.sec!==sec){sec=it.sec;h+='<div class="pal-sec">'+sec+'</div>';}h+='<button type="button" class="pal-item" role="option" data-pi="'+i+'" aria-selected="'+(i===palIdx)+'">'+esc(it.t)+'<small>'+esc(it.k)+'</small></button>';});
  $pl.innerHTML=h||'<div class="empty">Ничего не нашлось. Попробуйте «R11», «12 мая» или «сводка».</div>';
  const a=$pl.querySelector('[aria-selected="true"]');if(a)a.scrollIntoView({block:'nearest'});
}
function openPal(){if(!drawerFor){const a=document.activeElement;lastFocus=a&&!a.closest('#pal')?a:null;}$pal.classList.add('on');$scrim.classList.add('on');$pq.value='';palIdx=0;renderPal();$pq.focus();}
function closePal(restore){$pal.classList.remove('on');if(!drawerFor)$scrim.classList.remove('on');if(restore!==false&&!drawerFor&&lastFocus&&document.body.contains(lastFocus))lastFocus.focus({preventScroll:true});}
function runPal(i){const it=palItems[i];if(!it)return;closePal(false);$pq.blur();it.run();}
$pq.addEventListener('input',()=>{palIdx=0;renderPal();});
$pq.addEventListener('keydown',e=>{
  if(e.key==='ArrowDown'){e.preventDefault();palIdx=Math.min(palItems.length-1,palIdx+1);renderPal();}
  else if(e.key==='ArrowUp'){e.preventDefault();palIdx=Math.max(0,palIdx-1);renderPal();}
  else if(e.key==='Enter'){e.preventDefault();runPal(palIdx);}
});
$pl.addEventListener('click',e=>{const b=e.target.closest('[data-pi]');if(b)runPal(+b.dataset.pi);});

/* ═════════ Toast & copy ═════════ */
let tt;
function toast(msg,undo){const el=document.getElementById('toast');el.innerHTML='<span>'+msg+'</span>'+(undo?'<button type="button" id="undo">Отменить</button>':'');el.classList.add('on');clearTimeout(tt);tt=setTimeout(()=>el.classList.remove('on'),undo?6500:3600);if(undo)document.getElementById('undo').onclick=()=>{undo();el.classList.remove('on');};}
function copy(text,ok){
  const fb=()=>{const el=document.getElementById('toast');clearTimeout(tt);el.innerHTML='<div data-style="display:grid;gap:8px;min-width:min(520px,80vw)"><span>Скопировать не вышло — текст выделен, нажмите Ctrl+C.</span><textarea id="copy-box" readonly rows="6" data-style="width:100%;border:0;border-radius:10px;padding:10px;font:600 12.5px/1.45 var(--sans);color:var(--ink);background:var(--paper)"></textarea><button type="button" id="copy-close" data-style="justify-self:end">Закрыть</button></div>';el.classList.add('on');const b=document.getElementById('copy-box');b.value=text;b.focus();b.select();document.getElementById('copy-close').onclick=()=>el.classList.remove('on');};
  try{navigator.clipboard.writeText(text).then(()=>toast(ok),fb);}catch(e){fb();}
}

/* ═════════ Actions ═════════ */
function selectField(id,focusRow){
  if(!fieldById(id))return;S.sel=id;
  if(S.filter!=='all'&&st(id)!==S.filter)S.filter='all';
  const g=st(id);if(S.collapsed[g]){S.collapsed[g]=false;persist();}
  render({reveal:true,resetField:true});
  if(focusRow){const r=document.querySelector('.qrow[data-field="'+id+'"]');if(r)r.focus({preventScroll:true});}
}
function toggleTrip(id){const had=S.trip.includes(id);S.trip=had?S.trip.filter(x=>x!==id):S.trip.concat(id);persist();render();toast(had?id+' убрано из выезда.':id+' в выезде · '+S.trip.length+' '+plural(S.trip.length,'поле','поля','полей')+'.');}
function nextField(dir){const ids=[...document.querySelectorAll('.qrow')].map(r=>r.dataset.field);if(!ids.length)return;const i=ids.indexOf(S.sel);selectField(ids[Math.max(0,Math.min(ids.length-1,i<0?0:i+dir))],true);}
function stopPlay(){if(S.playing){clearInterval(S.playing);S.playing=null;}}
function togglePlay(){
  if(S.playing){stopPlay();render();return;}
  if(S.D>=DATA_END)S.D=MIN_D;
  S.playing=setInterval(()=>{if(S.D>=DATA_END){stopPlay();render();toast('Сезон проигран до '+fmt(DATA_END)+'.');return;}S.D++;render({flip:true});},matchMedia('(prefers-reduced-motion: reduce)').matches?450:180);
  render();
}
function saveMark(id){
  const d=S.draft[id];if(!d||!d.result)return;
  const f=fieldById(id),before=st(id,TODAY);
  const m={fid:id,i:d.date,result:d.result,tier:d.result==='focus'?d.tier:d.ctier,text:(d.text||'').trim(),phaseObs:d.phaseObs,cond:d.cond};
  if(d.result==='focus'){m.disease=d.obj;m.inc=+d.inc;m.sev=+d.sev;m.decision=d.decision;}
  let phaseChange;
  if(d.phaseObs&&d.phaseObs!==phaseAt(f,d.date)){
    const o=S.overrides[id]=S.overrides[id]||{};
    o.phases=o.phases||[];phaseChange=[d.phaseObs,d.date];o.phases.push(phaseChange);applyOverrides();
  }
  let task=null;if(d.result==='focus'&&d.decision==='обработать')task=addTask(id,d,'осмотр '+fmtS(d.date));
  S.marks.push(m);const wasDone=S.done.includes(id);if(S.trip.includes(id)&&!wasDone)S.done.push(id);
  delete S.draft[id];bump();persist();closeDrawer();
  const after=st(id,TODAY);
  const next=S.view==='today'?sortedIds(S.D).find(x=>x!==id&&['risk','check'].includes(st(x))&&!S.marks.some(k=>k.fid===x&&S.D-k.i<=7&&k.i<=S.D)):null;
  if(next)S.sel=next;
  render({flip:true,resetField:true,reveal:true});
  toast('Отметка '+id+' сохранена'+(task?' · обработка '+fmtS(task.plan):'')+(after!==before?' · статус: '+LABEL[after]:'')+(next?' · дальше '+next:'')+'.',()=>{
    S.marks=S.marks.filter(mark=>mark!==m);
    if(task)S.tasks=S.tasks.filter(t=>t!==task);
    if(phaseChange){
      const o=S.overrides[id];o.phases=o.phases.filter(change=>change!==phaseChange);
      if(!o.phases.length)delete o.phases;
      if(!Object.keys(o).length)delete S.overrides[id];
      applyOverrides();
    }
    if(!wasDone)S.done=S.done.filter(x=>x!==id);
    S.sel=id;bump();persist();render();
  });
}
function savePlan(id){const d=S.draft[id];if(!d)return;const t=addTask(id,d,'план');persist();closeDrawer();bump();render();toast('Обработка '+id+' запланирована на '+fmt(t.plan)+'.',()=>{S.tasks=S.tasks.filter(x=>x!==t);persist();bump();render();});}
function confirmTreatment(tid){
  const task=S.tasks.find(t=>t.id===tid&&t.status==='plan');if(!task)return;
  const planned=iso(task.plan);
  openDrawer('<div class="dr-head"><h2 id="dr-title">Фактическое внесение · '+task.fid+'</h2></div>'+
    '<div class="dr-body"><p>Плановая дата '+fmt(task.plan)+'. Укажите день, когда обработку выполнили.</p>'+
    '<label class="fld" for="actual-treatment-date">Дата внесения<input id="actual-treatment-date" type="date" required min="'+iso(0)+'" max="'+iso(TODAY+14)+'"></label>'+
    '<p class="hint">Демонстрационный сезон 2024. Расчёт учитывает внесение начиная с указанной даты. Архив погоды доступен по '+fmt(TODAY)+'.</p></div>'+
    '<div class="dr-foot"><button type="button" class="btn btn-primary" data-act="confirm-task-done" data-field="'+tid+'">Подтвердить внесение</button>'+
    '<button type="button" class="btn btn-ghost" data-act="close">Отмена</button></div>','treatment');
}
function taskDone(tid){
  const input=document.getElementById('actual-treatment-date');
  const t=S.tasks.find(x=>x.id===tid&&x.status==='plan');if(!t||!input?.reportValidity())return;
  const day=ixISO(input.value);if(!Number.isInteger(day)||day<0||day>TODAY+14)return;
  t.status='done';t.done=day;const o=S.overrides[t.fid]=S.overrides[t.fid]||{};o.treat=o.treat||[];o.treat.push(day);
  applyOverrides();bump();persist();closeDrawer();render();
  const p=protections(fieldById(t.fid),TODAY).find(x=>x.t===day);toast('Внесение на '+t.fid+' отмечено '+fmt(day)+(p?' · защита до '+fmt(p.until):'')+'.');}
function commitImport(){
  const rows=S.imported.res.rows,before={};FIELDS.forEach(f=>before[f.id]=st(f.id,TODAY));
  rows.forEach(r=>{const i=ixISO(r.date);const o=S.overrides[r.fid]=S.overrides[r.fid]||{};
    if(r.kind==='поле'&&RULES.phases.includes(r.phase)){o.phases=o.phases||[];o.phases.push([r.phase,i]);}
    if(r.kind==='обработка'&&/фунгицид/i.test(r.what)){o.treat=o.treat||[];o.treat.push(i);}
    if(r.kind==='очаг'){o.foci=o.foci||[];o.foci.push({i,disease:r.disease});}});
  applyOverrides();bump();persist();S.imported=null;
  const ch=FIELDS.filter(f=>before[f.id]!==st(f.id,TODAY)).map(f=>f.id+': '+LABEL[before[f.id]]+' → '+LABEL[st(f.id,TODAY)]);
  render();toast(rows.length+' '+plural(rows.length,'строка сохранена','строки сохранены','строк сохранено')+'. '+(ch.length?'Статусы на '+fmtS(TODAY)+': '+ch.join(', ')+'.':'Статусы не изменились.'));
}
function refreshDrawer(focusSel){const id=drawerField();if(!id)return;$dr.innerHTML=drawerFor.indexOf('plan:')===0?planHTML(id):inspectHTML(id);const b=focusSel&&$dr.querySelector(focusSel);if(b)b.focus();}

document.addEventListener('click',e=>{
  const t=e.target.closest('button,[data-field],[data-nav],[data-cell],a');if(!t||t.closest('#pal'))return;
  const ds=t.dataset;
  if(ds.nav){go(ds.nav);return;}
  if(ds.cell){const [id,i]=ds.cell.split('|');S.D=+i;S.filter='all';go('today');selectField(id);return;}
  if(ds.filter){S.filter=ds.filter;const vis=sortedIds(S.D).filter(id=>S.filter==='all'||st(id)===S.filter);if(vis.length&&!vis.includes(S.sel))S.sel=vis[0];render({resetField:true});return;}
  if(ds.grp){S.collapsed[ds.grp]=!S.collapsed[ds.grp];persist();render();return;}
  if(ds.tab){S.tab=ds.tab;render({resetField:true});return;}
  if(ds.layer){S.mapLayer=ds.layer;render();return;}
  if(ds.jf){S.jf=ds.jf;render();return;}
  if(ds.pick){const id=drawerField();if(!id)return;S.draft[id].result=ds.pick;refreshDrawer('[data-pick="'+ds.pick+'"]');return;}
  if(ds.dset){const id=drawerField();if(!id)return;S.draft[id][ds.dset]=ds.v;refreshDrawer('[data-dset="'+ds.dset+'"][data-v="'+CSS.escape(ds.v)+'"]');return;}
  if(ds.move){const i=S.trip.indexOf(ds.move),j=i+Number(ds.dir);if(j<0||j>=S.trip.length)return;S.trip.splice(j,0,S.trip.splice(i,1)[0]);persist();render();return;}
  if(ds.remove){const b=S.trip.slice();S.trip=S.trip.filter(x=>x!==ds.remove);persist();render();toast(ds.remove+' убрано из выезда.',()=>{S.trip=b;persist();render();});return;}
  const a=ds.act;
  if(!a){
    if(ds.field&&t.classList.contains('qrow')){selectField(ds.field);return;}
    if(ds.field&&t.tagName.toLowerCase()==='polygon'){S.sel=ds.field;if(ds.goto)go('map');else render({resetField:true});return;}
    return;}
  switch(a){
    case 'select':selectField(ds.field,true);break;
    case 'prev':stopPlay();setDate(S.D-1);break;
    case 'next':stopPlay();setDate(S.D+1);break;
    case 'today':stopPlay();setDate(TODAY);break;
    case 'play':togglePlay();break;
    case 'inspect':openInspect(ds.field||S.sel);break;
    case 'field-journal':openFieldJournal(ds.field);break;
    case 'toggle-trip':toggleTrip(ds.field);break;
    case 'open-map':S.sel=ds.field;go('map');break;
    case 'open-today':S.filter='all';go('today');selectField(ds.field,true);break;
    case 'trip-auto':{const b=S.trip.slice();S.trip=autoTrip();persist();render();toast(S.trip.length?'Выезд: '+S.trip.join(' → ')+' · '+num(routeStats(S.trip).km,1)+' км.':'Полей «Риск» и «Проверить» нет — выезд пуст.',()=>{S.trip=b;persist();render();});break;}
    case 'zoom':S.mapZoom=ds.field;render();break;
    case 'brief':openBrief();break;
    case 'copy-brief':copy(briefText(),'Сводка скопирована.');break;
    case 'copy-route':copy(routeText(),'Маршрутный лист скопирован.');break;
    case 'copy-header':copy(HEADER,'Заголовок шаблона скопирован.');break;
    case 'close':closeDrawer();break;
    case 'save-mark':saveMark(ds.field);break;
    case 'plan':openPlan(ds.field);break;
    case 'save-plan':savePlan(ds.field);break;
    case 'task-done':confirmTreatment(+ds.field);break;
    case 'confirm-task-done':taskDone(+ds.field);break;
    case 'task-cancel':{const t=S.tasks.find(x=>x.id===+ds.field);S.tasks=S.tasks.filter(x=>x!==t);persist();bump();render();toast('Обработка '+t.fid+' отменена.',()=>{S.tasks.push(t);persist();bump();render();});break;}
    case 'goto-tasks':go('trips');setTimeout(()=>{const c=document.getElementById('tasks');if(c)c.scrollIntoView({block:'start',behavior:'smooth'});},30);break;
    case 'tl-toggle':S.tlCompact=!S.tlCompact;persist();render();break;
    case 'sample':S.imported={name:'пример_выгрузки.csv',res:validate(SAMPLE)};render();break;
    case 'clear-import':S.imported=null;render();break;
    case 'commit':commitImport();break;
    case 'auto':S.autos[ds.field]=!S.autos[ds.field];persist();render();toast((S.autos[ds.field]?'Включено':'Выключено')+': '+AUTOS.find(x=>x[0]===ds.field)[1].toLowerCase()+'.');break;
    case 'reset':try{localStorage.removeItem(KEY);}catch(err){}location.reload();break;
  }
});
document.addEventListener('input',e=>{
  const el=e.target;
  if(el.id==='mpf'){S.minPerField=+el.value;persist();const v=document.getElementById('mpf-v');if(v)v.textContent=el.value+' мин';return;}
  const k=el.dataset&&el.dataset.d,id=drawerField();if(!k||!id)return;const d=S.draft[id];
  if(k==='sev'||k==='inc'){d[k]=+el.value;const v=document.getElementById(k+'-v');if(v)v.textContent=el.value+' %';}
  else if(k==='plan'){const i=ixISO(el.value);if(!isNaN(i))d.plan=Math.max(TODAY,Math.min(TODAY+14,i));}
  else if(k==='date'){const i=ixISO(el.value);if(!isNaN(i))d.date=Math.max(MIN_D,Math.min(TODAY,i));}
  else d[k]=el.value;
});
document.addEventListener('change',e=>{const el=e.target;if(el.id==='mpf'){render();return;}if(el.dataset&&el.dataset.tdate){const t=S.tasks.find(x=>x.id===+el.dataset.tdate),i=ixISO(el.value);if(t&&!isNaN(i)){t.plan=Math.max(TODAY,Math.min(TODAY+14,i));persist();bump();render();toast('Обработка '+t.fid+' перенесена на '+fmt(t.plan)+'.');}return;}const id=drawerField();if(!id||!el.dataset)return;const k=el.dataset.d;if(k==='obj'||k==='product'){S.draft[id][k]=el.value;}else if(k==='phaseObs'||k==='date'){if(k==='phaseObs')S.draft[id].phaseObs=el.value;refreshDrawer('#'+el.id);}});
$scrim.addEventListener('click',()=>{if($pal.classList.contains('on'))closePal();else closeDrawer();});
document.getElementById('open-pal').addEventListener('click',openPal);

/* timeline: click or drag to change the date, hover for the day's details */
let dragging=false,raf=0;
const $tip=document.getElementById('tip');
document.addEventListener('pointerdown',e=>{const host=e.target.closest&&e.target.closest('#tl-svg');if(!host||e.button>0)return;stopPlay();dragging=true;try{host.setPointerCapture(e.pointerId);}catch(err){}setDate(dayAtX(e.clientX));});
document.addEventListener('pointermove',e=>{
  if(dragging){cancelAnimationFrame(raf);const cx=e.clientX;raf=requestAnimationFrame(()=>setDate(dayAtX(cx)));}
  const host=e.target.closest&&e.target.closest('#tl-svg');
  if(host&&TL&&e.pointerType!=='touch'){const i=dayAtX(e.clientX);const hv=document.getElementById('tl-hover');
    if(i<0||i>=SEASON){$tip.classList.remove('on');if(hv)hv.style.display='none';return;}
    if(hv){hv.style.display='';hv.setAttribute('x',TL.L+i*TL.dw);}
    $tip.innerHTML=tipFor(i);$tip.classList.add('on');const w=$tip.offsetWidth,h=$tip.offsetHeight;
    $tip.style.left=Math.max(8,Math.min(innerWidth-w-8,e.clientX+14))+'px';$tip.style.top=Math.min(innerHeight-h-8,e.clientY+16)+'px';}
  else{$tip.classList.remove('on');const hv=document.getElementById('tl-hover');if(hv)hv.style.display='none';}
});
document.addEventListener('pointerup',()=>{dragging=false;});
document.addEventListener('pointercancel',()=>{dragging=false;});
/* linked highlight: hovering a reason marks that day on the timeline and in the chart */
document.addEventListener('mouseover',e=>{const li=e.target.closest&&e.target.closest('.reasons li[data-day]');const d=li?+li.dataset.day:null;if(d!==S.hlDay){S.hlDay=d;drawTimeline();document.querySelectorAll('.chart .bar').forEach(b=>b.classList.toggle('hl',d!=null&&+b.dataset.day===d));}});

document.addEventListener('keydown',e=>{
  const tag=(e.target.tagName||'').toLowerCase(),typing=/input|textarea|select/.test(tag);
  if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();$pal.classList.contains('on')?closePal():openPal();return;}
  if(e.key==='Escape'){if($pal.classList.contains('on')){e.preventDefault();closePal();}else if(drawerFor){e.preventDefault();closeDrawer();}else if(S.playing){stopPlay();render();}return;}
  if(drawerFor&&e.key==='Tab'){const f=[...$dr.querySelectorAll('button,input,select,textarea,a')].filter(x=>!x.disabled);if(!f.length)return;const a=f[0],z=f[f.length-1];if(e.shiftKey&&document.activeElement===a){e.preventDefault();z.focus();}else if(!e.shiftKey&&document.activeElement===z){e.preventDefault();a.focus();}return;}
  if($pal.classList.contains('on')&&e.key==='Tab'){e.preventDefault();$pq.focus();return;}
  if(typing||drawerFor||$pal.classList.contains('on')||e.ctrlKey||e.metaKey||e.altKey)return;
  const k=e.key;
  if(k==='/'){e.preventDefault();openPal();return;}
  if(k==='?'){e.preventDefault();openKeys();return;}
  if(/^[1-6]$/.test(k)){go(VIEWS[+k-1].id);return;}
  if(S.view==='today'||S.view==='map'){
    if(k==='ArrowLeft'||k==='ArrowRight'){if(e.target.closest&&e.target.closest('[role=tablist],.seg,input'))return;e.preventDefault();stopPlay();setDate(S.D+(k==='ArrowLeft'?-1:1)*(e.shiftKey?7:1));return;}
    if(k==='t'||k==='T'||k==='е'||k==='Е'){stopPlay();setDate(TODAY);return;}
    if(k==='p'||k==='P'||k==='з'||k==='З'){togglePlay();return;}
    if(S.view==='map'&&(k==='Enter'||k===' ')&&e.target.tagName&&e.target.tagName.toLowerCase()==='polygon'){e.preventDefault();S.sel=e.target.dataset.field;render({resetField:true});return;}
  }
  if(S.view==='today'){
    const onRow=e.target.classList&&e.target.classList.contains('qrow');
    if(k==='j'||k==='о'||(k==='ArrowDown'&&onRow)){e.preventDefault();nextField(1);return;}
    if(k==='k'||k==='л'||(k==='ArrowUp'&&onRow)){e.preventDefault();nextField(-1);return;}
    if((k==='i'||k==='ш')&&st(S.sel)!=='none'){e.preventDefault();openInspect(S.sel);return;}
  }
});
let rsz;window.addEventListener('resize',()=>{clearTimeout(rsz);rsz=setTimeout(()=>{drawTimeline();fitSticky();},80);});
function fromHash(){const h=location.hash.slice(1);S.view=VIEWS.some(v=>v.id===h)?h:'today';render();}
window.addEventListener('hashchange',fromHash);
fromHash();
})();
