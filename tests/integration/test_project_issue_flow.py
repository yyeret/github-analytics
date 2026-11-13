"""Verify ProjectV2 history through the authenticated hosted analysis endpoint."""
import pytest
from freezegun import freeze_time

import github_api
from tests.integration.test_private_issue_api import setup


def event(kind, date, status=None, board_id='board'):
    return {'__typename': kind, 'createdAt': f'2026-11-{date:02}T00:00:00Z',
            'project': {'id': board_id}, 'status': status}


@freeze_time('2026-11-12T12:00:00Z')
@pytest.mark.parametrize('scenario', ['history', 'unavailable', 'transport-error', 'visibility-change', 'ambiguous', 'planning-only',
                                      'custom-unstarted', 'custom-unstarted-with-horizons'])
def test_private_analysis_uses_only_the_linked_boards_observed_history(monkeypatch, scenario):
    client, _, cache, _ = setup(monkeypatch)

    async def collect(repo, token):
        assert token == 'caller-a'
        issues = [{'number': n, 'createdAt': '2026-10-01T00:00:00Z', 'state': 'OPEN'} for n in range(1, 5)]
        return {'repo': repo, 'issues': {'open': issues, 'source': 'rest'}}

    change = 'ProjectV2ItemStatusChangedEvent'
    histories = {
        1: [event('AddedToProjectV2Event', 4), event(change, 5, 'Next'), event(change, 6, 'Next+1'),
            event(change, 7, 'Build'), event(change, 8, 'Verify', board_id='other'),
            event('RemovedFromProjectV2Event', 9), event('AddedToProjectV2Event', 10),
            event(change, 11, 'Verify')],
        2: [event(change, 5, 'Backlog'), event(change, 8, 'Build'), event(change, 10)],
        3: [event(change, 5, 'Backlog'), event('AddedToProjectV2Event', 5),
            event(change, 8, 'Build', board_id='other')],
        4: [event(change, 5, 'Verify')],
    }
    if scenario == 'planning-only':
        histories = {n: [e for e in events if e['status'] not in ('Verify', 'Build')]
                     for n, events in histories.items()}

    custom_unstarted = scenario.startswith('custom-unstarted')
    if custom_unstarted:
        histories = {1: [event(change, 5, 'Unstarted')], 2: [], 3: [], 4: []}

    async def upstream(url, *, headers, body, **kwargs):
        assert url == 'https://api.github.com/graphql'
        assert headers['Authorization'] == 'Bearer caller-a'
        assert body['variables'] == {'owner': 'o', 'name': 'r'}
        if scenario == 'transport-error':
            raise RuntimeError('Worker fetch failed')
        repo = {'id': 'repo', 'nameWithOwner': 'o/r', 'isPrivate': True}
        if 'projectsV2' in body['query']:
            if scenario == 'unavailable':
                return 200, {}, {'errors': [{'message': 'GitHub project scope required'}]}
            statuses = ['Backlog', 'Next+1', 'Next', 'Build', 'Verify']
            if scenario == 'custom-unstarted':
                statuses = ['Unstarted', 'Doing']
            elif custom_unstarted:
                statuses.append('Unstarted')
            options = [{'name': status} for status in statuses]
            board = {'id': 'board', 'title': 'Development', 'closed': False, 'field': {'options': options}}
            repo['projectsV2'] = {'totalCount': 2 if scenario == 'ambiguous' else 1, 'nodes': [board]}
        else:
            if scenario == 'visibility-change':
                repo['isPrivate'] = False
            assert 'PROJECT_V2_ITEM_STATUS_CHANGED_EVENT' in body['query']
            assert 'ADDED_TO_PROJECT_V2_EVENT' not in body['query']
            for number, events in histories.items():
                assert f'issue(number: {number})' in body['query']
                repo[f'i{number}'] = {'number': number, 'timelineItems': {
                    'nodes': [e for e in events if e['__typename'] != 'AddedToProjectV2Event'], 'pageInfo': {'hasPreviousPage': number == 4}}}
        return 200, {}, {'data': {'repository': repo}}

    monkeypatch.setattr(github_api, 'request_json', upstream)
    from tests.integration.test_security_boundaries import hosted
    monkeypatch.setattr(hosted, 'collect_public_data', collect)

    response = client.post('/api/analyze', headers={'Authorization': 'Bearer caller-a'},
                           json={'repo_url': 'o/r', 'allow_private': True})
    assert response.status_code == 200, response.text
    assert all(':private:1:' in key for key in cache.stored)
    issues = response.json()['issues']
    assert issues['open_now'] == 4 and len(issues['weekly']) == 52
    if custom_unstarted:
        cfd = issues['lifecycle_cfd']
        assert cfd is not None
        assert cfd['stages'] == ['Unstarted']
        assert not cfd['coverage']['planning_horizons_grouped']
        assert cfd['points'][-1]['counts'] == {'Unstarted': 1}
        return
    if scenario != 'history':
        assert issues['lifecycle_cfd'] is None
        expected = {'planning-only': 'insufficient', 'transport-error': 'unavailable', 'visibility-change': 'unavailable'}
        assert issues['project_history_status'] == expected.get(scenario, scenario)
        return

    cfd = issues['lifecycle_cfd']
    assert cfd['source'] == 'github_project'
    assert cfd['profile'] == 'Development'
    assert cfd['stages'] == ['Unstarted', 'Build', 'Verify']
    assert cfd['coverage']['issues'] == 4
    assert cfd['coverage']['with_workflow_signal'] == 3
    assert cfd['coverage']['timeline_truncated'] == 1
    counts = {row['date']: row['counts'] for row in cfd['points']}
    assert list(counts)[0] == '2026-11-05'
    assert counts['2026-11-06'] == {'Unstarted': 3, 'Build': 0, 'Verify': 0}
    assert counts['2026-11-08'] == {'Unstarted': 1, 'Build': 2, 'Verify': 0}
    assert counts['2026-11-10'] == {'Unstarted': 1, 'Build': 0, 'Verify': 0}
    assert counts['2026-11-12'] == {'Unstarted': 1, 'Build': 0, 'Verify': 1}
