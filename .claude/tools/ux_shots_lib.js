'use strict';
/** analyzeSource(mobile): runs in the page; returns {meta:{title,viewport}, findings:[{kind,selector,detail,severity}]}. */
function analyzeSource(mobile) {
  const F = [];
  const add = (kind, el, detail, severity = 'warn') => {
    if (F.filter((f) => f.kind === kind).length < 25) F.push({ kind, selector: el ? sel(el) : '', detail, severity });
  };
  const sel = (e) => {
    if (e.id) return '#' + e.id;
    const c = typeof e.className === 'string' ? e.className.trim().split(/\s+/).filter(Boolean).slice(0, 2).join('.') : '';
    return e.tagName.toLowerCase() + (c ? '.' + c : '');
  };
  const visible = (e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const all = [...document.body.querySelectorAll('*')].filter(visible);
  const iw = window.innerWidth;

  if (document.documentElement.scrollWidth > iw) {
    let widest = null, w = 0;
    for (const e of all) { const r = e.getBoundingClientRect(); if (r.right > iw && r.right > w) { w = r.right; widest = e; } }
    add('overflow-x', widest, `scrollWidth ${document.documentElement.scrollWidth} > innerWidth ${iw}`, 'error');
  }
  for (const e of all) {
    const p = e.parentElement, r = e.getBoundingClientRect();
    if (!p || p === document.body || getComputedStyle(e).position === 'fixed') continue;
    const pr = p.getBoundingClientRect(), ps = getComputedStyle(p);
    if (ps.display === 'contents' || pr.width === 0) continue;
    if (r.right > pr.right + 1 || r.left < pr.left - 1) {
      const clipped = ps.overflowX !== 'visible';
      add('clipped', e, `${clipped ? 'clipped by' : 'overflows'} parent ${sel(p)} by ${Math.round(Math.max(r.right - pr.right, pr.left - r.left))}px`);
    }
  }
  if (mobile) {
    for (const e of all.filter((x) => x.matches('a[href],button,input,select,textarea,[role=button],[onclick]'))) {
      const r = e.getBoundingClientRect();
      if (r.width < 44 || r.height < 44) add('tap-target', e, `${Math.round(r.width)}x${Math.round(r.height)} < 44x44`);
    }
  }
  const rgba = (s) => { const m = s.match(/[\d.]+/g).map(Number); return { r: m[0], g: m[1], b: m[2], a: m.length > 3 ? m[3] : 1 }; };
  const lum = ({ r, g, b }) => [r, g, b].map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }).reduce((s, v, i) => s + v * [0.2126, 0.7152, 0.0722][i], 0);
  const mix = (f, b) => ({ r: f.r * f.a + b.r * (1 - f.a), g: f.g * f.a + b.g * (1 - f.a), b: f.b * f.a + b.b * (1 - f.a), a: 1 });
  const bgOf = (e) => {
    const layers = [];
    for (let n = e; n; n = n.parentElement) {
      const s = getComputedStyle(n);
      if (s.backgroundImage !== 'none') return null;
      const c = rgba(s.backgroundColor);
      if (c.a > 0) { layers.push(c); if (c.a === 1) break; }
    }
    let base = { r: 255, g: 255, b: 255, a: 1 };
    for (const l of layers.reverse()) base = mix(l, base);
    return base;
  };
  for (const e of all) {
    const own = [...e.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
    if (!own) continue;
    const s = getComputedStyle(e), bg = bgOf(e);
    if (!bg) { add('contrast-unknown', e, 'background is image/gradient', 'info'); continue; }
    const fg = mix(rgba(s.color), bg);
    const [a, b] = [lum(fg), lum(bg)].sort((x, y) => y - x);
    const ratio = (a + 0.05) / (b + 0.05);
    const px = parseFloat(s.fontSize), large = px >= 24 || (px >= 18.66 && Number(s.fontWeight) >= 700);
    if (ratio < (large ? 3 : 4.5)) add('contrast', e, `ratio ${ratio.toFixed(2)} < ${large ? 3 : 4.5}`);
  }
  const vm = document.querySelector('meta[name=viewport]');
  if (!vm) add('viewport-meta', null, 'missing viewport meta');
  return { meta: { title: document.title, viewport: vm ? vm.content : null }, findings: F };
}

/** summarize(results, viewports): SUMMARY.md text, <= 40 lines. */
function summarize(results, vps) {
  const counts = {};
  const flagged = [];
  for (const r of results) {
    const real = r.findings.filter((f) => f.severity !== 'info');
    real.forEach((f) => { counts[f.kind] = (counts[f.kind] || 0) + 1; });
    if (real.length) flagged.push(`${r.file} (${real.length})`);
  }
  const key = [];
  for (const vp of vps) {
    const rs = results.filter((r) => r.file.includes(`-${vp.name}-`));
    if (rs[0]) key.push(rs[0].file);
    const first = rs.find((r) => !r.file.startsWith('landing-'));
    if (first) key.push(first.file);
  }
  const title = results[0] && results[0].meta ? results[0].meta.title : '';
  const L = [`# UX shots: ${results.length} screenshots, title "${title}"`, '', '## Counts per kind'];
  const ks = Object.keys(counts);
  L.push(...(ks.length ? ks.map((k) => `- ${k}: ${counts[k]}`) : ['- none']));
  L.push('', `## Flagged (${flagged.length})`, ...flagged.slice(0, 15).map((f) => '- ' + f));
  if (flagged.length > 15) L.push(`- ... ${flagged.length - 15} more in findings.json`);
  L.push('', '## Key screens', ...[...new Set(key)].slice(0, 8).map((f) => '- ' + f));
  return L.slice(0, 40).join('\n') + '\n';
}

module.exports = { analyzeSource: analyzeSource.toString(), summarize };
