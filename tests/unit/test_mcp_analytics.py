import asyncio

import pytest

import mcp_analytics


def test_failed_dashboard_search_cancels_and_awaits_siblings(monkeypatch):
    async def exercise():
        started, cleaned_up = set(), set()
        all_started = asyncio.Event()
        failing_query = 'repo:o/r is:pr is:merged sort:updated-desc'

        async def request(url, **kwargs):
            query = kwargs['body']['variables']['searchQuery']
            started.add(query)
            if len(started) == 6:
                all_started.set()
            if query == failing_query:
                await all_started.wait()
                return 200, {}, {'data': {'search': {'nodes': []}}}
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned_up.add(query)

        monkeypatch.setattr(mcp_analytics, 'request_json', request)
        async with asyncio.timeout(1):
            with pytest.raises(ValueError, match='GitHub search failed or returned an invalid response'):
                await mcp_analytics.collect_dashboard_data('o/r', 'caller', False, 1)
        assert cleaned_up == started - {failing_query}

    asyncio.run(exercise())


def test_authenticated_public_dashboard_filters_out_private_linked_pull_requests(monkeypatch):
    async def request(url, **kwargs):
        terms = kwargs['body']['variables']['searchQuery']
        repository = {'nameWithOwner': 'o/r', 'isPrivate': False, 'fullDatabaseId': '1'}
        row = {'number': 7, 'repository': repository, 'closedByPullRequestsReferences': {
            'totalCount': 2, 'nodes': [
                {'number': 99, 'repository': {**repository, 'isPrivate': True}},
                {'number': 12, 'repository': repository, 'createdAt': '2026-11-01T00:00:00Z'},
            ]}}
        return 200, {}, {'data': {'search': {'nodes': [row] if 'is:issue is:open' in terms else [],
                                            'pageInfo': {'hasNextPage': False}}}}

    monkeypatch.setattr(mcp_analytics, 'request_json', request)
    raw = asyncio.run(mcp_analytics.collect_dashboard_data('o/r', 'caller', False, 1))
    from issues import build_github_issue_lifecycle

    issue = {**raw['issues']['open'][0], 'createdAt': '2026-10-30T00:00:00Z'}
    assert issue['closedByPullRequestsReferences'] == {
        'totalCount': 1, 'nodes': [{'number': 12, 'createdAt': '2026-11-01T00:00:00Z'}],
    }
    transitions, _ = build_github_issue_lifecycle(issue, 'o/r')
    assert [(stamp.date().isoformat(), stage) for stamp, stage in transitions] == [
        ('2026-10-30', 'Unobserved'), ('2026-11-01', 'In Progress'),
    ]
