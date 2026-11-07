const test=require('node:test');
const assert=require('node:assert/strict');
const {inspectionTarget,targetKey,observedAtUTC}=require('../web/land/journal.js');
test('Draft location and both index values are detached from later map changes',()=>{
  const d={inspection_reference:{period:'recent',observation_version:'version1'},before:{id:'a'},after:{id:'b'}};
  const w={id:'w1-1',centre:{latitude:45,longitude:38},ndvi:{median:-.2},ndmi:{median:.1}};
  const t=inspectionTarget(d,w,'ndvi',.1);const key=targetKey(t);
  w.centre.latitude=0;w.ndvi.median=9;d.inspection_reference.observation_version='version2';
  assert.equal(t.window.centre.latitude,45);assert.equal(t.window.ndvi.median,-.2);assert.equal(targetKey(t),key);
  assert.notEqual(targetKey(inspectionTarget(d,w,'ndvi',.1)),key);
  assert.equal(inspectionTarget(d,null,'ndvi',.1),null);
});
test('Inspection time is interpreted as Moscow time, independently of browser timezone',()=>{
  assert.equal(observedAtUTC('2026-10-18T12:34'),'2026-10-18T09:34:00.000Z');
  assert.throws(()=>observedAtUTC(''));
  assert.throws(()=>observedAtUTC('2026-10-18T12:34Z'));
});
