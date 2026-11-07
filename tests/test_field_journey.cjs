const test=require('node:test');const assert=require('node:assert/strict');
const J=require('../web/field/journey.js');const {chartModel}=require('../web/field/chart.js');
const entry=(id,pid,fid,time)=>({id,profile_id:pid,recorded_at_utc:time,payload:{forecast_id:fid}});
const data={forecasts:[{id:'f1',profile_id:'p1'},{id:'f2',profile_id:'p1'},{id:'f3',profile_id:'p2'}],
  reads:[entry('r1','p1','f1','2026-10-13T08:00:00+00:00'),entry('r2','p1','f2','2026-10-13T09:00:00+00:00')],
  journal:[entry('j1','p1','f1','2026-10-13T08:30:00+00:00'),entry('j2','p1','f2','2026-10-13T09:30:00+00:00')],
  assessments:[{forecast_id:'f1',observations:[{temperature_c:-3,rh_pct:99}]}],forecast_briefs:[{forecast_id:'f1',window_state:'past'}]};
test('Reads, decisions and result remain bound to the selected issue',()=>{const m=J.model(data,'p1','f1');assert.equal(m.read.id,'r1');assert.equal(m.decision.id,'j1');assert.equal(m.assessment.forecast_id,'f1');assert.equal(J.model(data,'p1','f2').read.id,'r2');});
test('A missing or other-profile issue never silently falls back',()=>{assert.equal(J.model(data,'p1','f3').forecast,null);assert.equal(J.model(data,'p1','gone').decision,null);});
test('Draft keys separate profiles and issues without delimiter collisions',()=>{assert.notEqual(J.contextKey('p1','f1'),J.contextKey('p1','f2'));assert.notEqual(J.contextKey('a-b','c'),J.contextKey('a','b-c'));});
test('Facts enter the chart only in the result step',()=>{const a=data.assessments[0],h=Array.from({length:14},()=>({temperature_c:3,rh_pct:75}));for(const step of ['forecast','decision']){const c=chartModel(h,J.observations(a,step),'temperature_c',0);assert.deepEqual(c.observed_segments,[]);assert.ok(c.low>-3);}assert.equal(chartModel(h,J.observations(a,'result'),'temperature_c',0).observed_segments[0][0].value,-3);});
test('Updated facts do not change read/decision binding or preview',()=>{const copy=structuredClone(data);copy.assessments[0].observations[0].temperature_c=-25;assert.deepEqual(J.observations(copy.assessments[0],'forecast'),[]);assert.equal(J.model(copy,'p1','f1').decision.id,'j1');assert.equal(data.assessments[0].observations[0].temperature_c,-3);});
test('Latest follow-up is shown for its issue only',()=>{const copy=structuredClone(data);copy.journal.push(entry('j3','p1','f1','2026-10-13T10:00:00+00:00'));assert.equal(J.model(copy,'p1','f1').decision.id,'j3');assert.equal(J.model(copy,'p1','f2').decision.id,'j2');});
