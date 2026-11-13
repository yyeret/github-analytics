"""Exercise the hosted issue dashboard from GitHub responses through the API."""
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from freezegun import freeze_time

import github_api
from tests.integration.test_security_boundaries import hosted, hosted_client


def issue(number, state, created, closed=None):
    return {
        'number': number, 'title': 'Let teams export their data', 'body': 'Teams can export their data.',
        'created_at': created, 'updated_at': closed or created, 'closed_at': closed, 'state': state,
    }


@pytest.mark.parametrize('scenario', ['history', 'sparse', 'unavailable', 'empty', 'open-only', 'closed-only', 'pr-unavailable', 'capped'])
@freeze_time("2026-11-12T00:00:00Z")
def test_hosted_analysis_includes_issues_without_any_pull_requests(monkeypatch, scenario):
    opened = issue(7, 'open', '2026-11-01T00:00:00Z')
    closed = issue(8, 'closed', '2026-10-20T00:00:00Z', '2026-11-06T00:00:00Z')
    async def upstream(url, *, headers, **kwargs):
        assert 'Authorization' not in headers  # Public content must remain anonymous.
        path = urlsplit(url)
        if path.path.endswith('/pulls'):
            return (503, {}, {}) if scenario == 'pr-unavailable' else (200, {}, [])
        if path.path.endswith('/issues/events'):
            events = [{'event': 'labeled', 'created_at': '2026-11-02T00:00:00Z',
                       'issue': {'number': 7}, 'label': {'name': 'feature-go'}}] if scenario == 'history' else []
            return 200, {}, events
        query = parse_qs(path.query)['q'][0]
        if 'is:pr' in query:
            return 200, {}, {'items': []}
        if (scenario == 'unavailable' or (scenario == 'open-only' and 'is:closed' in query)
                or (scenario == 'closed-only' and 'is:open' in query)):
            return 503, {}, {}
        if scenario == 'capped' and 'is:open' in query:
            if parse_qs(path.query)['page'] == ['2']:
                return 429, {}, {}
            return 200, {}, {'total_count': 125, 'items': [{**opened, 'number': n} for n in range(101, 201)]}
        items = [] if scenario == 'empty' else ([opened] if 'is:open' in query else [closed])
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
    if scenario == 'pr-unavailable':
        assert result['pr_collection_unavailable'] is True
    if scenario == 'unavailable':
        assert result['issues'] is None
    else:
        metrics = result['issues']
        if scenario == 'capped':
            assert metrics['open_now'] == 100 and metrics['open_truncated']
            assert result['value_classification']['issues']['total'] == 101
        elif scenario == 'open-only':
            assert metrics['open_now'] == 1 and not metrics['closed_available']
            assert metrics['weekly'] == [] and metrics['net_8w'] is None
        elif scenario == 'closed-only':
            assert not metrics['open_available'] and metrics['cycle_time']['modern']['median_hours'] == 408
            assert metrics['weekly'] == [] and metrics['lifecycle_cfd'] is None
        elif scenario == 'empty':
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


@freeze_time("2026-11-12T00:00:00Z")
def test_hosted_analysis_keeps_open_pr_work_alongside_issues(monkeypatch):
    opened = issue(7, 'open', '2026-11-01T00:00:00Z')

    async def upstream(url, **kwargs):
        path = urlsplit(url)
        params = parse_qs(path.query)
        if path.path.endswith('/pulls'):
            return 200, {}, [{**opened, 'number': 9}] if params['state'] == ['open'] else []
        if 'is:issue is:open' in params['q'][0]:
            return 200, {}, {'total_count': 1, 'items': [opened]}
        return 200, {}, {'total_count': 0, 'items': []}

    async def metadata(*args, **kwargs):
        return 200, {}, {'private': False}

    async def allowed(_):
        return {'success': True}

    async def assess(_repo, raw, *_args, **_kwargs):
        assert [pr['number'] for pr in raw['open_prs']] == [9]
        return {'status': 'ok', 'items': [{'number': 9, 'cohort': 'open'}]}

    monkeypatch.setattr(github_api, 'request_json', upstream)
    monkeypatch.setattr(hosted, 'request_json', metadata)
    monkeypatch.setattr(hosted, 'assess_product_value', assess)
    env = SimpleNamespace(AUTH_REQUIRED='false', ANALYZE_LIMIT=SimpleNamespace(limit=allowed))
    result = hosted_client(env).post('/api/analyze', json={'repo_url': 'o/r'}).json()
    assert result['recent_stats']['total'] == 0
    assert [point['num'] for point in result['active_wip_points']] == [9]
    assert result['product_value']['items'][0]['number'] == 9
    assert result['issues']['open_now'] == 1
