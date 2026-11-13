(() => {
  const byId = id => document.getElementById(id);
  const root = document.querySelector('main');
  const status = byId('app-status');
  const dashboard = byId('dashboard');
  const buttons = [...byId('views').querySelectorAll('button')];
  const panels = new Map([['overview', byId('kpi-grid')], ['issues', byId('issues-section')],
    ['value', byId('value-section')], ['issue-value', byId('issue-value-panel')], ['coach', byId('coach-panel')]]);
  const canvases = ['flow', 'work', 'batch'];
  [...dashboard.querySelectorAll(':scope > .charts-2col')].forEach((row, index) => {
    const heading = row.previousElementSibling;
    heading.after(Object.assign(document.createElement('section'), {id: 'view-' + canvases[index]}));
    const section = heading.nextElementSibling;
    section.append(heading, row);
    panels.set(canvases[index], section);
  });
  byId('issue-stale-table').parentElement.classList.add('scroll-table');

  let view = 'overview';
  let metrics = null;
  let repo = '';
  let allowPrivate = false;
  let history = [];
  let sequence = 0;
  let pending = null;
  let hostReady = false;
  let serverTools = false;
  let lastHeight = 0;
  let lastWidth = 0;
  let disposed = false;

  const send = message => window.parent.postMessage({jsonrpc: '2.0', ...message}, '*');
  const showStatus = message => { status.textContent = message; };
  const setTheme = context => {
    const theme = context?.theme;
    if (theme === 'light' || theme === 'dark') document.documentElement.dataset.theme = theme;
    Chart.defaults.color = theme === 'light' ? '#465672' : '#a2b0ca';
  };
  const resize = () => {
    if (!hostReady) return;
    const height = Math.ceil(root.getBoundingClientRect().height);
    const width = Math.ceil(root.getBoundingClientRect().width);
    if (height === lastHeight && width === lastWidth) return;
    lastHeight = height;
    lastWidth = width;
    send({method: 'ui/notifications/size-changed', params: {height, width}});
  };
  const observer = new ResizeObserver(resize);
  observer.observe(root);

  function showView(next) {
    view = next;
    dashboard.hidden = next === 'issue-value' || next === 'coach' || !metrics;
    for (const [id, panel] of panels) panel.hidden = id !== next;
    for (const button of buttons) {
      button.setAttribute('aria-current', String(button.dataset.view === next));
      button.hidden = button.dataset.view !== 'issue-value' && !metrics;
    }
    // Chart.js measures the canvas after its panel becomes visible.
    requestAnimationFrame(() => {
      for (const chart of Object.values(charts)) {
        if (chart.canvas?.offsetParent) chart.resize();
      }
      resize();
    });
  }

  function clearAnalysis() {
    for (const chart of Object.values(charts)) chart.destroy();
    charts = {};
    for (const element of dashboard.querySelectorAll('.empty-chart')) element.remove();
    for (const canvas of dashboard.querySelectorAll('canvas')) canvas.parentElement.hidden = false;
    byId('coach-thread').replaceChildren();
    history = [];
    metrics = null;
    byId('issue-numbers').value = '';
    byId('issue-value-note').textContent = '';
    byId('issue-value-table').replaceChildren();
    byId('refresh').hidden = true;
    byId('dashboard-basis').hidden = true;
  }

  function markEmptyCharts() {
    for (const [id, message] of Object.entries({
      'chart-cfd': 'No PR flow in the sample.',
      'chart-control': 'No merged PR cycle times in the sample.',
      'chart-throughput': 'No merged PRs in the sample.',
      'chart-wip': 'No open PRs in the sample.',
      'chart-correlation': 'PR size and cycle time are unavailable.',
      'chart-comet': 'No PR timelines in the sample.',
    })) {
      if (charts[id]) continue;
      const wrap = byId(id).parentElement;
      wrap.hidden = true;
      const note = document.createElement('p');
      note.className = 'issue-note empty-chart';
      note.textContent = message;
      wrap.after(note);
    }
  }

  function renderIssueValue(result) {
    const value = result.product_value;
    const table = byId('issue-value-table');
    const note = byId('issue-value-note');
    table.replaceChildren();
    if (value.status !== 'ok') {
      note.textContent = value.reason || 'Issue assessment is unavailable.';
      return;
    }
    const rank = new Map((value.ranking || []).map(row => [row.number, row.rank]));
    const dimensions = Object.entries(value.dimensions || {});
    const head = table.createTHead().insertRow();
    for (const label of ['Rank', 'Issue', ...dimensions.map(([, label]) => label), 'Cost of delay']) {
      const th = document.createElement('th');
      th.textContent = label;
      th.scope = 'col';
      head.append(th);
    }
    const rows = table.createTBody();
    const items = [...(value.items || [])].sort((a, b) => (rank.get(a.number) || Infinity) - (rank.get(b.number) || Infinity));
    for (const item of items) {
      const row = rows.insertRow();
      row.insertCell().textContent = rank.get(item.number) || '—';
      row.insertCell().textContent = '#' + item.number + ' ' + item.title;
      if (item.status !== 'assessed') {
        const cell = row.insertCell();
        cell.colSpan = dimensions.length + 1;
        cell.textContent = item.reason || 'Assessment unavailable.';
        continue;
      }
      for (const [key] of dimensions) {
        const dimension = item.dimensions[key];
        const cell = row.insertCell();
        cell.textContent = dimension.score + ' · ' + dimension.rubric;
        const details = document.createElement('details');
        const summary = document.createElement('summary');
        summary.textContent = 'Model uncertainty';
        details.append(summary, Object.entries(dimension.probabilities)
          .map(([score, p]) => score + ': ' + Math.round(p * 100) + '%').join(' · '));
        cell.append(details);
      }
      row.insertCell().textContent = item.cost_of_delay;
    }
    note.textContent = 'Assessed ' + value.assessed + ' of ' + value.selected + ' selected issues. ' + (value.disclaimer || '');
  }

  function showResult(result) {
    if (disposed) return;
    if (result?.isError) {
      showStatus(result.content?.[0]?.text || 'The dashboard could not be loaded.');
      return;
    }
    const data = result?.structuredContent;
    if (!data?.view) return;
    const meta = result._meta || {};
    byId('private-option').hidden = !meta.privateEnabled;
    byId('private-help').hidden = Boolean(meta.privateEnabled);
    if (!data.repo) {
      clearAnalysis();
      repo = '';
      allowPrivate = false;
      byId('repo-input').value = '';
      byId('allow-private').checked = false;
      byId('views').hidden = true;
      showView(data.view);
      showStatus('');
      return;
    }
    if (!meta.metrics && !meta.issueValue) {
      showStatus('This chat client did not provide the dashboard data.');
      return;
    }
    if (meta.metrics || repo !== data.repo) clearAnalysis();
    repo = data.repo;
    allowPrivate = data.visibility === 'private';
    byId('repo-input').value = repo;
    byId('allow-private').checked = allowPrivate;
    byId('views').hidden = false;
    if (meta.metrics) {
      metrics = meta.metrics;
      renderDashboard(metrics);
      markEmptyCharts();
      const basis = byId('dashboard-basis');
      basis.textContent = (allowPrivate ? 'Private repository. ' : '') + 'GitHub sample · ' +
        (data.captured_at?.slice(0, 10) || '') +
        '. Contributor type and active PR stages are estimates from sampled activity.';
      basis.hidden = false;
      byId('refresh').hidden = false;
    }
    if (meta.issueValue) renderIssueValue(meta.issueValue);
    showView(data.view);
    showStatus('');
  }

  function callTool(name, args) {
    if (!serverTools && !window.openai?.callTool) {
      return Promise.reject(new Error('This chat client cannot run tools from the dashboard.'));
    }
    if (pending) return Promise.reject(new Error('An analysis is already running.'));
    const id = 'github-tool-' + (++sequence);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        pending = null;
        reject(new Error('The request timed out. Try again.'));
      }, 180000);
      const finish = ({error, result}) => {
        if (pending?.id !== id) return;
        clearTimeout(timer);
        pending = null;
        if (error || result?.isError || !result) {
          reject(new Error(result?.content?.[0]?.text || 'The request failed. Try again.'));
        } else resolve(result);
      };
      pending = {id, finish};
      if (serverTools) send({id, method: 'tools/call', params: {name, arguments: args}});
      else window.openai.callTool(name, args).then(result => finish({result}), error => finish({error}));
    });
  }

  async function run(form, label, operation) {
    if (pending || disposed) return;
    const fields = [...form.querySelectorAll('button, input')];
    fields.forEach(field => { field.disabled = true; });
    showStatus(label);
    try { await operation(); }
    catch (error) { showStatus(error.message); }
    finally { fields.forEach(field => { field.disabled = false; }); }
  }

  function action(buttonId, inputId, submit, enter = true) {
    const button = byId(buttonId);
    const input = byId(inputId);
    function execute() {
      if (!button.disabled && input.reportValidity()) submit();
    }
    button.addEventListener('click', execute);
    if (enter) input.addEventListener('keydown', event => {
      if (event.key === 'Enter' && !event.isComposing) execute();
    });
  }

  for (const button of buttons) button.addEventListener('click', () => showView(button.dataset.view));
  function analyze(force) {
    const requestedRepo = byId('repo-input').value.trim();
    if (!requestedRepo) return showStatus('Enter a GitHub repository.');
    run(byId('analyze-form'), 'Analyzing repository…', async () => {
      const result = await callTool('refresh_github_dashboard', {
        repo: requestedRepo,
        view: ['issue-value', 'coach'].includes(view) ? 'overview' : view,
        force_refresh: force,
        allow_private: byId('allow-private').checked,
      });
      showResult(result);
    });
  }
  action('analyze', 'repo-input', () => analyze(false));
  action('refresh', 'repo-input', () => analyze(true), false);
  action('assess', 'issue-numbers', () => {
    run(byId('issue-form'), 'Assessing issue value…', async () => {
      const value = byId('issue-numbers').value.trim();
      const entries = value ? value.split(',').map(part => part.trim()) : [];
      const numbers = entries.map(Number);
      if (entries.some(entry => !/^\d+$/.test(entry)) ||
          numbers.some(number => !Number.isSafeInteger(number) || number < 1) ||
          new Set(numbers).size !== numbers.length || numbers.length > 50) {
        throw new Error('Enter up to 50 distinct positive issue numbers separated by commas.');
      }
      const result = await callTool('assess_dashboard_issues', {repo, allow_private: byId('allow-private').checked,
        ...(numbers.length ? {issue_numbers: numbers, max_issues: Math.max(20, numbers.length)} : {})});
      showResult(result);
    });
  });
  action('ask', 'coach-message', () => {
    run(byId('coach-form'), 'Asking the coach…', async () => {
      const input = byId('coach-message');
      const message = input.value.trim();
      if (!message || !metrics) return showStatus('');
      const snapshot = metrics;
      if (allowPrivate && !byId('allow-private').checked) {
        throw new Error('Enable private analysis to ask about this snapshot.');
      }
      const result = await callTool('ask_flow_coach', {repo, message, allow_private: allowPrivate, history: history.slice(-10),
        ...(snapshot.analysis_id ? {analysis_id: snapshot.analysis_id} : {})});
      if (metrics !== snapshot) return;
      const reply = result.structuredContent?.reply || result.content?.[0]?.text;
      for (const [role, content] of [['user', message], ['assistant', reply]]) {
        const line = document.createElement('p');
        line.textContent = (role === 'user' ? 'You: ' : 'Coach: ') + content;
        byId('coach-thread').append(line);
        history.push({role, content});
      }
      if (history.length > 10) history = history.slice(-10);
      input.value = '';
      showStatus('');
    });
  });

  window.addEventListener('message', event => {
    if (disposed || event.source !== window.parent || event.data?.jsonrpc !== '2.0') return;
    const message = event.data;
    if (message.id === 'github-app-init' && message.result) {
      hostReady = true;
      serverTools = Boolean(message.result.hostCapabilities?.serverTools);
      setTheme(message.result.hostContext);
      send({method: 'ui/notifications/initialized'});
      resize();
    } else if (pending && message.id === pending.id) pending.finish(message);
    else if (hostReady && message.method === 'ui/notifications/tool-result') showResult(message.params);
    else if (hostReady && message.method === 'ui/notifications/host-context-changed') setTheme(message.params);
    else if (hostReady && message.method === 'ui/resource-teardown') {
      disposed = true;
      observer.disconnect();
      if (pending) pending.finish({error: true});
      clearAnalysis();
      send({id: message.id, result: {}});
    }
  });
  // Compatibility with hosts that expose the earlier ChatGPT widget API.
  function readWidget() {
    if (hostReady || !window.openai?.toolOutput) return;
    setTheme({theme: window.openai.theme});
    const metadata = window.openai.toolResponseMetadata;
    const result = metadata?.mcp_tool_result || metadata?.call_tool_result;
    showResult({structuredContent: window.openai.toolOutput, _meta: result?._meta || metadata});
  }
  window.addEventListener('openai:set_globals', readWidget);
  readWidget();
  send({id: 'github-app-init', method: 'ui/initialize', params: {
    protocolVersion: '2026-01-26', appInfo: {name: 'GitHub Flow Analytics', version: '1.0.0'},
    appCapabilities: {availableDisplayModes: ['inline', 'fullscreen']},
  }});
})();
