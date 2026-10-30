const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { runInNewContext } = require('node:vm');
const code = readFileSync('src/copydesk/static/js/blog-transfer.js', 'utf8');
const source = 'https://blog.example.test/articles/original/';
const key = 'quillsail-blog-transfer';
const payload = (body, changes = {}) => '#quillsail=' + Buffer.from(JSON.stringify({
  version: 1, source, body, ...changes,
})).toString('base64url');

function visit(hash, { fill = true, storage = new Map(), blocked = false, value = 'Old excerpt' } = {}) {
  const status = { textContent: '' };
  const body = { value };
  let removed = false;
  const receiver = {
    dataset: { transferFill: fill ? 'true' : undefined },
    querySelector: name => name === '[data-transfer-status]' ? status : body,
  };
  const context = {
    document: { querySelector: () => receiver },
    location: { search: '?source=' + encodeURIComponent(source), pathname: '/community/write/from-blog', hash },
    sessionStorage: {
      getItem: name => { if (blocked) throw Error(); return storage.get(name); },
      setItem: (name, value) => { if (blocked) throw Error(); storage.set(name, value); },
      removeItem: name => { if (blocked) throw Error(); storage.delete(name); },
    },
    history: { replaceState: () => { removed = true; } },
    atob, TextDecoder, Uint8Array, URLSearchParams,
  };
  runInNewContext(code, context);
  return { body: body.value, status: status.textContent, removed, storage };
}

test('full Unicode Markdown transfers and fragment/storage are cleared', () => {
  const text = 'مرحبا 🌊 café **hello** <script>never execute</script>'.repeat(2000);
  const page = visit(payload(text));
  assert.equal(page.body, text);
  assert.match(page.status, /full article was transferred/);
  assert.equal(page.removed, true);
  assert.equal(page.storage.size, 0);
});

test('signed-out handoff survives same-tab sign-in and is consumed once', () => {
  const first = visit(payload('Full original text'), { fill: false });
  assert.equal(first.body, 'Old excerpt');
  assert.match(first.status, /Sign in/);
  const second = visit('', { storage: first.storage });
  assert.equal(second.body, 'Full original text');
  assert.equal(second.storage.size, 0);
  assert.equal(visit('', { storage: second.storage }).body, 'Old excerpt');
});

test('bad, oversized or mismatched data never overwrites the form', () => {
  for (const hash of ['#quillsail=!!!', payload('x', { source: 'https://other.example/' }),
    payload('x', { version: 2 }), payload(null), payload('x'.repeat(200001)), '#quillsail=' + 'A'.repeat(1100001)]) {
    const result = visit(hash);
    assert.equal(result.body, 'Old excerpt');
    assert.match(result.status, /could not be transferred/);
    assert.equal(result.storage.size, 0);
    assert.equal(result.removed, true);
  }
});

test('expired or different-destination saved transfers are not restored', () => {
  for (const overrides of [{ savedAt: Date.now() - 31 * 60 * 1000 },
    { path: '/different/write/from-blog' }, { source: 'https://other.example/' }]) {
    const storage = new Map([[key, JSON.stringify({
      body: 'Should not be used', version: 1, source,
      savedAt: Date.now(), path: '/community/write/from-blog', ...overrides,
    })]]);
    const page = visit('', { storage });
    assert.equal(page.body, 'Old excerpt');
    assert.equal(storage.size, 0);
  }
});

test('a validation response never overwrites submitted edits', () => {
  const first = visit(payload('Original'), { fill: false });
  assert.equal(visit('', { fill: false, storage: first.storage, value: 'Writer edits' }).body, 'Writer edits');
});

test('blocked storage still allows immediate transfer, but explains sign-in fallback', () => {
  assert.equal(visit(payload('Original'), { blocked: true }).body, 'Original');
  assert.match(visit(payload('Original'), { blocked: true, fill: false }).status, /could not be transferred/);
});
