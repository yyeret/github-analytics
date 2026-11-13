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


def test_public_readme_uses_github_priority_and_rechecks_the_blob(monkeypatch):
    identity = {'nameWithOwner': 'o/r', 'isPrivate': False, 'fullDatabaseId': '12'}
    readme_identity = identity
    readme_status = 200

    async def request(url, **kwargs):
        body = kwargs['body']
        if 'RepositoryValueSources' in body['query']:
            return 200, {}, {'data': {'repository': {**identity,
                'community': {'entries': [{'name': 'README.png', 'type': 'blob'},
                                          {'name': 'README.archive.md', 'type': 'blob'},
                                          {'name': 'ReadMe.rst', 'type': 'blob'}]},
                'root': {'entries': [{'name': 'README.md', 'type': 'blob'}]}}}}
        assert body['variables']['path'] == 'HEAD:.github/ReadMe.rst'
        return readme_status, {}, {'data': {'repository': {**readme_identity, 'readme': {'text': 'Community strategy'}}}}

    monkeypatch.setattr(mcp_analytics, 'request_json', request)
    assert asyncio.run(mcp_analytics.public_value_sources('o/r', 'caller', 12))['readme'] == 'Community strategy'
    readme_status = 403
    assert asyncio.run(mcp_analytics.public_value_sources('o/r', 'caller', 12))['readme'] == ''
    readme_status = 200
    readme_identity = {**identity, 'isPrivate': True}
    with pytest.raises(ValueError, match='Repository access changed'):
        asyncio.run(mcp_analytics.public_value_sources('o/r', 'caller', 12))


@pytest.mark.parametrize('became_private', [False, True])
def test_empty_public_issue_search_still_checks_repository_visibility(monkeypatch, became_private):
    async def request(url, **kwargs):
        assert '-linked:pr' in kwargs['body']['variables']['query']
        return 200, {}, {'data': {'repository': {'nameWithOwner': 'o/r', 'isPrivate': became_private,
                                                 'fullDatabaseId': '12'},
                                   'search': {'issueCount': 0, 'nodes': []}}}

    monkeypatch.setattr(mcp_analytics, 'request_json', request)
    operation = mcp_analytics.collect_public_value_issues('o/r', 'caller', 12, max_issues=5, issue_numbers=None)
    if became_private:
        with pytest.raises(ValueError, match='Repository access changed'):
            asyncio.run(operation)
    else:
        assert asyncio.run(operation)['items'] == []
