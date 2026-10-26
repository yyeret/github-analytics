import asyncio
from urllib.parse import parse_qs, urlsplit

import pytest


def test_issue_candidates_discover_unlinked_open_issues(monkeypatch):
    import issue_candidates as module
    seen = []
    async def request(url, **kwargs):
        seen.append((parse_qs(urlsplit(url).query), kwargs['headers']))
        return 200, {}, {'total_count': 2, 'incomplete_results': False, 'items': [
            {'number': 2, 'title': 'B', 'body': 'Body', 'state': 'open', 'html_url': 'https://github.com/o/r/issues/2'},
            {'number': 1, 'title': 'A', 'body': '', 'state': 'open'}]}
    monkeypatch.setattr(module, 'request_json', request)
    result = asyncio.run(module.collect_issue_candidates('o/r', 'caller', max_issues=2))
    assert [i['number'] for i in result['items']] == [2, 1]
    assert '-linked:pr' in seen[0][0]['q'][0] and 'is:issue is:open' in seen[0][0]['q'][0]
    assert seen[0][1]['Authorization'] == 'Bearer caller'
    assert result['total_count'] == 2 and not result['truncated']
    assert 'closing reference' in result['selection_policy']


def test_issue_candidates_do_not_silently_drop_unavailable_selection(monkeypatch):
    import issue_candidates as module
    async def request(*_, **__): return 200, {}, {'total_count': 0, 'incomplete_results': False, 'items': []}
    monkeypatch.setattr(module, 'request_json', request)
    with pytest.raises(ValueError, match='selected'):
        asyncio.run(module.collect_issue_candidates('o/r', 'caller', issue_numbers=[3]))


def test_explicit_selection_includes_closed_issues_for_learning(monkeypatch):
    import issue_candidates as module
    async def request(url, **kwargs):
        assert url == 'https://api.github.com/repos/o/r/issues/701'
        return 200, {}, {'number': 701, 'title': 'Learn from live page', 'body': 'Evaluate reader response.', 'state': 'closed'}
    monkeypatch.setattr(module, 'request_json', request)
    result = asyncio.run(module.collect_issue_candidates('o/r', 'caller', issue_numbers=[701]))
    assert result['items'][0]['state'] == 'closed'
    assert result['items'][0]['item_type'] == 'issue'
    assert result['total_count'] == 1 and not result['truncated']


def test_explicit_selection_rejects_prs_in_issue_endpoint(monkeypatch):
    import issue_candidates as module
    async def request(*_, **__): return 200, {}, {'number': 9, 'state': 'closed', 'pull_request': {}}
    monkeypatch.setattr(module, 'request_json', request)
    with pytest.raises(ValueError, match='pull request'):
        asyncio.run(module.collect_issue_candidates('o/r', 'caller', issue_numbers=[9]))
