const test=require('node:test');const assert=require('node:assert/strict');
const {previewHTML}=require('../web/validation/measurement-import.js');
test('preview exposes gaps and escapes file and row text without fabricated quality',()=>{
 const html=previewHTML({filename:'<img src=x>',counts:{invalid:1},rows_total:1,rows_shown:1,can_commit:false,unsorted:true,plan_action:'draft',sensor_checked:false,source_sha256:'abc',nights:[{night:'2026-10-02',hourly_before:0,hourly_after:1,hourly_missing:13,slots_missing:167}],rows:[{line:2,observed_at_utc:null,temperature_c:null,rh_pct:null,status:'invalid',reasons:['<script>bad</script>']}]});
 assert(!html.includes('<script>'));assert(!html.includes('<img'));assert(html.includes('&lt;script&gt;'));assert(html.includes('13'));assert(html.includes('167'));assert(html.includes('Сохранение недоступно'));assert(html.includes('черновая'));
});
