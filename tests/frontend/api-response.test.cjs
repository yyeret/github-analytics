const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.resolve(__dirname, '../../index.html'), 'utf8');
const start = html.indexOf('// ── Analyze Repo');
const end = html.indexOf('// ── Dashboard Renderer', start);

function dashboard(response) {
  const statuses = [];
  const button = {classList: {add() {}, remove() {}}};
  const context = vm.createContext({
    document: {getElementById(id) {
      return id === 'repo-input' ? {value: 'https://github.com/github/spec-kit'} : button;
    }},
    fetch: async () => response,
    showStatus: (text) => statuses.push(text),
    showLoading() {}, hideLoading() {}, clearStatus() {},
  });
  vm.runInContext(html.slice(start, end), context);
  return {context, statuses, button};
}

test('plain-text server failure shows HTTP status instead of a JSON parser error', async () => {
  const {context, statuses, button} = dashboard(new Response('Internal Server Error', {status: 500}));
  await context.analyzeRepo();
  assert.match(statuses.at(-1), /HTTP 500/);
  assert.doesNotMatch(statuses.at(-1), /Unexpected token|not valid JSON/);
  assert.equal(button.disabled, false);
});

test('structured API error retains its useful detail', async () => {
  const response = new Response(JSON.stringify({detail: 'Sign in with Google to continue.'}), {status: 401});
  const {context, statuses} = dashboard(response);
  await context.analyzeRepo();
  assert.match(statuses.at(-1), /Sign in with Google/);
});

test('successful JSON is returned without alteration', async () => {
  const {context} = dashboard();
  const result = await context.readApiResponse(new Response('{"repo":"github/spec-kit"}'));
  assert.equal(result.repo, 'github/spec-kit');
});

test('invalid successful response is reported without exposing its body', async () => {
  const {context} = dashboard();
  await assert.rejects(context.readApiResponse(new Response('private-body-sentinel')), /invalid response.*HTTP 200/i);
});
