/* AgroPulse «Пульс» — presentation motion layer.
   Progressive enhancement only: inert unless <html data-design="pulse">.
   Never changes data, requests or the journal. Number counters always end on the
   exact string app.js wrote. Only classes and CSS custom properties are touched. */
(() => {
  'use strict';
  const reduce = matchMedia('(prefers-reduced-motion: reduce)');
  const isPulse = () => document.documentElement.dataset.design === 'pulse';
  let on = false;

  /* ── 1. Animated counters ── */
  const COUNTER_IDS = ['selected-ndvi', 'selected-ndmi', 'evidence-delta'];
  const NUMBER = /^([+\-−]?)(\d+),(\d+)$/;
  const parse = text => {
    const m = NUMBER.exec(text.trim());
    if (!m) return null;
    const value = Number(m[2] + '.' + m[3]) * (m[1] === '-' || m[1] === '−' ? -1 : 1);
    return Number.isFinite(value) ? { value, digits: m[3].length, minus: m[1] === '−' ? '−' : '-' } : null;
  };
  const format = (v, p) => (v > 0 ? '+' : v < 0 ? p.minus : '') + Math.abs(v).toFixed(p.digits).replace('.', ',');
  const counters = new Map(); // element -> {observer, last, frame, target}

  function write(el, text) {
    const c = counters.get(el);
    el.textContent = text;
    c.observer.takeRecords(); // our own writes are never treated as app updates
  }
  function finish(el) {
    const c = counters.get(el);
    if (!c || !c.frame) return;
    cancelAnimationFrame(c.frame); c.frame = 0;
    write(el, c.target);
    el.classList.remove('pulse-counting');
  }
  function onAppWrite(el) {
    const c = counters.get(el);
    const text = el.textContent, next = parse(text), from = c.frame ? c.shown : (c.last ? c.last.value : 0);
    if (c.frame) { cancelAnimationFrame(c.frame); c.frame = 0; el.classList.remove('pulse-counting'); }
    c.last = next;
    if (!next || !on || reduce.matches || from === next.value) return;
    c.target = text; c.shown = from;
    const start = performance.now(), duration = 760;
    el.classList.add('pulse-counting');
    const step = now => {
      const t = Math.min(1, (now - start) / duration), eased = 1 - Math.pow(1 - t, 4);
      if (t >= 1) { c.frame = 0; write(el, c.target); el.classList.remove('pulse-counting'); return; }
      c.shown = from + (next.value - from) * eased;
      write(el, format(c.shown, next));
      c.frame = requestAnimationFrame(step);
    };
    write(el, format(from, next));
    c.frame = requestAnimationFrame(step);
  }
  for (const id of COUNTER_IDS) {
    const el = document.getElementById(id);
    if (!el) continue;
    const observer = new MutationObserver(() => onAppWrite(el));
    counters.set(el, { observer, last: parse(el.textContent), frame: 0, target: el.textContent, shown: 0 });
    observer.observe(el, { childList: true, characterData: true, subtree: true });
  }

  /* ── 2. Calm intro, once per load, only on the map page ── */
  const workspace = document.getElementById('workspace');
  const CHROME = ['.map-control-dock', '#toggle-area-list', '.selection-card', '.atlas-caption', '.map-toolbar', '#layer-note'];
  let introTimer = 0;
  function endIntro() {
    clearTimeout(introTimer); introTimer = 0;
    document.body.classList.remove('pulse-boot', 'pulse-ready');
    for (const sel of CHROME) document.querySelector(sel)?.style.removeProperty('--pulse-i');
  }
  if (workspace && workspace.hidden && isPulse() && !reduce.matches) {
    document.body.classList.add('pulse-boot');
    const watch = new MutationObserver(() => {
      if (workspace.hidden) return;
      watch.disconnect();
      if (!on) return endIntro();
      CHROME.forEach((sel, i) => document.querySelector(sel)?.style.setProperty('--pulse-i', String(i)));
      document.body.classList.remove('pulse-boot');
      document.body.classList.add('pulse-ready');
      introTimer = setTimeout(endIntro, 900); // drop the class so later re-shows never replay
    });
    watch.observe(workspace, { attributes: true, attributeFilter: ['hidden'] });
  }

  /* ── 3. Stage transitions ── */
  let stage = document.body.dataset.stage, stageTimer = 0;
  function clearStage() {
    clearTimeout(stageTimer);
    for (const panel of document.querySelectorAll('.pulse-stage-enter')) panel.classList.remove('pulse-stage-enter');
  }
  new MutationObserver(() => {
    const next = document.body.dataset.stage;
    if (next === stage) return;
    stage = next;
    if (!on || reduce.matches) return;
    clearStage();
    const panel = document.querySelector(`[data-panel="${CSS.escape(next || '')}"]`);
    if (!panel) return;
    void panel.offsetWidth; // restart the animation if the same panel re-enters
    panel.classList.add('pulse-stage-enter');
    stageTimer = setTimeout(clearStage, 900);
  }).observe(document.body, { attributes: true, attributeFilter: ['data-stage'] });

  /* ── Enable / disable with the live design switch ── */
  function sync() {
    const want = isPulse();
    if (want === on) return;
    on = want;
    document.documentElement.classList.toggle('pulse-motion', on);
    if (!on) {
      for (const el of counters.keys()) finish(el);
      clearStage(); endIntro();
    }
  }
  window.addEventListener('scouting-design-change', sync);
  sync();
  window.AgroPulseMotion = { get enabled() { return on; } };
})();
