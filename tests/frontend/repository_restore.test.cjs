const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '..', '..', 'index.html'), 'utf8');
const script = html.match(/<script>\s*([\s\S]*?)<\/script>/)[1];

function loadPage(storage, requests, cacheHit = false, failRequest = false, authState = 'none') {
  const elements = new Map();
  let renderCalls = 0;
  const element = id => {
    if (!elements.has(id)) {
      elements.set(id, {
        value: '', textContent: '', disabled: false, style: {},
        classList: { add() {}, remove() {} },
        listeners: {},
        addEventListener(type, callback) { this.listeners[type] = callback; },
      });
    }
    return elements.get(id);
  };
  const context = {
    window: { location: { search: '' } },
    document: {
      getElementById: element,
      createElement: () => ({}),
      head: { appendChild() {} },
    },
    localStorage: storage,
    URLSearchParams,
    Chart: class { static defaults = { font: {} }; destroy() {} },
    fetch: async (url, options) => {
      if (url === '/api/auth/config') {
        return { json: async () => ({
          required: authState !== 'none',
          client_id: authState !== 'none' ? 'test-client' : '',
        }) };
      }
      if (url === '/api/auth/session') {
        return { json: async () => ({ signed_in: authState === 'signed-in', email: 'test@example.com' }) };
      }
      if (url === '/api/auth/nonce') {
        return { json: async () => ({ nonce: 'test-nonce' }) };
      }
      const request = JSON.parse(options.body);
      requests.push(request);
      if (failRequest) {
        return { ok: false, json: async () => ({ detail: 'GitHub unavailable' }) };
      }
      return {
        ok: true,
        json: async () => ({
          repo: 'owner/alpha', recent_stats: { total: 4 },
          captured_at: '2026-10-15T10:00:00',
          cache_hit: cacheHit && !request.force_refresh,
        }),
      };
    },
    setTimeout: () => 0,
  };
  vm.runInNewContext(script, context);
  context.renderDashboard = () => { renderCalls += 1; };
  context.addChatMessage = () => {};
  return { context, element, renderCalls: () => renderCalls };
}

test('reload reopens the last repository and refresh requests new data', async () => {
  const values = new Map();
  const storage = {
    getItem: key => values.get(key) || null,
    setItem: (key, value) => values.set(key, value),
  };
  const requests = [];
  const first = loadPage(storage, requests);
  first.element('repo-input').value = 'owner/alpha';
  await first.context.analyzeRepo();
  assert.equal(values.get('lastAnalyzedRepo'), 'owner/alpha');
  assert.deepEqual(requests[0], { repo_url: 'owner/alpha', force_refresh: false });

  const reopened = loadPage(storage, requests, true);
  await new Promise(setImmediate);
  assert.deepEqual(requests[1], { repo_url: 'owner/alpha', force_refresh: false });
  assert.equal(reopened.renderCalls(), 1);
  assert.match(reopened.element('capture-note').textContent, /Saved GitHub data/);

  await reopened.context.refreshRepo();
  assert.deepEqual(requests[2], { repo_url: 'owner/alpha', force_refresh: true });
  assert.match(reopened.element('capture-note').textContent, /Fresh GitHub data/);
});

test('unavailable browser storage leaves manual analysis usable', async () => {
  const storage = {
    getItem() { throw new Error('storage blocked'); },
    setItem() { throw new Error('storage blocked'); },
  };
  const requests = [];
  const page = loadPage(storage, requests);
  page.element('repo-input').value = 'owner/alpha';
  await page.context.analyzeRepo();
  assert.equal(requests.length, 1);
});

test('failed automatic restore leaves the form available', async () => {
  const storage = { getItem: () => 'owner/alpha', setItem() {} };
  const page = loadPage(storage, [], false, true);
  await new Promise(setImmediate);
  assert.equal(page.element('analyze-btn').disabled, false);
  assert.match(page.element('status-bar').textContent, /GitHub unavailable/);
  assert.equal(page.element('repo-input').value, 'owner/alpha');
});

test('signed-out reload waits for sign-in before reopening saved analysis', async () => {
  const storage = { getItem: () => 'owner/alpha', setItem() {} };
  const requests = [];
  const page = loadPage(storage, requests, true, false, 'signed-out');
  await new Promise(setImmediate);
  assert.equal(requests.length, 0);

  page.context.completeSignIn('test@example.com');
  await new Promise(setImmediate);
  assert.deepEqual(requests[0], { repo_url: 'owner/alpha', force_refresh: false });
});

test('a newer repository request wins if the restored request finishes later', async () => {
  const saved = [];
  const storage = {
    getItem: () => null,
    setItem: (_key, value) => saved.push(value),
  };
  const page = loadPage(storage, []);
  await new Promise(setImmediate);
  const pending = [];
  page.context.fetch = async (url, options) => new Promise(resolve => {
    pending.push({ request: JSON.parse(options.body), resolve });
  });

  page.element('repo-input').value = 'owner/alpha';
  const first = page.context.analyzeRepo();
  page.element('repo-input').value = 'owner/beta';
  const second = page.context.analyzeRepo();

  pending[1].resolve({
    ok: true,
    json: async () => ({
      repo: 'owner/beta', recent_stats: { total: 2 },
      captured_at: '2026-10-15T10:00:00', cache_hit: false,
    }),
  });
  await second;
  pending[0].resolve({
    ok: true,
    json: async () => ({
      repo: 'owner/alpha', recent_stats: { total: 1 },
      captured_at: '2026-10-15T09:00:00', cache_hit: true,
    }),
  });
  await first;

  assert.deepEqual(pending.map(item => item.request.repo_url), ['owner/alpha', 'owner/beta']);
  assert.equal(page.element('repo-input').value, 'owner/beta');
  assert.equal(saved.at(-1), 'owner/beta');
  assert.equal(page.renderCalls(), 1);
});

test('editing the input before auth finishes suppresses automatic restore', async () => {
  const storage = { getItem: () => 'owner/alpha', setItem() {} };
  const requests = [];
  const page = loadPage(storage, requests);
  page.element('repo-input').value = 'owner/beta';
  page.element('repo-input').listeners.input();
  await new Promise(setImmediate);
  assert.equal(page.element('repo-input').value, 'owner/beta');
  assert.equal(requests.length, 0);
});
