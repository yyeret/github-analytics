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
  assert.equal(drawn['chart-issue-age'].data.datasets[0].data[0].url, null);
});

test('mkChart wires hover cursor, click-to-open new tab, and tooltip hint for safe point URLs', () => {
  const snippet = extractRenderer('  // ── External Link & Chart Helpers', '  // ── Shared Chart Defaults');
  const opened = [];
  let createdConfig = null;
  const ctx = {
    URL,
    charts: {},
    document: { getElementById: () => ({ id: 'c' }) },
    window: { open: (...args) => opened.push(args) },
    Chart: function(_el, cfg) { createdConfig = cfg; this.destroy = () => {}; },
  };
  vm.runInNewContext(`${snippet}\nthis.mkChart = mkChart;`, ctx);

  ctx.mkChart('chart-test', {
    type: 'scatter',
    data: {
      datasets: [{
        data: [
          { x: 1, y: 2, url: 'https://linear.app/org/issue/MAG-1' },
          { x: 2, y: 3, url: 'javascript:alert(1)' },
        ],
      }],
    },
    options: {},
  });

  const fakeCanvas = { style: { cursor: '' } };
  const fakeChart = { canvas: fakeCanvas, data: createdConfig.data };

  createdConfig.options.onHover({ native: { target: fakeCanvas } }, [{ datasetIndex: 0, index: 0 }], fakeChart);
  assert.equal(fakeCanvas.style.cursor, 'pointer');

  createdConfig.options.onHover({ native: { target: fakeCanvas } }, [{ datasetIndex: 0, index: 1 }], fakeChart);
  assert.equal(fakeCanvas.style.cursor, '');

  createdConfig.options.onClick({}, [{ datasetIndex: 0, index: 0 }], fakeChart);
  createdConfig.options.onClick({}, [{ datasetIndex: 0, index: 1 }], fakeChart);
  assert.deepEqual(opened, [['https://linear.app/org/issue/MAG-1', '_blank', 'noopener,noreferrer']]);

  assert.equal(createdConfig.options.plugins.tooltip.callbacks.footer([{ raw: createdConfig.data.datasets[0].data[0] }]), 'Click to open ↗');
  assert.equal(createdConfig.options.plugins.tooltip.callbacks.footer([{ raw: createdConfig.data.datasets[0].data[1] }]), '');
});

test('PR charts and issue tables/drilldowns attach clickable external links', () => {
  const wipSnippet = extractRenderer('  // ── Active WIP Age', '  // ── Correlation Scatter');
  const wipEls = {
    'wip-drilldown': { hidden: true },
    'wip-drilldown-summary': { textContent: '' },
    'wip-stage-filter': { innerHTML: '', value: '' },
    'wip-drilldown-count': { textContent: '' },
    'wip-drilldown-table': { innerHTML: '' },
  };
  let wipCfg = null;
  const wipCtx = {
    URL,
    document: { getElementById: id => wipEls[id] || null },
    mkChart: (_id, cfg) => { wipCfg = cfg; },
  };
  vm.runInNewContext(`${wipSnippet}\nthis.renderWIP = renderWIP; this.renderWIPDrilldown = renderWIPDrilldown;`, wipCtx);
  wipCtx.renderWIP([
    { x: 'In Review', y: 4.2, num: 42, title: 'Fix flow', classification: 'Human' },
    { x: 'Opened', y: 1.0, num: 43, title: 'Add tests', classification: 'AI-Assisted', url: 'javascript:alert(1)' },
  ], null, 'acme/app');
  assert.equal(wipCfg.data.datasets[0].data[0].url, 'https://github.com/acme/app/pull/42');
  assert.equal(wipCfg.data.datasets[1].data[0].url, 'https://github.com/acme/app/pull/43');
  assert.doesNotMatch(wipEls['wip-drilldown-table'].innerHTML, /javascript:/i);
  assert.equal(wipEls['wip-drilldown'].hidden, false);
  assert.match(wipEls['wip-drilldown-table'].innerHTML, /href="https:\/\/github\.com\/acme\/app\/pull\/42" target="_blank" rel="noopener noreferrer">PR #42<\/a>/);
  wipEls['wip-stage-filter'].value = 'Opened';
  wipCtx.renderWIPDrilldown();
  assert.equal(wipEls['wip-drilldown-count'].textContent, 'Showing 1 of 2');
  assert.doesNotMatch(wipEls['wip-drilldown-table'].innerHTML, /PR #42/);

  const issueSnippet = extractRenderer('  // ── Issues', '  // ── Throughput Chart');
  const issueEls = {
    'issues-section': { style: {} },
    'issues-note': { textContent: '' },
    'issue-details': { hidden: true },
    'linear-connect-banner': { hidden: true, textContent: '' },
    'issue-lifecycle-card': { hidden: true },
    'issue-cfd-note': { textContent: '' },
    'issue-flow-card': { hidden: false },
    'issue-age-card': { hidden: true },
    'issue-age-title': { textContent: '' },
    'issue-age-drilldown': { hidden: true },
    'issue-age-drilldown-summary': { textContent: '' },
    'issue-age-stage-filter': { innerHTML: '', value: '' },
    'issue-age-pace-filter': { value: '' },
    'issue-age-drilldown-count': { textContent: '' },
    'issue-age-drilldown-table': { innerHTML: '' },
    'issue-stale-card': { hidden: true },
    'issue-stale-title': { textContent: '' },
    'issue-stale-table': { innerHTML: '' },
  };
  const drawn = {};
  const issueCtx = {
    document: { getElementById: id => issueEls[id] || null },
    mkChart: (id, cfg) => { drawn[id] = cfg; },
    fmt_days: h => `${h}h`,
    window: { parent: null },
  };
  issueCtx.window.parent = issueCtx.window;
  vm.runInNewContext(`${issueSnippet}\nthis.renderIssues = renderIssues; this.renderAgingDrilldown = renderAgingDrilldown;`, issueCtx);
  issueCtx.renderIssues({
    source: 'linear',
    linear_team: { key: 'MAG', org: 'magnitude' },
    sample_size: 2,
    open_available: true,
    closed_available: true,
    open_now: 2,
    cycle_time: { modern: { n: 1, median_hours: 24, p85_hours: 48 } },
    weekly: [],
    age_buckets: [],
    stale: {
      threshold_days: 14,
      items: [{ identifier: 'MAG-9', number: 9, title: 'Old item', days_since_activity: 20, age_days: 30, url: 'https://linear.app/magnitude/issue/MAG-9' }],
    },
    work_item_aging: {
      stages: ['Spec Review'],
      items: [
        { identifier: 'MAG-1', number: 1, title: 'Slow spec', stage: 'Spec Review', age_days: 14, url: 'https://linear.app/magnitude/issue/MAG-1' },
        { identifier: 'MAG-2', number: 2, title: 'Fast spec', stage: 'Spec Review', age_days: 2 },
      ],
      stage_percentiles: [{ stage: 'Spec Review', n: 5, p50_days: 4, p85_days: 10 }],
      p50_days: 4,
      p85_days: 10,
    },
  }, null, 'acme/app');
  assert.equal(drawn['chart-issue-age'].data.datasets[0].data[0].url, 'https://linear.app/magnitude/issue/MAG-1');
  assert.equal(drawn['chart-issue-age'].data.datasets[0].data[1].url, 'https://linear.app/magnitude/issue/MAG-2');
  assert.match(issueEls['issue-stale-table'].innerHTML, /href="https:\/\/linear\.app\/magnitude\/issue\/MAG-9" target="_blank" rel="noopener noreferrer">MAG-9<\/a>/);
  assert.equal(issueEls['issue-age-drilldown'].hidden, false);
  assert.match(issueEls['issue-age-drilldown-summary'].textContent, /1 above P85/);
  issueEls['issue-age-pace-filter'].value = 'red';
  issueCtx.renderAgingDrilldown();
  assert.equal(issueEls['issue-age-drilldown-count'].textContent, 'Showing 1 of 2');
  assert.match(issueEls['issue-age-drilldown-table'].innerHTML, /MAG-1/);
  assert.doesNotMatch(issueEls['issue-age-drilldown-table'].innerHTML, /MAG-2/);

  // Linear items without org or explicit url must NOT fall back to GitHub issue URLs
  issueCtx.renderIssues({
    source: 'linear',
    linear_team: { key: 'MAG' },
    sample_size: 1,
    open_available: true,
    closed_available: true,
    open_now: 1,
    cycle_time: { modern: { n: 0 } },
    weekly: [],
    age_buckets: [],
    stale: { threshold_days: 14, items: [] },
    work_item_aging: {
      stages: ['Build'],
      items: [{ identifier: 'MAG-5', number: 5, title: 'No org', stage: 'Build', age_days: 3 }],
      stage_percentiles: [],
    },
  }, null, 'acme/app');
  assert.equal(drawn['chart-issue-age'].data.datasets[0].data[0].url, null);
});

test('simpleMarkdown and linkifyChatRefs link PRs, issues, Linear IDs without nested anchors or code mangling', () => {
  const helpers = extractRenderer('  // ── External Link & Chart Helpers', '  // ── Destroy + Create Chart helpers');
  const escSnippet = extractRenderer('  function escapeHtml(', '  function renderIssueLifecycle(');
  const mdSnippet = extractRenderer('  function linkifyChatRefs(', '  async function sendChat(');
  const ctx = {
    URL,
    GITHUB_REPO_RE: /^[A-Za-z0-9._-]+\/[A-Za-z0-9._-]+$/,
    currentRepo: 'acme/app',
    currentMetrics: { linear_team: { key: 'MAG', org: 'magnitude' } },
  };
  vm.runInNewContext(`${helpers}\n${escSnippet}\n${mdSnippet}\nthis.simpleMarkdown = simpleMarkdown;`, ctx);

  const out = ctx.simpleMarkdown('Check PR #42, #12, MAG-7, `PR #99`, &amp;#39; and [doc](https://example.com/a**b**c) and [bad](javascript:alert(1)).');
  assert.match(out, /<a href="https:\/\/github\.com\/acme\/app\/pull\/42" target="_blank" rel="noopener noreferrer">PR #42<\/a>/);
  assert.doesNotMatch(out, /<a[^>]*>PR <a/);
  assert.match(out, /<a href="https:\/\/github\.com\/acme\/app\/issues\/12" target="_blank" rel="noopener noreferrer">#12<\/a>/);
  assert.match(out, /<a href="https:\/\/linear\.app\/magnitude\/issue\/MAG-7" target="_blank" rel="noopener noreferrer">MAG-7<\/a>/);
  assert.match(out, /<code>PR #99<\/code>/);
  assert.doesNotMatch(out, /issues\/39/);
  assert.match(out, /<a href="https:\/\/example\.com\/a\*\*b\*\*c" target="_blank" rel="noopener noreferrer">doc<\/a>/);
  assert.doesNotMatch(out, /href="javascript:/i);
});
