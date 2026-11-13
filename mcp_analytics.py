"""Collect the dashboard's PR and issue inputs through GitHub GraphQL."""
import asyncio
import datetime

from github_api import request_json
from issue_candidates import issue_record, issue_selection, open_issue_query
from repo_research import README_CHARS
from issues import BASELINE_CAP, BASELINE_WINDOW, CLOSED_CAP, ISSUE_QUERY, ISSUE_WINDOW_WEEKS, OPEN_CAP, filter_closing_prs

# Search results carry repository visibility so a public-to-private transition cannot expose private contents.
PR_QUERY = """
query($searchQuery: String!, $first: Int!, $cursor: String) {
  search(query: $searchQuery, type: ISSUE, first: $first, after: $cursor) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        repository { nameWithOwner isPrivate fullDatabaseId }
        body number title createdAt mergedAt closedAt additions deletions isDraft
        author { login __typename }
        closingIssuesReferences(first: 1) {
          nodes { number createdAt title closedAt repository { nameWithOwner isPrivate fullDatabaseId } }
        }
        commits(first: 3) {
          totalCount
          nodes { commit { committedDate authors(first: 2) { nodes { name email user { login } } } } }
        }
        reviews(first: 2, states: [APPROVED, CHANGES_REQUESTED, COMMENTED, DISMISSED]) {
          totalCount nodes { createdAt: submittedAt state author { login } }
        }
        timelineItems(first: 5, itemTypes: [READY_FOR_REVIEW_EVENT]) {
          nodes { ... on ReadyForReviewEvent { createdAt } }
        }
      }
    }
  }
}
"""
HOSTED_ISSUE_QUERY = ISSUE_QUERY.replace('... on Issue {', '... on Issue { repository { nameWithOwner isPrivate fullDatabaseId }')


def matches_repository(metadata, repo, private, repository_id):
    return (isinstance(metadata, dict) and metadata.get('isPrivate') is private
            and str(metadata.get('fullDatabaseId')) == str(repository_id)
            and str(metadata.get('nameWithOwner', '')).lower() == repo.lower())


async def collect_dashboard_data(repo, token, private, repository_id):
    headers = {'Authorization': f'Bearer {token}', 'User-Agent': 'github-flow-analytics'}
    now = datetime.datetime.now(datetime.timezone.utc)
    since = (now - datetime.timedelta(weeks=ISSUE_WINDOW_WEEKS)).date().isoformat()

    def visible(node):
        metadata = node.get('repository') if isinstance(node, dict) else None
        return matches_repository(metadata, repo, private, repository_id)

    async def search(query, terms, limit):
        items, cursors, cursor = {}, set(), None
        page_size = min(50, limit)
        for _ in range((limit + page_size - 1) // page_size):
            status, _, payload = await request_json('https://api.github.com/graphql', method='POST', headers=headers,
                body={'query': query, 'variables': {'searchQuery': f'repo:{repo} {terms}',
                     'first': page_size, 'cursor': cursor}}, timeout=15)
            if status != 200 or not isinstance(payload, dict) or payload.get('errors'):
                raise ValueError('GitHub search is unavailable.')
            result = (payload.get('data') or {}).get('search')
            if not result or not isinstance(result.get('nodes'), list):
                raise ValueError('GitHub returned an invalid search result.')
            for node in result['nodes']:
                if node is None:
                    continue
                if not visible(node):
                    raise ValueError('Repository access changed during analysis.')
                node.pop('repository')
                linked_issues = node.get('closingIssuesReferences')
                if linked_issues:
                    linked_issues['nodes'] = [{k: v for k, v in item.items() if k != 'repository'}
                                              for item in linked_issues['nodes'] if visible(item)]
                linked_prs = node.get('closedByPullRequestsReferences')
                if linked_prs and 'nodes' in linked_prs:
                    filter_closing_prs(linked_prs, visible)
                number = node['number']
                if type(number) is not int or number < 1:
                    raise ValueError('GitHub returned an invalid item number.')
                items.setdefault(number, node)
            info = result['pageInfo']
            if not info['hasNextPage']:
                return list(items.values()), result.get('issueCount', len(items)) > len(items)
            cursor = info.get('endCursor')
            if not cursor or cursor in cursors or not result['nodes']:
                raise ValueError('GitHub returned an invalid search cursor.')
            cursors.add(cursor)
        return list(items.values()), True

    searches = (
        (PR_QUERY, 'is:pr is:merged sort:updated-desc', 600),
        (PR_QUERY, 'is:pr is:open sort:updated-desc', 100),
        (PR_QUERY, f'is:pr is:merged merged:{BASELINE_WINDOW} sort:created-asc', 30),
        (HOSTED_ISSUE_QUERY, 'is:issue is:open sort:updated-desc', OPEN_CAP),
        (HOSTED_ISSUE_QUERY, f'is:issue is:closed closed:>={since} sort:updated-desc', CLOSED_CAP),
        (HOSTED_ISSUE_QUERY, f'is:issue is:closed closed:{BASELINE_WINDOW}', BASELINE_CAP),
    )
    tasks = [asyncio.create_task(search(query, terms, limit)) for query, terms, limit in searches]
    try:
        recent, opened, baseline, open_issues, closed_issues, baseline_issues = await asyncio.wait_for(asyncio.gather(*tasks), timeout=40)
    except (KeyError, TypeError, AttributeError, TimeoutError) as exc:
        raise ValueError('GitHub search failed or returned an invalid response.') from exc
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return {'repo': repo, 'recent_prs': recent[0], 'open_prs': opened[0], 'pre_ai_prs': baseline[0],
        'captured_at': now.isoformat(), 'issues': {'open': open_issues[0], 'closed': closed_issues[0],
        'pre_ai': baseline_issues[0], 'open_truncated': open_issues[1], 'closed_truncated': closed_issues[1]}}


async def check_private_dashboard_access(repo, token, repository_id):
    owner, name = repo.split('/')
    status, _, payload = await asyncio.wait_for(request_json('https://api.github.com/graphql', method='POST',
        headers={'Authorization': f'Bearer {token}', 'User-Agent': 'github-flow-analytics'},
        body={'query': '''query($owner: String!, $name: String!) {
          repository(owner: $owner, name: $name) {
            fullDatabaseId isPrivate
            pullRequests(first: 1) { totalCount }
            issues(first: 1) { totalCount }
          }
        }''', 'variables': {'owner': owner, 'name': name}}, timeout=10), timeout=10)
    data = (payload.get('data') or {}).get('repository') if status == 200 and isinstance(payload, dict) else None
    if (not isinstance(data, dict) or payload.get('errors') or str(data.get('fullDatabaseId')) != str(repository_id)
            or data.get('isPrivate') is not True or not data.get('pullRequests') or not data.get('issues')):
        raise ValueError('Repository access changed.')


class ValueSourceUnavailable(ValueError):
    """GitHub refused the GraphQL request without returning usable source data."""


async def _value_graphql(token, query, variables):
    status, _, payload = await asyncio.wait_for(request_json('https://api.github.com/graphql', method='POST',
        headers={'Authorization': f'Bearer {token}', 'User-Agent': 'github-flow-analytics'},
        body={'query': query, 'variables': variables}, timeout=10), timeout=10)
    data = payload.get('data') if isinstance(payload, dict) else None
    if status != 200:
        raise ValueSourceUnavailable('GitHub could not load the assessment sources.')
    if not isinstance(data, dict) or payload.get('errors'):
        raise ValueError('GitHub returned unreadable assessment sources.')
    return data


async def _public_value_repository(repo, token, repository_id, operation, fields, **variables):
    owner, name = repo.split('/')
    declarations = '$owner: String!, $name: String!'
    if 'path' in variables:
        declarations += ', $path: String!'
    query = f'''query {operation}({declarations}) {{
      repository(owner: $owner, name: $name) {{ nameWithOwner isPrivate fullDatabaseId {fields} }}
    }}'''
    data = await _value_graphql(token, query, {'owner': owner, 'name': name, **variables})
    repository = data.get('repository')
    if not matches_repository(repository, repo, False, repository_id):
        raise ValueError('Repository access changed during assessment.')
    return repository


async def public_value_sources(repo, token, repository_id):
    repository = await _public_value_repository(repo, token, repository_id, 'RepositoryValueSources', """
      description homepageUrl repositoryTopics(first: 20) { nodes { topic { name } } }
      community: object(expression: "HEAD:.github") { ... on Tree { entries { name type } } }
      root: object(expression: "HEAD:") { ... on Tree { entries { name type } } }
      docs: object(expression: "HEAD:docs") { ... on Tree { entries { name type } } }
    """)
    readme = {}
    for directory, prefix in (('community', '.github/'), ('root', ''), ('docs', 'docs/')):
        entries = (repository.get(directory) or {}).get('entries') or []
        path = next((entry['name'] for entry in entries if entry['type'] == 'blob'
                     and entry['name'].lower().partition('.')[0] == 'readme'
                     and entry['name'].count('.') <= 1), None)
        if path:
            try:
                source = await _public_value_repository(repo, token, repository_id, 'RepositoryReadme',
                    'readme: object(expression: $path) { ... on Blob { text isTruncated } }', path=f'HEAD:{prefix}{path}')
                readme = source.get('readme') or {}
            except ValueSourceUnavailable:
                pass  # Retain the verified metadata if the optional README request was refused.
            break
    text = readme.get('text') or ''
    topics = (repository.get('repositoryTopics') or {}).get('nodes') or []
    return {'description': repository.get('description') or '', 'homepage': repository.get('homepageUrl') or '',
            'topics': [node['topic']['name'] for node in topics if node], 'private': False,
            'readme': text[:README_CHARS], 'readme_truncated': len(text) > README_CHARS or bool(readme.get('isTruncated'))}


async def collect_public_value_issues(repo, token, repository_id, *, max_issues, issue_numbers):
    fields = 'number title body url updatedAt state milestone { number title }'
    if issue_numbers:
        selected = ' '.join(f'issue{i}: issue(number: {number}) {{ {fields} }}' for i, number in enumerate(issue_numbers))
        repository = await _public_value_repository(repo, token, repository_id, 'ValueIssues', selected)
        nodes = [repository.get(f'issue{i}') for i in range(len(issue_numbers))]
        if any(not isinstance(node, dict) or node.get('number') != number for node, number in zip(nodes, issue_numbers)):
            raise ValueError('A selected issue could not be retrieved.')
        query, total, incomplete = None, len(nodes), False
    else:
        query = open_issue_query(repo)
        owner, name = repo.split('/')
        data = await _value_graphql(token, f'''query ValueIssueSearch($owner: String!, $name: String!, $query: String!, $first: Int!) {{
          repository(owner: $owner, name: $name) {{ nameWithOwner isPrivate fullDatabaseId }}
          search(query: $query, type: ISSUE, first: $first) {{
            issueCount nodes {{ ... on Issue {{ {fields} repository {{ nameWithOwner isPrivate fullDatabaseId }} }} }}
          }}
        }}''', {'owner': owner, 'name': name, 'query': f'{query} sort:updated-desc', 'first': max_issues})
        if not matches_repository(data.get('repository'), repo, False, repository_id):
            raise ValueError('Repository access changed during issue selection.')
        search = data.get('search') or {}
        nodes, total = search.get('nodes'), search.get('issueCount')
        if not isinstance(nodes, list) or type(total) is not int or total < len(nodes):
            raise ValueError('GitHub returned an invalid issue selection.')
        if any(not isinstance(node, dict) or not matches_repository(node.get('repository'), repo, False, repository_id)
               or node.get('state') != 'OPEN' for node in nodes):
            raise ValueError('Repository access changed during issue selection.')
        incomplete = len(nodes) < min(total, max_issues)
    if any(type(node.get('number')) is not int or node['number'] < 1 or node.get('state') not in ('OPEN', 'CLOSED')
           for node in nodes):
        raise ValueError('GitHub returned an invalid issue.')
    items = [issue_record({**node, 'state': node['state'].lower(), 'html_url': node.get('url'),
                           'updated_at': node.get('updatedAt')}) for node in nodes]
    return issue_selection(items, total, incomplete, query)
