'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function loadRenderCFD() {
  const html = fs.readFileSync(path.resolve(__dirname, '../../index.html'), 'utf8');
  const start = html.indexOf('  function renderCFD(');
  const end = html.indexOf('  // ── Control Chart', start);
  assert.ok(start !== -1 && end > start, 'renderCFD function not found in index.html');
  const snippet = html.slice(start, end);
  let captured = null;
  const ctx = {
    mkChart: (id, config) => { captured = { id, config }; },
  };
  vm.runInNewContext(`${snippet}\nthis.renderCFD = renderCFD;`, ctx);
  return { renderCFD: ctx.renderCFD, getCaptured: () => captured };
}

test('PR CFD stacks Merged at the bottom with In Review and earlier stages above', () => {
  const { renderCFD, getCaptured } = loadRenderCFD();
  renderCFD([
    { month: '2026-11-01', merged: 10, review: 3, coding: 2, backlog: 0, opened: 15 },
    { month: '2026-11-08', merged: 25, review: 5, coding: 1, backlog: 0, opened: 31 },
  ]);
  const { id, config } = getCaptured();
  assert.equal(id, 'chart-cfd');
  assert.equal(config.options.scales.y.stacked, true);
  assert.deepEqual(Array.from(config.data.datasets, d => d.label), ['Merged', 'In Review', 'Coding']);
  assert.equal(config.data.datasets[0].fill, 'origin');
  assert.equal(config.data.datasets[1].fill, '-1');
  assert.equal(config.data.datasets[2].fill, '-1');
});

test('PR CFD omits empty Coding and Backlog layers while keeping Merged below In Review', () => {
  const { renderCFD, getCaptured } = loadRenderCFD();
  renderCFD([
    { month: '2026-11-01', merged: 10, review: 3, coding: 0, backlog: 0, opened: 13 },
  ]);
  const { config } = getCaptured();
  assert.equal(config.options.scales.y.stacked, true);
  assert.deepEqual(Array.from(config.data.datasets, d => d.label), ['Merged', 'In Review']);
});
