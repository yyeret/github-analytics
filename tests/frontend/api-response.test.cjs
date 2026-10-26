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
  const providerInputs = {'typesafe-key': {value: ''}, 'gemini-key': {value: ''}};
  const context = vm.createContext({
    document: {getElementById(id) {
      return providerInputs[id] || (id === 'repo-input' ? {value: 'https://github.com/github/spec-kit'} : button);
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

test('an empty-description PR displays its reason without a score beside assessed PRs', () => {
  const elements = Object.fromEntries(['product-value-card', 'product-value-note', 'product-value-context', 'product-value-items']
    .map(id => [id, {style: {}, innerHTML: '', textContent: ''}]));
  const context = vm.createContext({document: {getElementById: id => elements[id]}});
  vm.runInContext(html.slice(html.indexOf('  function escapeHtml('), html.indexOf('  function renderIssues(')), context);
  vm.runInContext(html.slice(html.indexOf('  function renderProductValue('), html.indexOf('  let valueClassification')), context);
  context.renderProductValue({status: 'ok', assessed: 1, failed: 1, selected: 2,
    dimensions: {value: 'Value'}, items: [
      {number: 1, title: 'Scored', cohort: 'merged', status: 'assessed', cost_of_delay: 4,
        dimensions: {value: {score: 1, rubric: 'Routine change', probabilities: {'1': 1}}}},
      {number: 2, title: 'Empty description', cohort: 'merged', status: 'failed', cost_of_delay: null,
        error_code: 'insufficient_context', reason: 'The PR description is empty; there is insufficient context to assess its value.'},
    ]});
  const table = elements['product-value-items'].innerHTML;
  assert.match(table, /<strong>4<\/strong>/);
  const unscored = table.slice(table.indexOf('#2'), table.indexOf('</tr>', table.indexOf('#2')));
  assert.match(unscored, /Not scored: The PR description is empty/);
  assert.doesNotMatch(unscored, /<strong>|null|NaN/);
});
