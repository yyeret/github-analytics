"""Bounded issue discovery or explicit lifecycle-independent selection."""
import asyncio
from urllib.parse import urlencode

from github_api import request_json


async def collect_issue_candidates(repo, token, *, max_issues=20, issue_numbers=None):
    headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'github-flow-analytics/0.1'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    if issue_numbers:
        if len(issue_numbers) > max_issues or len(set(issue_numbers)) != len(issue_numbers) or any(type(n) is not int or n < 1 for n in issue_numbers):
            raise ValueError('Choose distinct positive issue numbers within max_issues.')
        semaphore = asyncio.Semaphore(5)
        async def fetch(number):
            async with semaphore:
                status, _, item = await asyncio.wait_for(request_json(
                    f'https://api.github.com/repos/{repo}/issues/{number}', headers=headers, timeout=15), 15)
            if status != 200 or not isinstance(item, dict) or item.get('number') != number:
                raise ValueError('A selected issue could not be retrieved.')
            if 'pull_request' in item:
                raise ValueError('A selected item is a pull request; use the PR analysis endpoint.')
            if item.get('state') not in ('open', 'closed'):
                raise ValueError('A selected issue has an invalid state.')
            return issue_record(item)
        items = await asyncio.wait_for(asyncio.gather(*(fetch(n) for n in issue_numbers)), 30)
        return {'items': items, 'total_count': len(items), 'truncated': False, 'search_incomplete': False,
                'query': None, 'selection_policy': 'Explicit issues in any lifecycle state, independent of PR linkage; value does not grant execution permission.'}
    query = f'repo:{repo} is:issue is:open -linked:pr'
    found, total, incomplete = {}, 0, False
    for page in range(1, 2):
        url = 'https://api.github.com/search/issues?' + urlencode({
            'q': query, 'sort': 'updated', 'order': 'desc', 'per_page': 100, 'page': page})
        status, _, data = await asyncio.wait_for(request_json(url, headers=headers, timeout=15), 15)
        if status != 200 or not isinstance(data, dict) or not isinstance(data.get('items'), list):
            raise ValueError('GitHub could not return the issue selection.')
        total = data.get('total_count')
        if type(total) is not int or total < 0:
            raise ValueError('GitHub returned an invalid issue count.')
        incomplete = incomplete or data.get('incomplete_results') is not False
        for item in data['items']:
            if not isinstance(item, dict) or type(item.get('number')) is not int or 'pull_request' in item or item.get('state') != 'open':
                raise ValueError('GitHub returned an invalid open issue.')
            found[item['number']] = issue_record(item)
    items = list(found.values())[:max_issues]
    return {'items': items, 'total_count': total, 'truncated': incomplete or len(items) < total,
            'search_incomplete': incomplete, 'query': query,
            'selection_policy': 'Open issues without a linked PR closing reference; this does not rule out mentions or work already in progress.'}


def issue_record(item):
    record = {key: item.get(key) for key in ('number', 'title', 'body', 'html_url', 'labels', 'assignees', 'updated_at', 'state', 'milestone')}
    return {**record, 'url': item.get('html_url'), 'item_type': 'issue'}
