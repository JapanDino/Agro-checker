(function(root){
  'use strict';
  const contextKey=(profileId,forecastId)=>JSON.stringify([profileId,forecastId]);
  function latest(records,profileId,forecastId){
    return (records||[]).filter(r=>r.profile_id===profileId&&r.payload.forecast_id===forecastId)
      .reduce((a,b)=>!a||b.recorded_at_utc>=a.recorded_at_utc?b:a,null);
  }
  function model(data,profileId,forecastId){
    const forecast=(data.forecasts||[]).find(r=>r.profile_id===profileId&&r.id===forecastId)||null;
    if(!forecast)return {forecast:null,read:null,decision:null,assessment:null,brief:null};
    return {forecast,read:latest(data.reads,profileId,forecastId),decision:latest(data.journal,profileId,forecastId),
      assessment:(data.assessments||[]).find(r=>r.forecast_id===forecastId)||null,
      brief:(data.forecast_briefs||[]).find(r=>r.forecast_id===forecastId)||null};
  }
  // Facts enter the chart/table only after the user explicitly opens the result step.
  function observations(assessment,step){return step==='result'?(assessment?.observations||[]):[];}
  const api={contextKey,latest,model,observations};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.FieldJourney=api;
})(typeof window!=='undefined'?window:globalThis);
