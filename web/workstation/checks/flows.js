/* Main flows: forecast, planning a treatment, inspection with «обработать», marking it applied, alarming journal, passport, brief.
   Run: node web/workstation/checks/flows.js — prints each step; exit code 1 on page errors. */
const {chromium}=require('playwright');
const path=require('path');/* WORKSTATION_URL=http://127.0.0.1:8770/workstation/ checks the served page under the server's CSP; default is the file. */
const PAGE=process.env.WORKSTATION_URL||'file://'+path.resolve(__dirname,'../index.html');

(async()=>{const b=await chromium.launch();const p=await b.newPage({viewport:{width:1440,height:900}});const errs=[];p.on('pageerror',e=>errs.push(e.message));p.on('console',m=>{if(m.type()==='error')errs.push(m.text())});
const U=PAGE;const log=(...a)=>console.log(...a);
await p.goto(U+'#today');await p.waitForTimeout(400);
log('digest:',(await p.textContent('.digest')).replace(/\s+/g,' '));

// select R02 via palette, check forecast box
await p.keyboard.press('Control+k');await p.fill('#pal-q','R02');await p.keyboard.press('Enter');await p.waitForTimeout(200);
log('R02 fcbox:',(await p.textContent('.fcbox').catch(()=>'none')).replace(/\s+/g,' ').slice(0,220));
await p.click('.fcbox [data-act="plan"]');await p.waitForTimeout(300);
await p.click('[data-act="save-plan"]');await p.waitForTimeout(200);log('plan toast:',await p.textContent('#toast'));
log('R02 fcbox after:',(await p.textContent('.fcbox').catch(()=>'none')).replace(/\s+/g,' ').slice(0,160));
// inspection on R09 with focus + treat
await p.keyboard.press('Control+k');await p.fill('#pal-q','R09');await p.keyboard.press('Enter');await p.waitForTimeout(200);
await p.keyboard.press('i');await p.waitForTimeout(300);await p.click('[data-pick="focus"]');await p.click('[data-dset="decision"][data-v="обработать"]');await p.waitForTimeout(100);

await p.selectOption('#pho','флаговый лист');await p.waitForTimeout(100);
log('phase hint:',await p.textContent('.dr-body').then(t=>/Сохраним фазу/.test(t)));
await p.click('[data-act="save-mark"]');await p.waitForTimeout(300);log('mark toast:',await p.textContent('#toast'));
// tasks on trips
await p.click('[data-nav="trips"]');await p.waitForTimeout(300);log('tasks:',(await p.textContent('#tasks')).replace(/\s+/g,' ').slice(0,300));
await p.evaluate(()=>document.getElementById('tasks').scrollIntoView());
const actual=await p.inputValue('#tasks [data-tdate]');
await p.click('#tasks [data-act="task-done"]');
await p.fill('#actual-treatment-date',actual);
await p.click('[data-act="confirm-task-done"]');await p.waitForTimeout(200);log('done toast:',await p.textContent('#toast'));
// journal alarm
await p.click('[data-nav="journal"]');await p.click('[data-jf="alarm"]');await p.waitForTimeout(200);log('alarm rows:',await p.$$eval('table.t tbody tr',r=>r.length));

// R09 history passport
await p.click('[data-nav="today"]');await p.keyboard.press('Control+k');await p.fill('#pal-q','R11');await p.keyboard.press('Enter');await p.click('[data-tab="hist"]');await p.waitForTimeout(200);
log('passport:',(await p.textContent('.kv.pass')).replace(/\s+/g,' '));
await p.click('[data-tab="wx"]');await p.waitForTimeout(150);await p.evaluate(()=>document.querySelector('.sgrid').scrollIntoView());
// compact timeline
await p.click('[data-act="tl-toggle"]');await p.waitForTimeout(200);log('compact svg?',await p.$('#tl-svg')!==null);await p.click('[data-act="tl-toggle"]');
// brief
await p.click('[data-act="brief"]');await p.waitForTimeout(200);log('brief:\n'+await p.textContent('.brief'));
log('errors',errs);await b.close();process.exitCode=errs.length?1:0;})();
