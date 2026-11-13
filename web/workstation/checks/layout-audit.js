/* Layout audit: overlaps, clipped text and sideways scroll in every view at 7 widths.
   Run: node web/workstation/checks/layout-audit.js (Playwright with Chromium). Exit code 1 on issues. */
const {chromium}=require('playwright');
const path=require('path');/* WORKSTATION_URL=http://127.0.0.1:8770/workstation/ checks the served page under the server's CSP; default is the file. */
const PAGE=process.env.WORKSTATION_URL||'file://'+path.resolve(__dirname,'../index.html');

const AUDIT=()=>{const out=[];const vis=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0;};
  const name=e=>e.tagName.toLowerCase()+(typeof e.className==='string'&&e.className?'.'+e.className.trim().split(/\s+/).join('.'):'');
  document.querySelectorAll('main *, header *, .drawer *').forEach(e=>{if(!vis(e)||e.closest('svg')||e.closest('.sr')||e.classList.contains('sr'))return;const cs=getComputedStyle(e);if(cs.textOverflow==='ellipsis'||cs.overflowX==='auto'||cs.overflowX==='scroll'||cs.overflowX==='hidden'&&e.closest('.lane'))return;
    if(e.scrollWidth>e.clientWidth+1&&e.clientWidth>0&&!['TEXTAREA','INPUT','SELECT'].includes(e.tagName))out.push('overflow '+name(e)+' '+e.scrollWidth+'>'+e.clientWidth+' «'+e.textContent.trim().slice(0,30)+'»');});
  document.querySelectorAll('main *, .drawer *').forEach(p=>{if(p.closest('svg'))return;const kids=[...p.children].filter(k=>vis(k)&&!['absolute','fixed','sticky'].includes(getComputedStyle(k).position));
    for(let i=0;i<kids.length;i++)for(let j=i+1;j<kids.length;j++){const a=kids[i].getBoundingClientRect(),b=kids[j].getBoundingClientRect();const ox=Math.min(a.right,b.right)-Math.max(a.left,b.left),oy=Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top);if(ox>2&&oy>2)out.push('overlap in '+name(p)+': '+name(kids[i])+' × '+name(kids[j]));}});
  return [...new Set(out)];};
(async()=>{const b=await chromium.launch();let total=0;
for(const w of [1920,1440,1366,1280,1024,768,400]){
  const p=await b.newPage({viewport:{width:w,height:w<500?860:820}});const errs=[];p.on('pageerror',e=>errs.push(e.message));p.on('console',m=>{if(m.type()==='error')errs.push(m.text());});
  for(const [v,f,extra] of [['today','R11'],['today','R21'],['today','R10'],['today','R07','wx'],['today','R11','sat'],['today','R23','hist'],['map','R21'],['map','R10'],['season'],['trips'],['journal'],['data','','sample'],['today','R11','drawer']]){
    await p.goto(PAGE+'#'+v);await p.waitForTimeout(200);
    if(f){await p.keyboard.press('Control+k');await p.fill('#pal-q',f);await p.keyboard.press('Enter');await p.waitForTimeout(150);if(v!=='today'){await p.click('[data-nav="'+v+'"]');await p.waitForTimeout(150);}}
    if(extra==='sample')await p.click('[data-act="sample"]');
    if(['wx','sat','hist'].includes(extra))await p.click('[data-tab="'+extra+'"]');
    if(extra==='drawer'){await p.keyboard.press('i');await p.waitForTimeout(300);try{await p.click('[data-pick="focus"]',{timeout:2000});}catch(e){console.log('NO DRAWER at',w,await p.evaluate(()=>document.activeElement.outerHTML.slice(0,90)));}}
    const r=await p.evaluate(AUDIT);const hs=await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth||document.body.scrollWidth>innerWidth);
    if(r.length||hs){total+=r.length+(hs?1:0);console.log(`${w} ${v} ${f||''} ${extra||''}${hs?' HSCROLL':''}\n  `+r.slice(0,10).join('\n  '));}
  }
  if(errs.length){console.log('errors',w,[...new Set(errs)].slice(0,5));total+=errs.length;}await p.close();}
console.log('issues',total);await b.close();process.exitCode=total?1:0;})();
