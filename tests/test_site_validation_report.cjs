const test=require('node:test');const assert=require('node:assert/strict');
const {value,pair,criteriaHTML}=require('../web/validation/app.js');
test('Unknown quantities are never rendered as zero error or perfect percentages',()=>{
  assert.equal(value(null),'—');assert.equal(value(null,'fraction'),'—');assert.equal(value(0,'fraction'),'0%');assert.equal(value(1,'fraction'),'100%');assert.equal(pair(null,null),'— / —');
});
test('Gate states distinguish missing data, unagreed criteria and actual numerical failures',()=>{
  const rows=['insufficient_data','not_agreed','not_met','met'].map((status,i)=>({label:'<script>bad</script>',value:i?0:null,limit:i===1?null:.95,unit:'fraction',direction:'min',status,group:'technical',reason:'<img onerror=x>'}));
  const html=criteriaHTML(rows);assert.match(html,/Недостаточно данных/);assert.match(html,/Не согласовано/);assert.match(html,/Порог не выполнен/);assert.match(html,/Численный порог выполнен/);assert.doesNotMatch(html,/<script>|<img /);
  assert.equal(criteriaHTML(rows,'support'),'');
});
