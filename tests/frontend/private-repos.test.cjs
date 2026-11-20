const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.resolve(__dirname, '../../index.html'), 'utf8');

function makeElement(tag = 'div') {
  const listeners = {};
  return {
    tagName: tag.toUpperCase(),
    hidden: false,
    disabled: false,
    checked: false,
    value: '',
    textContent: '',
    className: '',
    title: '',
    type: '',
    children: [],
    focused: false,
    classList: {
      classes: new Set(),
      add(c) { this.classes.add(c); },
      remove(c) { this.classes.delete(c); },
    },
    appendChild(child) {
      this.children.push(child);
      return child;
    },
    replaceChildren(...nodes) {
      this.children = [...nodes];
    },
    addEventListener(type, fn) {
      listeners[type] = fn;
    },
    click() {
      if (listeners.click) listeners.click();
    },
    focus() {
      this.focused = true;
    },
  };
}

function loadAuthAndPicker(fetchImpl) {
  const start = html.indexOf('  // ── State');
  const end = html.indexOf('  // ── Analyze Repo');
  assert.ok(start >= 0 && end > start, 'Expected script markers in index.html');

  const ids = [
    'login-gate', 'account-actions', 'account-email', 'analyze-btn',
    'github-connect', 'repo-input', 'allow-private',
    'private-repos-section', 'private-repos-status', 'private-repos-list',
  ];
  const elements = Object.fromEntries(ids.map(id => [id, makeElement()]));
  elements['private-repos-section'].hidden = true;

  const statuses = [];
  const analyzed = [];
  const context = vm.createContext({
    document: {
      getElementById: (id) => elements[id] || makeElement(),
      createElement: (tag) => makeElement(tag),
    },
    fetch: fetchImpl,
    readApiResponse: async (res) => {
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      return data;
    },
    showStatus: (msg, type) => statuses.push({msg, type}),
    analyzeRepo: (saved) => analyzed.push(saved || elements['repo-input'].value),
    localStorage: {getItem: () => null, setItem() {}, removeItem() {}},
    Date,
    Intl,
  });
  vm.runInContext(html.slice(start, end), context);
  return {context, elements, statuses, analyzed};
}

test('GitHub sign-in loads active private repositories and hides Connect GitHub', async () => {
  const urls = [];
  const {context, elements} = loadAuthAndPicker(async (url) => {
    urls.push(url);
    return new Response(JSON.stringify({
      login: 'yyeret',
      repos: [
        {
          full_name: 'yyeret/active-one',
          description: '<img src=x onerror=alert(1)>',
          pushed_at: new Date(Date.now() - 2 * 3600 * 1000).toISOString(),
          language: 'Python',
        },
      ],
    }), {status: 200});
  });

  context.completeSignIn({signed_in: true, provider: 'github', login: 'yyeret', account_key: 'github:abc'});
  await new Promise(resolve => setTimeout(resolve, 10));

  assert.deepEqual(urls, ['/api/github/repos']);
  assert.equal(elements['github-connect'].hidden, true);
  assert.equal(elements['private-repos-section'].hidden, false);
  assert.equal(elements['private-repos-list'].children.length, 1);
  const pill = elements['private-repos-list'].children[0];
  assert.equal(pill.children[0].textContent, 'yyeret/active-one');
  assert.match(pill.children[1].textContent, /2h ago/);
  assert.equal(pill.title, '<img src=x onerror=alert(1)>');
});

test('Google sign-in does not fetch private GitHub repositories', async () => {
  const urls = [];
  const {context, elements} = loadAuthAndPicker(async (url) => {
    urls.push(url);
    return new Response('{}', {status: 200});
  });

  context.completeSignIn({signed_in: true, email: 'user@example.com', account_key: 'google:123'});
  await new Promise(resolve => setTimeout(resolve, 10));

  assert.deepEqual(urls, []);
  assert.equal(elements['private-repos-section'].hidden, true);
});

test('clicking a private repo requires explicit consent before analyzing', async () => {
  const {context, elements, statuses, analyzed} = loadAuthAndPicker(async () => new Response(JSON.stringify({
    login: 'yyeret',
    repos: [{full_name: 'yyeret/secret-repo', pushed_at: new Date().toISOString()}],
  }), {status: 200}));

  await context.loadPrivateRepositories();
  const pill = elements['private-repos-list'].children[0];

  // Consent unchecked: fills input, focuses checkbox, does NOT auto-check or analyze
  pill.click();
  assert.equal(elements['repo-input'].value, 'yyeret/secret-repo');
  assert.equal(elements['allow-private'].checked, false);
  assert.equal(elements['allow-private'].focused, true);
  assert.deepEqual(analyzed, []);
  assert.match(statuses.at(-1).msg, /Allow private analysis/);

  // Consent checked: immediately runs analyzeRepo
  elements['allow-private'].checked = true;
  pill.click();
  assert.deepEqual(analyzed, ['yyeret/secret-repo']);
});
