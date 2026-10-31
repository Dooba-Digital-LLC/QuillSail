// Run with node --test tests/edge_fallback.cjs. No network or Cloudflare account.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
globalThis.crypto ||= require('node:crypto').webcrypto;
const source = fs.readFileSync(path.join(__dirname, '../src/copydesk/edge/fallback.js'), 'utf8');
const worker = import('data:text/javascript;base64,' + Buffer.from(source).toString('base64')).then(m => m.default);
function fixture() {
  const values = new Map([['snapshot', '<h1>Launching soon</h1>']]);
  const env = {HOST: 'publication.example.test', QUEUE: {get: async k => values.get(k), put: async (k, v) => values.set(k, v)},
    LIMITER: {limit: async () => ({success: true})}};
  return {env, values};
}
function request(url = '/', data, extra = {}) {
  return new Request('https://publication.example.test' + url, data === undefined ? extra : {method: 'POST',
    headers: {'Content-Type': 'application/x-www-form-urlencoded', 'CF-Connecting-IP': '192.0.2.1'}, body: data, ...extra});
}
test('passes online requests through without storing subscriber actions', async () => {
  const {env, values} = fixture(); global.fetch = async () => new Response('origin response');
  assert.equal(await (await (await worker).fetch(request('/subscribe', 'email=reader@example.test'), env)).text(), 'origin response');
  assert.equal(values.size, 1);
});
test('offline snapshot is public; private and other paths fail closed', async () => {
  const {env} = fixture(); global.fetch = async () => new Response('', {status: 530});
  assert.match(await (await (await worker).fetch(request(), env)).text(), /Launching soon/);
  for (const url of ['/dashboard/', '/login', '/article/private', '/api/v1/articles'])
    assert.equal((await (await worker).fetch(request(url), env)).status, 503);
});
test('saves one-click unsubscribe and sign-up with received timestamps', async () => {
  const {env, values} = fixture(); global.fetch = async () => {throw Error('offline')};
  const token = 'synthetic-reader-token-long-enough';
  const response = await (await worker).fetch(request('/unsubscribe/' + token, 'List-Unsubscribe=One-Click'), env);
  assert.equal(response.status, 200); assert.match(await response.text(), /You're unsubscribed/);
  await (await worker).fetch(request('/subscribe', 'email=reader%40example.test'), env);
  const entries = [...values].filter(([k]) => k.startsWith('action:')).map(([, v]) => JSON.parse(v));
  assert.equal(entries.length, 2); assert.equal(entries[0].token, token);
  assert.equal(entries[1].email, 'reader@example.test'); assert.ok(entries.every(e => !isNaN(Date.parse(e.received))));
});
test('rate limits, size caps, invalid addresses, and failed storage do not promise success', async () => {
  const {env, values} = fixture(); global.fetch = async () => new Response('', {status: 522});
  env.LIMITER.limit = async () => ({success: false});
  assert.equal((await (await worker).fetch(request('/subscribe', 'email=reader@example.test'), env)).status, 429);
  env.LIMITER.limit = async () => ({success: true});
  assert.equal((await (await worker).fetch(request('/subscribe', 'email=' + 'a'.repeat(5000)), env)).status, 413);
  assert.equal((await (await worker).fetch(request('/subscribe', 'email=invalid'), env)).status, 422);
  env.QUEUE.put = async () => {throw Error('quota exceeded')};
  const response = await (await worker).fetch(request('/subscribe', 'email=reader@example.test'), env);
  assert.equal(response.status, 503); assert.equal(values.size, 1);
});
test('offline manage links allow opt-out but never change preferences or resubscribe', async () => {
  const {env, values} = fixture(); global.fetch = async () => new Response('', {status: 530});
  const url = '/subscription/synthetic-reader-token-long-enough';
  assert.match(await (await (await worker).fetch(request(url), env)).text(), /Unsubscribe/);
  assert.equal((await (await worker).fetch(request(url, 'action=resubscribe'), env)).status, 503);
  assert.equal((await (await worker).fetch(request(url, 'action=unsubscribe'), env)).status, 200);
  assert.equal([...values].filter(([k]) => k.startsWith('action:')).length, 1);
});
