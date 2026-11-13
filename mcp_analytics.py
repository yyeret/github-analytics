"""Collect the dashboard's PR and issue inputs through GitHub GraphQL."""
import asyncio
import datetime

from github_api import request_json
from issues import BASELINE_CAP, BASELINE_WINDOW, CLOSED_CAP, ISSUE_QUERY, ISSUE_WINDOW_WEEKS, OPEN_CAP

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


async def collect_dashboard_data(repo, token, private, repository_id):
    headers = {'Authorization': f'Bearer {token}', 'User-Agent': 'github-flow-analytics'}
    now = datetime.datetime.now(datetime.timezone.utc)
    since = (now - datetime.timedelta(weeks=ISSUE_WINDOW_WEEKS)).date().isoformat()

    def visible(node):
        metadata = node.get('repository') if isinstance(node, dict) else None
        return (isinstance(metadata, dict) and metadata.get('isPrivate') is private
                and str(metadata.get('fullDatabaseId')) == str(repository_id)
                and str(metadata.get('nameWithOwner', '')).lower() == repo.lower())

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
                for key in ('closingIssuesReferences', 'closedByPullRequestsReferences'):
                    linked = node.get(key)
                    if linked and 'nodes' in linked:
                        linked['nodes'] = [{k: v for k, v in item.items() if k != 'repository'}
                                           for item in linked['nodes'] if visible(item)]
                        if key == 'closedByPullRequestsReferences':
                            linked['totalCount'] = len(linked['nodes'])
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
