#!/usr/bin/env node
/* ux_shots: screenshot matrix + numeric layout findings for UX review.
 * node ux_shots.js --url http://127.0.0.1:8000/ --out DIR
 *      [--viewports desktop=1280x800,phone=390x844] [--themes light,dark] [--steps steps.json]
 * steps.json: [{name, actions:[{click:css}|{type:{sel,text}}|{press:"Enter"}|{wait:ms}|{eval:js}]}]
 * Without --steps: landing plus a walk over up to 8 same-origin buttons/links.
 * Writes DIR/<state>-<viewport>-<theme>.png, DIR/findings.json, DIR/SUMMARY.md.
 * Findings: {state, viewport, theme, kind, selector, detail, severity}.
 * Viewport names containing phone or mobile get touch + mobile emulation and the tap-target check.
 * Exit 0 on finished run, 2 on tool failure (no chrome, page unreachable). */
'use strict';
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const { analyzeSource, summarize } = require('./ux_shots_lib.js');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function parseArgs(argv) {
  const a = { viewports: 'desktop=1280x800,phone=390x844', themes: 'light,dark' };
  for (let i = 0; i < argv.length; i += 2) a[argv[i].replace(/^--/, '')] = argv[i + 1];
  return a;
}

function freePort() {
  return new Promise((res, rej) => {
    const s = net.createServer().listen(0, '127.0.0.1', () => {
      const p = s.address().port;
      s.close(() => res(p));
    }).on('error', rej);
  });
}

/** Minimal CDP session over a page websocket. */
class Cdp {
  constructor(ws) { this.ws = ws; this.id = 0; this.pending = new Map(); this.handlers = []; }
  static open(url) {
    return new Promise((res, rej) => {
      const ws = new WebSocket(url);
      const c = new Cdp(ws);
      ws.onopen = () => res(c);
      ws.onerror = () => rej(new Error('websocket failed'));
      ws.onmessage = (m) => {
        const d = JSON.parse(m.data);
        if (d.id && c.pending.has(d.id)) {
          const [ok, bad] = c.pending.get(d.id); c.pending.delete(d.id);
          d.error ? bad(new Error(d.error.message)) : ok(d.result);
        } else if (d.method) c.handlers.forEach((h) => h(d.method, d.params));
      };
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((ok, bad) => this.pending.set(id, [ok, bad]));
  }
  on(h) { this.handlers.push(h); }
  async eval(expr) {
    const r = await this.send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.text + ' ' + (r.exceptionDetails.exception?.description || ''));
    return r.result.value;
  }
}

async function launchChrome() {
  const port = await freePort();
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'uxs-'));
  const exe = fs.existsSync('/usr/local/bin/chrome-nonroot') ? '/usr/local/bin/chrome-nonroot' : '/usr/bin/google-chrome';      // non-root browser user when set up
  fs.chmodSync(dir, 0o777);
  const proc = spawn(exe, ['--headless=new', '--no-sandbox', '--disable-gpu',
    `--remote-debugging-port=${port}`, `--user-data-dir=${dir}`, 'about:blank'], { stdio: 'ignore', detached: true });
  proc.on('error', () => {});
  for (let i = 0; i < 100; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/json/version`);
      if (r.ok) return { proc, port, dir };
    } catch {}
    await sleep(100);
  }
  try { process.kill(-proc.pid, 'SIGKILL'); } catch {}
  fs.rmSync(dir, { recursive: true, force: true });
  throw new Error('chrome did not start');
}

async function openPage(port) {
  const r = await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' });
  const t = await r.json();
  const c = await Cdp.open(t.webSocketDebuggerUrl);
  for (const d of ['Page', 'Runtime', 'Log', 'Network']) await c.send(`${d}.enable`);
  return c;
}

async function setup(c, vp, theme) {
  const mobile = /phone|mobile/i.test(vp.name);
  await c.send('Emulation.setDeviceMetricsOverride', { width: vp.w, height: vp.h, deviceScaleFactor: 1, mobile });
  await c.send('Emulation.setTouchEmulationEnabled', { enabled: mobile });
  await c.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: theme }] });
  return mobile;
}

function collector(c) {
  const out = { items: [], loaded: false };
  const add = (kind, detail) => out.items.push({ kind, selector: '', detail, severity: 'warn' });
  c.on((m, p) => {
    if (m === 'Page.loadEventFired') out.loaded = true;
    else if (m === 'Runtime.consoleAPICalled' && /error|warning/.test(p.type))
      add('console', `${p.type}: ${p.args.map((a) => a.value ?? a.description).join(' ')}`);
    else if (m === 'Runtime.exceptionThrown') add('console', 'exception: ' + (p.exceptionDetails.exception?.description || p.exceptionDetails.text));
    else if (m === 'Log.entryAdded' && /error|warning/.test(p.entry.level)) add('console', `${p.entry.level}: ${p.entry.text} ${p.entry.url || ''}`);
    else if (m === 'Network.loadingFailed' && !p.canceled) add('request', `failed: ${p.errorText}`);
    else if (m === 'Network.responseReceived' && p.response.status >= 400) add('request', `${p.response.status} ${p.response.url}`);
  });
  return out;
}

async function runAction(c, a) {
  if (a.click) await c.eval(`document.querySelector(${JSON.stringify(a.click)}).click()`);
  else if (a.type) {
    await c.eval(`(()=>{const e=document.querySelector(${JSON.stringify(a.type.sel)});e.focus();e.value=${JSON.stringify(a.type.text)};e.dispatchEvent(new Event('input',{bubbles:true}));})()`);
  } else if (a.press) {
    for (const t of ['keyDown', 'keyUp']) await c.send('Input.dispatchKeyEvent', { type: t, key: a.press, code: a.press, text: t === 'keyDown' && a.press === 'Enter' ? '\r' : undefined });
  } else if (a.wait) await sleep(a.wait);
  else if (a.eval) await c.eval(a.eval);
  await sleep(150);
}

async function loadState(c, col, url, actions) {
  col.loaded = false; col.items.length = 0;
  const nav = await c.send('Page.navigate', { url });
  if (nav.errorText) throw new Error('page unreachable: ' + url + ' ' + nav.errorText);
  for (let i = 0; i < 100 && !col.loaded; i++) await sleep(100);
  if (!col.loaded) throw new Error('page unreachable: ' + url);
  await sleep(300);
  for (const a of actions) await runAction(c, a);
}

async function capture(c, col, o, state, vp, theme, mobile, url, actions) {
  await loadState(c, col, url, actions);
  const f = await c.eval(`(${analyzeSource})(${mobile})`);
  const file = `${state}-${vp.name}-${theme}.png`;
  const shot = await c.send('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync(path.join(o.out, file), Buffer.from(shot.data, 'base64'));
  const all = [...f.findings, ...col.items];
  return { file, meta: f.meta, findings: all.map((x) => ({ state, viewport: vp.name, theme, ...x })) };
}

async function main() {
  const o = parseArgs(process.argv.slice(2));
  if (!o.url || !o.out) throw new Error('usage: --url URL --out DIR');
  fs.mkdirSync(o.out, { recursive: true });
  const vps = o.viewports.split(',').map((s) => { const [n, d] = s.split('='); const [w, h] = d.split('x').map(Number); return { name: n, w, h }; });
  const themes = o.themes.split(',');
  const steps = o.steps ? JSON.parse(fs.readFileSync(o.steps, 'utf8')) : null;
  const chrome = await launchChrome();
  const results = [];
  try {
    const c = await openPage(chrome.port);
    const col = collector(c);
    for (const vp of vps) for (const theme of themes) {
      const mobile = await setup(c, vp, theme);
      let states = steps || [{ name: 'landing', actions: [] }];
      if (!steps) {
        await loadState(c, col, o.url, []);
        const cands = await c.eval(`(${walkSource})(${JSON.stringify(new URL(o.url).origin)})`);
        states = states.concat(cands.map((sel, i) => ({ name: `walk${i + 1}`, actions: [{ click: sel }] })));
      }
      for (const s of states) results.push(await capture(c, col, o, s.name, vp, theme, mobile, o.url, s.actions));
    }
  } finally {
    try { process.kill(-chrome.proc.pid, 'SIGKILL'); } catch {}
    await sleep(100);
    fs.rmSync(chrome.dir, { recursive: true, force: true });
  }
  const findings = results.flatMap((r) => r.findings);
  fs.writeFileSync(path.join(o.out, 'findings.json'), JSON.stringify(findings, null, 1));
  fs.writeFileSync(path.join(o.out, 'SUMMARY.md'), summarize(results, vps));
}

/** In-page: selectors of up to 8 visible same-origin buttons/links (skips links leaving the origin). */
function walkSource(origin) {
  const path = (e) => { const p = []; for (; e && e.nodeType === 1 && e !== document.documentElement; e = e.parentElement) p.unshift(`${e.tagName.toLowerCase()}:nth-child(${[...e.parentElement.children].indexOf(e) + 1})`); return 'html>' + p.join('>'); };
  const out = [];
  for (const e of document.querySelectorAll('button,a[href],[role=button]')) {
    const r = e.getBoundingClientRect();
    if (!r.width || !r.height || e.disabled) continue;
    if (e.tagName === 'A' && e.origin !== origin) continue;
    out.push(path(e));
    if (out.length >= 8) break;
  }
  return out;
}

main().then(() => process.exit(0), (e) => { console.error('ux_shots failed: ' + e.message); process.exit(2); });
