/* Capability showcase: explains what the prototype already does and jumps to each feature.
   It only opens a dialog and clicks the app's own controls, so every route goes through
   app.js checks (archive loaded, pair available) and nothing is written to the journal. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  document.addEventListener('DOMContentLoaded',()=>{
    const dialog=$('capabilities-dialog');if(!dialog)return;
    const open=()=>{if(!dialog.open){dialog.showModal();dialog.scrollTop=0;}};
    const close=()=>{if(dialog.open)dialog.close();};
    const nav=step=>document.querySelector(`.primary-nav [data-step="${step}"]`)?.click();
    const actions={
      open,
      map:()=>{close();nav(1);},
      history:()=>{close();nav(4);},
      plan:()=>{close();nav(5);},
      // Sensor-site candidates: switch the map layer through its own control and show the tab.
      sensors:()=>{close();nav(1);const layer=$('map-layer');if(layer&&layer.value!=='sensors'){layer.value='sensors';layer.dispatchEvent(new Event('change',{bubbles:true}));}
        $('focus-map')?.click(); // zooms in and collapses the card, so expand it afterwards
        const details=$('toggle-details');if(details?.getAttribute('aria-expanded')==='false')details.click();
        document.querySelector('[data-aside="sensors"]')?.click();},
      // The archive example lives in the demo dialog; reuse its button so app.js keeps control.
      demo:()=>{close();const start=$('start-demo');if(start&&!start.disabled)start.click();else $('open-demo')?.click();},
    };
    $('open-capabilities')?.addEventListener('click',open);
    $('close-capabilities')?.addEventListener('click',close);
    dialog.addEventListener('click',event=>{if(event.target===dialog)close();});
    for(const button of document.querySelectorAll('[data-showcase-action]'))
      button.addEventListener('click',()=>actions[button.dataset.showcaseAction]?.());
  });
})();
