from fastapi.testclient import TestClient

import analyze
import app
from tests.factories import small_raw_data


def bundle():
    data = small_raw_data('acme/widgets')
    data['recent_prs'][0].update(title='Add CSV export', body='Users can download their history.')
    data['issues'] = {'open': [{'number': 90, 'title': 'Investigate timeout', 'body': '',
                              'createdAt': '2024-01-01T00:00:00Z'}], 'closed': [], 'pre_ai': []}
    return data


def test_api_exposes_content_classification_separately_from_ai_mix(monkeypatch):
    monkeypatch.setattr(app, '_run_analysis', lambda repo, force, *_: analyze.compute_flow_metrics(bundle()))
    with TestClient(app.app, base_url="http://localhost") as client:
        response = client.post('/api/analyze', json={'repo_url': 'acme/widgets'})
    assert response.status_code == 200
    result = response.json()
    value = result['value_classification']
    assert value['prs']['counts']['Outcome'] >= 1
    assert value['issues']['counts']['Activity'] == 1
    assert 'classification' in result['recent_prs_summary'][0]
    assert result['recent_prs_summary'][0]['value_classification']['level'] == 'Outcome'
    prompt = app._build_system_prompt(result)
    assert 'Content framing' in prompt
    assert 'achieved' in prompt
    app._metrics_cache.clear()


def test_static_report_includes_framing_and_escapes_content():
    data = bundle()
    data['recent_prs'][0]['title'] = '<script>alert(1)</script> Build dashboard'
    analyze.analyze_and_build_report(data)
    from pathlib import Path
    html = Path(analyze.DASHBOARD_FILE).read_text()
    assert 'Content framing' in html
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in html
    assert '<script>alert(1)</script>' not in html


def test_pr_collectors_request_and_preserve_body(monkeypatch):
    import subprocess
    import re
    from tests.factories import FakeGh, graphql_response, make_pr
    pr = make_pr(1)
    pr['body'] = 'Users can manage their preferences.'
    fake = FakeGh([graphql_response([pr]), graphql_response([pr]), graphql_response([pr])])
    monkeypatch.setattr(subprocess, 'run', fake)
    monkeypatch.setattr(analyze, 'collect_issues', lambda repo: None)
    data = analyze.collect_data('o/r')
    for key in ['pre_ai_prs', 'recent_prs', 'open_prs']:
        assert data[key][0]['body'] == pr['body']
    for cmd in fake.calls:
        query = next(arg for arg in cmd if arg.startswith('query='))
        assert re.search(r'\.\.\. on PullRequest\s*\{\s*body\b', query)


def test_issue_collector_requests_body_and_excludes_baseline_from_framing():
    import json
    from types import SimpleNamespace
    from issues import collect_issues
    def fake(cmd, **kwargs):
        query = next(arg for arg in cmd if arg.startswith('query='))
        assert 'body' in query.split('... on Issue {', 1)[1].split('comments', 1)[0]
        return SimpleNamespace(stdout=json.dumps({'data': {'search': {
            'issueCount': 1, 'pageInfo': {'hasNextPage': False, 'endCursor': None},
            'nodes': [{'number': 5, 'title': 'Build feature', 'body': 'Users can export data.'}]
        }}}))
    raw = collect_issues('o/r', run=fake)
    assert raw['open'][0]['body'] == 'Users can export data.'
    from value_classification import compute_value_classification
    result = compute_value_classification({'issues': raw})
    assert result['issues']['counts']['Outcome'] == 1
    assert result['issues']['total'] == 1
    assert compute_value_classification({'issues': {'pre_ai': raw['pre_ai']}})['issues']['total'] == 0


def test_hosted_rest_collection_preserves_content_for_classification(monkeypatch):
    import asyncio
    import github_api
    async def fake(url, **kwargs):
        if '/search/issues?' in url:
            payload = {'items': []}
        elif 'state=open' in url:
            payload = []
        elif 'page=1' in url:
            payload = [{'number': 91, 'title': 'Add exports', 'body': 'Users can download their data.',
                        'created_at': '2026-10-01T00:00:00Z', 'merged_at': '2026-10-02T00:00:00Z'}]
        else:
            payload = []
        return 200, {}, payload
    monkeypatch.setattr(github_api, 'request_json', fake)
    raw = asyncio.run(analyze.collect_public_data('acme/widgets'))
    assert raw['recent_prs'][0]['body'] == 'Users can download their data.'
    assert analyze.compute_flow_metrics(raw)['value_classification']['prs']['counts']['Outcome'] == 1
