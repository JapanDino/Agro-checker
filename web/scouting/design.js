/* Two independent visual systems; changing design never reloads the journal. */
(() => {
  'use strict';
  const key='agropulse:design:v1',valid=v=>v==='atlas'||v==='hightech';
  let mode='hightech';
  try{const saved=localStorage.getItem(key);if(valid(saved))mode=saved;}catch{}
  const requested=new URL(location.href).searchParams.get('design');if(valid(requested))mode=requested;
  function apply(value,persist=false){
    mode=value;document.documentElement.dataset.design=mode;
    for(const sheet of document.querySelectorAll('link[data-design-sheet]'))sheet.disabled=sheet.dataset.designSheet!==mode;
    const button=document.getElementById('design-toggle');
    if(button){button.textContent=mode==='hightech'?'Дизайн: Хай-тек':'Дизайн: Атлас';button.title=mode==='hightech'?'Вернуть прежний дизайн «Атлас»':'Включить новый дизайн «Хай-тек»';button.setAttribute('aria-label',button.title);}
    if(persist){try{localStorage.setItem(key,mode);}catch{}const url=new URL(location.href);url.searchParams.set('design',mode);history.replaceState(history.state,'',url);}
    // Also preserve the selection when preference storage is blocked.
    for(const link of document.querySelectorAll('a[href]')){const url=new URL(link.getAttribute('href'),location.href);if(url.origin===location.origin&&['/scouting','/scouting/about'].includes(url.pathname)){url.searchParams.set('design',mode);link.href=url.href;}}
    if(persist){window.dispatchEvent(new Event('resize'));window.dispatchEvent(new Event('scouting-design-change'));}
  }
  apply(mode);
  document.addEventListener('DOMContentLoaded',()=>{apply(mode);document.getElementById('design-toggle')?.addEventListener('click',()=>apply(mode==='hightech'?'atlas':'hightech',true));});
})();
