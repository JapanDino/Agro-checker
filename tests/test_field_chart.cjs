const test=require('node:test');
const assert=require('node:assert/strict');
const {chartModel}=require('../web/field/chart.js');
const hours=Array.from({length:14},(_,i)=>({temperature_c:4-i*.5,rh_pct:75+i}));

test('Missing observations create separate segments including isolated points',()=>{
  const observations=hours.map((_,i)=>({temperature_c:[2,1,null,-1,null,-2][i]??null}));
  const chart=chartModel(hours,observations,'temperature_c',0);
  assert.deepEqual(chart.observed_segments.map(s=>s.map(p=>p.index)),[[0,1],[3],[5]]);
});

test('Humidity uses its own observations and a stable 0–100 scale',()=>{
  const chart=chartModel(hours,[{temperature_c:-20,rh_pct:95},{temperature_c:-10,rh_pct:null},{temperature_c:null,rh_pct:80}],'rh_pct',1);
  assert.equal(chart.low,0);assert.equal(chart.high,100);assert.equal(chart.threshold_y,null);
  assert.deepEqual(chart.observed_segments.map(s=>s.map(p=>p.index)),[[0],[2]]);
  assert.equal(chart.predicted[0].value,75);
});

test('Custom weather threshold lies on the temperature scale without assuming zero',()=>{
  const chart=chartModel(hours,[],'temperature_c',-5);
  assert.ok(chart.low< -5);assert.ok(chart.threshold_y>chart.predicted.at(-1).y);
  const atThreshold=chartModel(hours.map(h=>({...h,temperature_c:1})),[],'temperature_c',1);
  assert.equal(atThreshold.threshold_y,atThreshold.predicted[0].y);
});

test('Later facts change only observed series and display scale, never forecast values',()=>{
  const original=structuredClone(hours),before=chartModel(hours,[],'temperature_c',0);
  const after=chartModel(hours,hours.map(()=>({temperature_c:-20})),'temperature_c',0);
  assert.deepEqual(after.predicted.map(p=>p.value),before.predicted.map(p=>p.value));
  assert.deepEqual(hours,original);assert.ok(after.low<before.low);
});

test('Nonfinite facts remain missing instead of drawing a zero humidity observation',()=>{
  const chart=chartModel(hours,[{rh_pct:NaN},{rh_pct:Infinity},{rh_pct:0}],'rh_pct',0);
  assert.deepEqual(chart.observed_segments.map(s=>s.map(p=>p.index)),[[2]]);
  assert.equal(chart.observed_segments[0][0].value,0);
});
