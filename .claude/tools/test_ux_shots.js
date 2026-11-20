'use strict';
const test = require('node:test');
const { after } = test;
const assert = require('node:assert');
const http = require('node:http');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFile, execSync } = require('node:child_process');

const BAD = `<!doctype html><title>bad</title><meta name=viewport content="width=device-width">
<body style="margin:0;background:#fff"><div style="width:2000px;height:10px;background:#eee" id=wide></div>
<button id=tiny style="width:20px;height:20px">x</button>
<p id=faint style="color:#ccc">faint text</p>
<script>console.error('boom')</script></body>`;
const GOOD = `<!doctype html><title>good</title><meta name=viewport content="width=device-width">
<body style="margin:0;background:#fff;color:#000"><button style="width:80px;height:50px">ok</button><p>fine text</p></body>`;

function serve() {
  return new Promise((res) => {
    const s = http.createServer((q, r) => {
      if (q.url === '/favicon.ico') { r.statusCode = 204; return r.end(); }
      r.setHeader('content-type', 'text/html'); r.end(q.url.startsWith('/good') ? GOOD : BAD);
    }).listen(0, '127.0.0.1', () => res(s));
  });
}

async function run(url) {
  const out = fs.mkdtempSync(path.join(os.tmpdir(), 'uxt-'));
  after(() => fs.rmSync(out, { recursive: true, force: true }));
  const r = await new Promise((res) => execFile('node', [path.join(__dirname, 'ux_shots.js'), '--url', url, '--out', out], (e, so, se) => res({ status: e ? e.code : 0, stderr: se })));
  const f = fs.existsSync(out + '/findings.json') ? JSON.parse(fs.readFileSync(out + '/findings.json')) : [];
  return { r, out, f };
}

test('detects each defect with right kind; clean page yields none; chrome gone', async () => {
  const s = await serve();
  after(() => s.close());
  const base = `http://127.0.0.1:${s.address().port}`;
  const bad = await run(base + '/bad');
  assert.strictEqual(bad.r.status, 0, bad.r.stderr);
  const has = (k, pred = () => true) => bad.f.some((x) => x.kind === k && pred(x));
  assert.ok(has('overflow-x', (x) => x.viewport === 'phone'), 'overflow');
  assert.ok(has('tap-target', (x) => x.selector === '#tiny' && x.viewport === 'phone'), 'tap');
  assert.ok(!bad.f.some((x) => x.kind === 'tap-target' && x.viewport === 'desktop'), 'tap phone only');
  assert.ok(has('contrast', (x) => x.selector === '#faint'), 'contrast');
  assert.ok(has('console', (x) => /boom/.test(x.detail)), 'console');
  assert.ok(fs.existsSync(bad.out + '/landing-phone-dark.png'));
  assert.ok(fs.readFileSync(bad.out + '/SUMMARY.md', 'utf8').split('\n').length <= 40);
  const good = await run(base + '/good');
  assert.strictEqual(good.r.status, 0, good.r.stderr);
  assert.deepStrictEqual(good.f.filter((x) => x.severity !== 'info'), []);
  let left = '';
  try { left = execSync("pgrep -f '[u]ser-data-dir=.*uxs-' || true").toString().trim(); } catch {}
  assert.strictEqual(left, '', 'chrome still running');
});

test('exit 2 when page unreachable', async () => {
  const r = (await run('http://127.0.0.1:1/')).r;
  assert.strictEqual(r.status, 2);
});
