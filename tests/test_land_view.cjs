const test=require('node:test');
const assert=require('node:assert/strict');
const {observation,gapReason,pointInCell,inspectionCandidate}=require('../web/land/app.js');
test('A rejected date never becomes a zero or an unfiltered index',()=>{
  const cloudy={point_window_usable:false,point_scl:9,point_60m_ndvi_median:.6};
  assert.equal(observation(cloudy,'ndvi'),null);
  assert.match(gapReason(cloudy),/облака/);
  assert.equal(observation({point_window_usable:true,point_60m_ndmi_median:0},'ndmi'),0);
  assert.equal(observation({point_window_usable:true,point_60m_ndmi_median:null},'ndmi'),null);
});
test('Cell selection respects projected quadrilaterals instead of their bounding boxes',()=>{
  const skew=[[0,0],[2,1],[3,3],[1,2]];
  assert.equal(pointInCell([1.5,1.5],skew),true);
  assert.equal(pointInCell([.1,2.9],skew),false);
});
test('Technical extraction failures are not labelled as cloud observations',()=>{
  const failed={extraction_status:'error',point_window_usable:false,point_scl:null,point_60m_ndvi_median:null};
  assert.match(gapReason(failed),/ошибка получения/);
  assert.equal(observation(failed,'ndvi'),null);
});
test('Inspection filter rejects uncertain shifts, partial support and incompatible versions',()=>{
  const b={paired_cells:9,ndvi:{positive_cells:9,negative_cells:0,shift_scenarios:9,shift_range:[.12,.22]}};
  assert.equal(inspectionCandidate(b,'ndvi',.1),true);
  assert.equal(inspectionCandidate(b,'ndvi',.15),false);
  assert.equal(inspectionCandidate(b,'ndvi',.1,false),false);
  assert.equal(inspectionCandidate({...b,paired_cells:8},'ndvi',.1),false);
  assert.equal(inspectionCandidate({...b,ndvi:{...b.ndvi,shift_range:[-.1,.2]}},'ndvi',.1),false);
  assert.equal(inspectionCandidate({...b,ndvi:{...b.ndvi,shift_scenarios:8}},'ndvi',.1),false);
});
