const test=require('node:test');const assert=require('node:assert/strict');
const {model,chartHTML,detailHTML}=require('../web/validation/night-chart.js');
function fixture(){
 const start=Date.parse('2026-10-02T16:00Z'),at=m=>new Date(start+m*60000).toISOString();
 return {night:'2026-10-02',start_at_utc:at(0),end_at_utc:at(840),samples:[],sensor:{interval_minutes:5},forecast:null,hourly:[{valid_at_utc:at(0),state:'missing',temperature_c:null}],slots:[{valid_at_utc:at(0),state:'missing'}],counts:{rows:0,usable:0,excluded:0,hourly:{missing:14}},selected:{reference_sensor_id:'t1'},unplaced_count:0,limitations:[],at};
}
function point(d,m,temp,usable=true){return {observed_at_utc:d.at(m),temperature_c:temp,usable,reasons:usable?[]:['TEST excluded'],status:usable?'accepted':'rejected'};}
test('empty and nonnumeric series have no zero measurements invented',()=>{
 const d=fixture();d.samples=[point(d,0,null)];const c=model(d);assert.equal(c.observed.length,0);assert.equal(c.excluded.length,0);assert.equal(c.gfs.length,0);assert(chartHTML(d).includes('night-missing'));
});
test('excluded rows and cadence gaps split observation curve at actual timestamps',()=>{
 const d=fixture();d.samples=[point(d,0,1),point(d,5,.5),point(d,10,9,false),point(d,15,0),point(d,30,-1)];
 const c=model(d);assert.deepEqual(c.observed.map(s=>s.map(p=>p.value)),[[1,.5],[0],[-1]]);assert.equal(c.excluded.length,1);assert(c.excluded[0].x>c.observed[0][1].x);
});
test('unknown sampling cadence creates isolated points and future slots are not orange gaps',()=>{
 const d=fixture();d.sensor=null;d.samples=[point(d,0,1),point(d,5,0)];d.hourly=[{valid_at_utc:d.at(0),state:'pending'}];d.slots=[{valid_at_utc:d.at(0),state:'pending'}];
 assert.deepEqual(model(d).observed.map(s=>s.length),[1,1]);assert.equal(model(d).missing.length,0);assert(chartHTML(d).includes('slot-pending'));
});
test('later observations never mutate stored GFS values or bridge its missing hour',()=>{
 const d=fixture();d.forecast={hours:[{valid_at_utc:d.at(0),temperature_c:1},{valid_at_utc:d.at(60),temperature_c:null},{valid_at_utc:d.at(120),temperature_c:-1}]};const before=structuredClone(d.forecast);
 d.samples=[point(d,5,-20)];const c=model(d);assert.deepEqual(c.gfs.map(s=>s.map(p=>p.value)),[[1],[-1]]);assert.deepEqual(d.forecast,before);
});
test('reasons and sources are escaped and missing temperatures remain dashes',()=>{
 const d=fixture();d.samples=[{...point(d,0,null,false),reasons:['<script>bad</script>'],batch_id:'<img src=x>',line:2}];d.counts={...d.counts,rows:1,excluded:1};
 const html=detailHTML(d);assert(!html.includes('<script>'));assert(!html.includes('<img'));assert(html.includes('&lt;script&gt;'));assert(html.includes('Измерения')||html.includes('измерения'));assert(html.includes('—'));
});
