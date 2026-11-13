"""Read status history for the single GitHub Project linked to a private repository."""

import asyncio
import datetime

import github_api

BATCH_SIZE = 50
ISSUE_LIMIT = 300
TIMELINE_LIMIT = 50

_REPO_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    id nameWithOwner isPrivate
    %s
  }
}
"""

_PROJECT_FIELDS = """
projectsV2(first: 2) {
  totalCount
  nodes {
    id title closed
    field(name: "Status") {
      ... on ProjectV2SingleSelectField { options { name } }
    }
  }
}
"""

_ISSUE_FIELDS = """
i{number}: issue(number: {number}) {{
  number
  timelineItems(last: {limit}, itemTypes: [
    PROJECT_V2_ITEM_STATUS_CHANGED_EVENT, REMOVED_FROM_PROJECT_V2_EVENT
  ]) {{
    pageInfo {{ hasPreviousPage }}
    nodes {{
      ... on ProjectV2ItemStatusChangedEvent {{ createdAt status project {{ id }} }}
      ... on RemovedFromProjectV2Event {{ createdAt project {{ id }} }}
    }}
  }}
}}
"""


async def _collect_history(repo, token, issue_numbers):
    owner, name = repo.split('/')
    headers = {'Authorization': f'Bearer {token}', 'User-Agent': 'github-flow-analytics'}

    async def repository(fields):
        try:
            status, _, payload = await github_api.request_json(
                'https://api.github.com/graphql', method='POST', headers=headers,
                body={'query': _REPO_QUERY % fields, 'variables': {'owner': owner, 'name': name}}, timeout=10)
        except Exception as exc:
            # Workers fetch and the local HTTP client raise different transport exceptions.
            raise ValueError('GitHub Project request failed') from exc
        if status != 200 or payload.get('errors'):
            raise ValueError('GitHub Project history unavailable')
        result = payload['data']['repository']
        if result['isPrivate'] is not True or result['nameWithOwner'].lower() != repo.lower() or not result['id']:
            raise ValueError('Repository access changed')
        return result

    metadata = await repository(_PROJECT_FIELDS)
    projects = metadata['projectsV2']
    if projects['totalCount'] == 0:
        return {'status': 'none'}
    if projects['totalCount'] != 1:
        return {'status': 'ambiguous'}
    project, = projects['nodes']
    options = (project['field'] or {}).get('options')
    if project['closed'] or not options:
        return {'status': 'none'}

    timelines = {}
    for start in range(0, min(len(issue_numbers), ISSUE_LIMIT), BATCH_SIZE):
        batch = issue_numbers[start:start + BATCH_SIZE]
        result = await repository('\n'.join(_ISSUE_FIELDS.format(number=n, limit=TIMELINE_LIMIT) for n in batch))
        if result['id'] != metadata['id']:
            raise ValueError('Repository access changed')
        for number in batch:
            issue = result[f'i{number}']
            if issue is None:
                continue
            timeline = issue['timelineItems']
            if (issue['number'] != number or type(timeline['pageInfo']['hasPreviousPage']) is not bool
                    or not isinstance(timeline['nodes'], list)):
                raise ValueError('GitHub returned an invalid Project timeline')
            timelines[number] = timeline
    return {'status': 'available', 'id': project['id'], 'title': project['title'],
            'captured_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'options': [option['name'] for option in options], 'timelines': timelines}


async def collect_project_history(repo, token, issues):
    """Keep optional Project failures independent of basic repository issue metrics."""
    numbers = list(dict.fromkeys(issue['number'] for issue in issues if type(issue.get('number')) is int))
    try:
        return await asyncio.wait_for(_collect_history(repo, token, numbers), timeout=15)
    except (AttributeError, KeyError, TypeError, ValueError, TimeoutError):
        return {'status': 'unavailable'}
