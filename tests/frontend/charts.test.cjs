'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.resolve(__dirname, '../../index.html'), 'utf8');

function extractRenderer(startMarker, endMarker) {
  const start = html.indexOf(startMarker);
  const end = html.indexOf(endMarker, start);
  assert.ok(start !== -1 && end > start, `Missing section ${startMarker}`);
  return html.slice(start, end);
}

test('renderWIP includes Draft only when drafts exist and orders Opened -> In Review -> Approved', () => {
  const snippet = extractRenderer('  // ── Active WIP Age', '  // ── Correlation Scatter');
  let captured = null;
  const ctx = { mkChart: (id, config) => { captured = { id, config }; } };
  vm.runInNewContext(`${snippet}\nthis.renderWIP = renderWIP;`, ctx);

  ctx.renderWIP([
    { x: 'Opened', y: 1.5, num: 1, title: 'Open PR', classification: 'Human' },
    { x: 'In Review', y: 2.5, num: 2, title: 'Reviewed PR', classification: 'Human' },
    { x: 'Approved', y: 3.5, num: 3, title: 'Approved PR', classification: 'Human' },
  ]);
  assert.deepEqual(Array.from(captured.config.options.scales.x.labels), ['Opened', 'In Review', 'Approved']);

  ctx.renderWIP([
    { x: 'Draft', y: 0.5, num: 4, title: 'Draft PR', classification: 'Human' },
    { x: 'Opened', y: 1.5, num: 1, title: 'Open PR', classification: 'Human' },
  ]);
  assert.deepEqual(Array.from(captured.config.options.scales.x.labels), ['Draft', 'Opened', 'In Review', 'Approved']);
});

test('renderCorrelation hides chart and shows note when all PR sizes are 0', () => {
  const snippet = extractRenderer('  // ── Correlation Scatter', '  // ── Comet Chart');
  let drawn = false;
  const wrap = { hidden: false };
  const note = { hidden: true, textContent: '' };
  const elements = {
    'chart-correlation': { parentElement: wrap },
    'correlation-note': note,
  };
  const ctx = {
    document: { getElementById: id => elements[id] || null },
    mkChart: () => { drawn = true; },
  };
  vm.runInNewContext(`${snippet}\nthis.renderCorrelation = renderCorrelation;`, ctx);

  ctx.renderCorrelation([{ x: 0, y: 12, num: 1, title: 'PR', classification: 'Human' }], 0);
  assert.equal(drawn, false);
  assert.equal(wrap.hidden, true);
  assert.equal(note.hidden, false);
  assert.match(note.textContent, /Sign in with GitHub/i);
});

test('renderComet uses month+day ticks when plotted span is <= 90 days', () => {
  const snippet = extractRenderer('  // ── Comet Chart', '  // Shared dashboard renderer end');
  let captured = null;
  const ctx = { mkChart: (id, config) => { captured = { id, config }; } };
  vm.runInNewContext(`${snippet}\nthis.renderComet = renderComet;`, ctx);

  const t1 = Date.UTC(2026, 10, 2);
  const t2 = Date.UTC(2026, 10, 18);
  ctx.renderComet([
    { start: t1, end: t2, lane: 0, key: 1, summary: 'PR 1', size: 10, cycle_time: 24, classification: 'Human' },
  ]);
  const tick = captured.config.options.scales.x.ticks.callback(Date.UTC(2026, 10, 10));
  assert.match(tick, /Nov \d+/);
});

test('renderIssues replaces weekly arrivals with CFD and age distribution with Work Item Aging when present', () => {
  const snippet = extractRenderer('  // ── Issues', '  // ── Throughput Chart');
  const drawn = {};
  const els = {
    'issues-section': { style: {} },
    'issues-note': { textContent: '' },
    'issue-details': { hidden: true },
    'linear-connect-banner': { hidden: true, textContent: '' },
    'issue-lifecycle-card': { hidden: true },
    'issue-cfd-note': { textContent: '' },
    'issue-flow-card': { hidden: false },
    'issue-age-card': { hidden: true },
    'issue-age-title': { textContent: '' },
    'issue-stale-card': { hidden: true },
    'issue-stale-title': { textContent: '' },
    'issue-stale-table': { innerHTML: '' },
  };
  const ctx = {
    document: { getElementById: id => els[id] || null },
    mkChart: (id, config) => { drawn[id] = config; },
    fmt_days: h => `${h}h`,
    window: { parent: null },
  };
  ctx.window.parent = ctx.window;
  vm.runInNewContext(`${snippet}\nthis.renderIssues = renderIssues;`, ctx);

  ctx.renderIssues({
    source: 'linear',
    linear_team: { key: 'MAG' },
    sample_size: 5,
    open_available: true,
    closed_available: true,
    open_now: 2,
    cycle_time: { modern: { n: 3, median_hours: 48, p85_hours: 96 } },
    weekly: [{ week: '2026-11-01', arrived: 2, resolved: 1, open_count: 2 }, { week: '2026-11-08', arrived: 1, resolved: 1, open_count: 2 }],
    age_buckets: [{ label: '<7d', count: 2 }],
    stale: { threshold_days: 14, items: [] },
    lifecycle_cfd: {
      source: 'linear',
      profile: 'MAG',
      stages: ['Todo', 'In Progress', 'Done'],
      points: [{ date: '2026-11-01', counts: { Todo: 1, 'In Progress': 1, Done: 1 } }, { date: '2026-11-08', counts: { Todo: 1, 'In Progress': 1, Done: 3 } }],
      coverage: { issues: 5, with_workflow_signal: 5 },
    },
    work_item_aging: {
      stages: ['Specify', 'Spec Review', 'Build'],
      items: [{ identifier: 'MAG-1', number: 1, title: 'Open task', stage: 'Spec Review', age_days: 9.5 }],
      stage_percentiles: [
        { stage: 'Specify', n: 4, p50_days: 3.0, p85_days: 6.0 },
        { stage: 'Spec Review', n: 3, p50_days: 5.0, p85_days: 11.0 },
        { stage: 'Build', n: 0, p50_days: null, p85_days: null },
      ],
      p50_days: 5.0,
      p85_days: 11.0,
    },
  });

  assert.equal(els['issue-lifecycle-card'].hidden, false);
  assert.equal(els['issue-flow-card'].hidden, true);
  assert.equal(drawn['chart-issue-flow'], undefined);
  assert.equal(els['issue-age-title'].textContent, 'Work Item Aging by Status');
  assert.equal(drawn['chart-issue-age'].type, 'scatter');
  assert.deepEqual(Array.from(drawn['chart-issue-age'].options.scales.x.labels), ['Specify', 'Spec Review', 'Build']);
  assert.deepEqual(Array.from(drawn['chart-issue-age'].data.labels), ['Specify', 'Spec Review', 'Build']);
  const ann = drawn['chart-issue-age'].options.plugins.annotation.annotations;
  assert.equal(ann.band_green_1.yMin, 0);
  assert.equal(ann.band_green_1.yMax, 5.0);
  assert.equal(ann.band_yellow_1.yMin, 5.0);
  assert.equal(ann.band_yellow_1.yMax, 11.0);
  assert.equal(ann.band_red_1.yMin, 11.0);
  assert.ok(ann.band_red_1.yMax > 11.0);
  assert.equal(ann.p50_line_1.yMin, 5.0);
  assert.equal(ann.p85_line_1.yMin, 11.0);
  assert.equal(ann.band_green_2, undefined);
});
