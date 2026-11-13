"""Exercise the hosted issue dashboard from GitHub responses through the API."""
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

import github_api
from tests.integration.test_security_boundaries import hosted, hosted_client


def issue(number, state, created, closed=None):
    return {
        'number': number, 'title': 'Let teams export their data', 'body': 'Teams can export their data.',
        'created_at': created, 'updated_at': closed or created, 'closed_at': closed, 'state': state,
    }


@pytest.mark.parametrize('scenario', ['history', 'sparse', 'unavailable', 'empty'])
def test_hosted_analysis_includes_issues_without_any_pull_requests(monkeypatch, scenario):
    opened = issue(7, 'open', '2026-11-01T00:00:00Z')
    closed = issue(8, 'closed', '2026-10-20T00:00:00Z', '2026-11-06T00:00:00Z')
    async def upstream(url, *, headers, **kwargs):
        assert 'Authorization' not in headers  # Public content must remain anonymous.
        path = urlsplit(url)
        if path.path.endswith('/pulls'):
            return 200, {}, []
        if path.path.endswith('/issues/events'):
            events = [{'event': 'labeled', 'created_at': '2026-11-02T00:00:00Z',
                       'issue': {'number': 7}, 'label': {'name': 'feature-go'}}] if scenario == 'history' else []
            return 200, {}, events
        query = parse_qs(path.query)['q'][0]
        if 'is:pr' in query:
            return 200, {}, {'items': []}
        if scenario == 'unavailable':
            return 503, {}, {}
        items = [] if scenario == 'empty' or '..' in query else ([opened] if 'is:open' in query else [closed])
        return 200, {}, {'total_count': len(items), 'incomplete_results': False, 'items': items}

    async def metadata(*args, **kwargs):
        return 200, {}, {'private': False}

    async def allowed(_):
        return {'success': True}

    monkeypatch.setattr(github_api, 'request_json', upstream)
    monkeypatch.setattr(hosted, 'request_json', metadata)
    monkeypatch.setattr(hosted, 'assess_product_value', lambda *_a, **_kw: pytest.fail('No PRs to score'))
    env = SimpleNamespace(AUTH_REQUIRED='false', GITHUB_TOKEN='service-token', ANALYZE_LIMIT=SimpleNamespace(limit=allowed))
    response = hosted_client(env).post('/api/analyze', json={'repo_url': 'github/spec-kit'})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['recent_stats']['total'] == 0
    if scenario == 'unavailable':
        assert result['issues'] is None
    else:
        metrics = result['issues']
        if scenario == 'empty':
            assert metrics['sample_size'] == 0
        else:
            assert metrics['open_now'] == 1
            assert metrics['weekly'][-1]['open_count'] == 1
            assert metrics['cycle_time']['modern']['median_hours'] == 408
            assert result['value_classification']['issues']['total'] == 2
            assert metrics['resolution'] is None  # REST issue search does not tell us PR linkage.
            if scenario == 'history':
                assert metrics['lifecycle_cfd']['points'][-1]['counts']['Ready'] == 1
            else:
                assert metrics['lifecycle_cfd'] is None
