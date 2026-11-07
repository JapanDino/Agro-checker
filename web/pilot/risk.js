(function (root) {
  "use strict";
  const finite = Number.isFinite;
  function stats(points, key) {
    const values = points.map(p => p[key]).filter(finite);
    return {count: values.length, total: points.length, complete: values.length === points.length && points.length > 0,
      min: values.length === points.length && points.length > 0 ? Math.min(...values) : null};
  }
  function samples(points, key, threshold, inclusive = false) {
    return points.filter(p => finite(p[key]) && (inclusive ? p[key] <= threshold : p[key] < threshold))
      .map(p => ({time:p.time,local_time:p.local_time,value:p[key]}));
  }
  function risk(night, stage) {
    const base = stats(night.points, "base"), corrected = stats(night.points, "corrected");
    const complete = base.complete && corrected.complete;
    const weather = {base: samples(night.points, "base", 0), corrected: samples(night.points, "corrected", 0)};
    if (!stage) return {status: "phase_required", complete, threshold: null, base, corrected, weather,
      baseHits: [], correctedHits: [], probability: null, damagePercent: null};
    const baseHits = samples(night.points, "base", stage.threshold_c, true);
    const correctedHits = samples(night.points, "corrected", stage.threshold_c, true);
    return {status: !complete ? "incomplete" : baseHits.length || correctedHits.length ? "threshold_reached" : "threshold_not_reached",
      complete, threshold: stage.threshold_c, base, corrected, weather, baseHits, correctedHits,
      probability: null, damagePercent: null};
  }
  function retrospective(night) {
    const observed = stats(night.points, "observed"), base = stats(night.points, "base"), corrected = stats(night.points, "corrected");
    if (!observed.complete || !base.complete || !corrected.complete) return {available: false, observed};
    const event = observed.min < 0;
    const label = min => event ? min < 0 ? "hit" : "miss" : min < 0 ? "false_alarm" : "correct_negative";
    return {available: true, observed, base: label(base.min), corrected: label(corrected.min), event};
  }
  function segments(points, key) {
    const output = []; let current = [];
    points.forEach((p, index) => {
      if (finite(p[key])) current.push({index, value: p[key]});
      else if (current.length) { output.push(current); current = []; }
    });
    if (current.length) output.push(current);
    return output;
  }
  function filterNights(nights, year, filter) {
    return nights.filter(n => n.year === Number(year) &&
      (filter === "cold" ? n.observations_complete && n.observed_min < 0 :
       filter === "incomplete" ? !n.observations_complete || !n.forecast_complete : true));
  }
  const api = {stats, samples, risk, retrospective, segments, filterNights};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.AgroRisk = api;
})(globalThis);
