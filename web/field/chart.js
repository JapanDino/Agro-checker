(function(root){
  "use strict";
  function chartModel(hours,observations,quantity,threshold){
    const humidity=quantity==="rh_pct", predicted=hours.map(h=>h[quantity]);
    const observed=hours.map((_,i)=>Number.isFinite(observations[i]?.[quantity])?observations[i][quantity]:null);
    const values=predicted.concat(observed).filter(Number.isFinite);
    if(!humidity&&Number.isFinite(threshold))values.push(threshold);
    const low=humidity?0:Math.floor(Math.min(...values)-1),high=humidity?100:Math.ceil(Math.max(...values)+1);
    const x=i=>65+i*55,y=v=>245-(v-low)/(high-low)*190;
    const segments=[];let part=[];
    observed.forEach((v,i)=>{if(v!==null)part.push({index:i,value:v,x:x(i),y:y(v)});else if(part.length){segments.push(part);part=[];}});
    if(part.length)segments.push(part);
    return {quantity,unit:humidity?"%":"°C",low,high,
      threshold_y:!humidity&&Number.isFinite(threshold)?y(threshold):null,
      predicted:predicted.map((value,i)=>({index:i,value,x:x(i),y:Number.isFinite(value)?y(value):null})),
      observed_segments:segments,ticks:Array.from({length:5},(_,i)=>{const value=low+(high-low)*i/4;return {value,y:y(value)};})};
  }
  if(typeof module!=="undefined"&&module.exports)module.exports={chartModel};
  else root.FieldChart={chartModel};
})(typeof globalThis!=="undefined"?globalThis:this);
