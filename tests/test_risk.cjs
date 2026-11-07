const test = require('node:test');
const assert = require('node:assert/strict');
const core = require('../web/pilot/risk.js');
const phase = {threshold_c: -2.2};
const night = (base, corrected, observed) => ({points:base.map((b,i) => ({base:b,corrected:corrected[i],observed:observed[i],local_time:String(i)}))});

test('Unknown phase does not invent an apple damage threshold', () => {
  const r = core.risk(night([-5],[-6],[-10]),null);
  assert.equal(r.status,'phase_required'); assert.equal(r.threshold,null);
  assert.equal(r.probability,null); assert.equal(r.damagePercent,null);
});
test('Risk is unchanged by later observations', () => {
  assert.deepEqual(core.risk(night([1,0],[-1,-2],[5,6]),phase),core.risk(night([1,0],[-1,-2],[-20,-20]),phase));
});
test('Negative air temperature is not the apple threshold', () => {
  const r = core.risk(night([-1],[-1.8],[-5]),phase);
  assert.equal(r.status,'threshold_not_reached'); assert.equal(r.weather.base.length,1);
});
test('Reference threshold equality and disagreement are retained', () => {
  const r = core.risk(night([0],[-2.2],[0]),phase);
  assert.equal(r.status,'threshold_reached'); assert.equal(r.baseHits.length,0); assert.equal(r.correctedHits.length,1);
});
test('An incomplete forecast cannot reassure or invent a minimum', () => {
  const r = core.risk(night([2,null],[1,null],[0,0]),phase);
  assert.equal(r.status,'incomplete'); assert.equal(r.base.min,null); assert.equal(r.complete,false);
});
test('Chart paths do not bridge missing observations', () => {
  assert.deepEqual(core.segments([{observed:1},{observed:null},{observed:2},{observed:3}], 'observed'),
    [[{index:0,value:1}],[{index:2,value:2},{index:3,value:3}]]);
});
test('Retrospective misses and false alarms use the weather threshold', () => {
  const r = core.retrospective(night([1,2],[0.1,1],[-4,1]));
  assert.equal(r.base,'miss'); assert.equal(r.corrected,'miss');
  assert.equal(core.retrospective(night([0],[-1],[0])).corrected,'false_alarm');
});
test('Incomplete observations cannot supply retrospective minimum', () => {
  const r = core.retrospective(night([1,2],[0,1],[-5,null]));
  assert.equal(r.available,false); assert.equal(r.observed.min,null);
});
test('Cold-night filters explicitly use only complete hindsight', () => {
  const rows = [{year:2025,observations_complete:true,observed_min:-2,forecast_complete:true},
    {year:2025,observations_complete:false,observed_min:null,forecast_complete:true},
    {year:2024,observations_complete:true,observed_min:-3,forecast_complete:true}];
  assert.equal(core.filterNights(rows,2025,'cold').length,1);
  assert.equal(core.filterNights(rows,2025,'incomplete').length,1);
  assert.equal(core.filterNights(rows,2022,'all').length,0);
});
