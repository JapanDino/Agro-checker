'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const card=require('../web/scouting/review-card.js');
function fixture(){return {status:'window_comparison_missing',question:'Проверить на месте',
  observation:{after:{at:'2026-11-08T08:37:39Z'},pair_age_days:1,window:{paired_cells:0,total_cells:9,coverage:0,ndvi:null,ndmi:null},
    boundary_sensitivity:{ndmi:{complete:false}}},latest_usable_place_now:{at:'2026-11-05T08:00:00Z',age_days:4},
  forecast:{status:'not_in_input_pool',issued_at_utc:'2026-11-09T12:37:20Z',input_available:false,
    target_window:{start_utc:'2026-11-14T00:00:00Z',end_exclusive_utc:'2026-11-17T00:00:00Z'}}};}
test('missing window never presented as measured decline or negative forecast',()=>{
 const v=card.view(fixture());assert.match(v.lines[0][1],/Недостаточно/);assert.match(v.forecast,/не выпускался/);
 assert.match(v.forecast,/это не отрицательный прогноз/);assert.doesNotMatch(v.forecast,/Исход ещё не оценён/);assert.doesNotMatch(v.lines[0][1],/0,000/);
});
test('target dates retain UTC calendar window and do not shift end to17Nov',()=>{
 const v=card.view(fixture());assert.match(v.forecast,/14\.11\.2026–16\.11\.2026 UTC/);
});
test('area boundary sensitivity is not labelled window model confidence',()=>{
 const f=fixture();f.observation.boundary_sensitivity.ndmi={complete:true,min:-.129,max:-.098,direction:'decrease'};
 const v=card.view(f);assert.match(v.boundary,/-0,129…-0,098/);assert.match(v.boundary,/во всех сценариях/);
});
test('future selection is distinct from a final positive outcome',()=>{
 const f=fixture();Object.assign(f.forecast,{status:'pending',input_available:true,selected_baseline:true,selected_candidate:true});
 const v=card.view(f);assert.match(v.forecast,/В коротком списке обеих/);assert.match(v.forecast,/не оценён/);
 assert.doesNotMatch(v.forecast,/Зарегистрировано/);
});
test('absent final row stays unknown rather than becoming a negative',()=>{
 const f=fixture();Object.assign(f.forecast,{status:'finalised',input_available:true,selected_baseline:true,selected_candidate:true,outcome:null});
 assert.match(card.view(f).forecast,/исход неизвестен/);
});
