/* Three visual systems; changing design never reloads the journal.
   "pulse" (default since the presentation build) layers its own sheets over the
   high-tech layout skeleton. "hightech" and "atlas" stay available for rollback. */
(() => {
  'use strict';
  // v2: a fresh key so earlier saved "hightech" choices do not hide the new default.
  const key='agropulse:design:v2',order=['pulse','hightech','atlas'],valid=v=>order.includes(v);
  const names={pulse:'Пульс',hightech:'Хай-тек',atlas:'Атлас'};
  let mode='pulse';
  try{const saved=localStorage.getItem(key);if(valid(saved))mode=saved;}catch{}
  const requested=new URL(location.href).searchParams.get('design');if(valid(requested))mode=requested;
  const next=()=>order[(order.indexOf(mode)+1)%order.length];
  function apply(value,persist=false){
    mode=value;document.documentElement.dataset.design=mode;
    for(const sheet of document.querySelectorAll('link[data-design-sheet]'))sheet.disabled=!sheet.dataset.designSheet.split(' ').includes(mode);
    const button=document.getElementById('design-toggle');
    if(button){button.textContent='Дизайн: '+names[mode];button.title=`Переключить на «${names[next()]}»`;button.setAttribute('aria-label',`Оформление «${names[mode]}». ${button.title}`);}
    if(persist){try{localStorage.setItem(key,mode);}catch{}const url=new URL(location.href);url.searchParams.set('design',mode);history.replaceState(history.state,'',url);}
    // Also preserve the selection when preference storage is blocked.
    for(const link of document.querySelectorAll('a[href]')){const url=new URL(link.getAttribute('href'),location.href);if(url.origin===location.origin&&['/scouting','/scouting/about'].includes(url.pathname)){url.searchParams.set('design',mode);link.href=url.href;}}
    if(persist){window.dispatchEvent(new Event('resize'));window.dispatchEvent(new Event('scouting-design-change'));}
  }
  apply(mode);
  document.addEventListener('DOMContentLoaded',()=>{apply(mode);document.getElementById('design-toggle')?.addEventListener('click',()=>apply(next(),true));});
})();
