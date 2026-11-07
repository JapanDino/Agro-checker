const test=require('node:test');const assert=require('node:assert/strict');
const {view,between}=require('../web/land/place-history.js');
function data(){return {place:{centre:{latitude:45,longitude:38}},inspection_reference:{period:'recent',observation_version:'a'.repeat(32)},scenes:[
  {acquired_at_utc:'2026-10-01T08:00:00Z',usable:true,valid_cells:9,ndvi:{median:.4,spatial_range:[.3,.5]},ndmi:{median:.2,spatial_range:[.1,.3]}},
  {acquired_at_utc:'2026-10-02T08:00:00Z',usable:false,valid_cells:0,reason:'Облака <img onerror=x>'}],visits:[],intervals:[]};}
test('Empty real journal shows satellite points and explicit gaps without invented visits or interpolated lines',()=>{
  const d=data(),html=view(d);assert.match(html,/осмотров: 0/);assert.match(html,/1\/2/);assert.match(html,/×/);assert.doesNotMatch(html,/<polyline|<path|<img onerror/);assert.match(html,/&lt;img onerror=x&gt;/);
});
test('Visit comparison explains missing observations and never turns them into a zero delta',()=>{
  const d=data();d.visits=[{payload:{notes:'same',photos:[]}},{payload:{notes:'same',photos:[{}]}}];
  d.intervals=[{days_between:2,before:null,after:null,status:'same_scene',reason:'Одна съёмка',ndvi:null,ndmi:null}];
  const html=between(d,1);assert.match(html,/текст совпадает/);assert.match(html,/0 → 1/);assert.match(html,/Одна съёмка/);assert.doesNotMatch(html,/Изменение NDVI/);
  assert.match(between(d,0),/Предыдущего посещения/);
});
test('Paired values and acquisition lags displayed with escaped reasons',()=>{
  const d=data();d.visits=[{payload:{notes:'a',photos:[]}},{payload:{notes:'b',photos:[]}}];
  const s={acquired_at_utc:'2026-10-01T08:00:00Z',lag_days:1,within_lag_limit:true};
  d.intervals=[{days_between:2,before:s,after:s,status:'paired',reason:'<script>x</script>',paired_cells:5,ndvi:{median:-.2,spatial_range:[-.3,-.1]},ndmi:{median:.1,spatial_range:[0,.2]}}];
  const html=between(d,1);assert.match(html,/Изменение NDVI/);assert.match(html,/общих 5\/9/);assert.match(html,/отставание.*1.0 дн/);assert.doesNotMatch(html,/<script>/);
});
test('A visit after last acquisition but inside the catalogue period keeps its real date marker',()=>{
  const d=data();d.catalogue_window={start:'2026-10-01T00:00:00Z',end:'2026-10-04T23:59:59Z'};
  d.visits=[{payload:{observed_at_utc:'2026-10-03T12:00:00Z'}}];
  assert.match(view(d),/Осмотр №1:/);
  d.visits[0].payload.observed_at_utc='2026-10-05T12:00:00Z';
  assert.doesNotMatch(view(d),/Осмотр №1:/);
});
