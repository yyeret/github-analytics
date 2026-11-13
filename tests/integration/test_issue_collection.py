import datetime as dt
import json
import subprocess
from types import SimpleNamespace

from issues import collect_issues, compute_issue_metrics


def node(n):
    return {
        "number": n,
        "title": f"Issue {n}",
        "createdAt": "2026-08-01T00:00:00Z",
        "closedAt": None,
        "updatedAt": "2026-08-02T00:00:00Z",
        "state": "OPEN",
        "stateReason": None,
        "comments": {"totalCount": 0, "nodes": []},
        "closedByPullRequestsReferences": {"totalCount": 0},
    }


def page(nodes, has_next, cursor="c", count=None):
    body = {"data": {"search": {"issueCount": len(nodes) if count is None else count, "pageInfo": {"hasNextPage": has_next, "endCursor": cursor}, "nodes": nodes}}}
    return SimpleNamespace(stdout=json.dumps(body))


def arg(cmd, key):
    for part in cmd:
        if isinstance(part, str) and part.startswith(f"{key}="):
            return part.split("=", 1)[1]
    return None


def test_pages_are_concatenated_in_order_and_cursor_is_passed():
    calls = []

    def fake(cmd, **kwargs):
        q = arg(cmd, "searchQuery")
        calls.append((q, arg(cmd, "cursor")))
        if "is:open" in q:
            return page([node(1), node(2)], True, "next", count=3) if arg(cmd, "cursor") is None else page([node(3)], False, count=3)
        return page([], False)

    out = collect_issues("o/r", run=fake)
    assert [i["number"] for i in out["open"]] == [1, 2, 3]
    assert out["open_truncated"] is False
    assert ("repo:o/r is:issue is:open", "next") in calls


def test_hitting_the_closed_cap_with_more_pages_sets_closed_truncated():
    closed_calls = []

    def fake(cmd, **kwargs):
        q = arg(cmd, "searchQuery")
        if "sort:updated-desc" in q:
            closed_calls.append(q)
            return page([node(i) for i in range(100)], True)
        return page([], False)

    out = collect_issues("o/r", run=fake)
    assert len(closed_calls) == 10
    assert len(out["closed"]) == 1000
    assert out["closed_truncated"] is True


def test_subprocess_failure_returns_none():
    def boom(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd, stderr="rate limited")

    assert collect_issues("o/r", run=boom) is None


def test_non_json_output_returns_none():
    assert collect_issues("o/r", run=lambda cmd, **kw: SimpleNamespace(stdout="not json")) is None


def test_result_feeds_compute_issue_metrics():
    def fake(cmd, **kwargs):
        return page([node(1)], False) if "is:open" in arg(cmd, "searchQuery") else page([], False)

    out = collect_issues("o/r", run=fake)
    assert set(out) == {"open", "closed", "pre_ai", "closed_truncated", "open_truncated"}
    bins = [dt.datetime(2026, 7, 1) + dt.timedelta(days=7 * i) for i in range(10)]
    assert compute_issue_metrics(out, bins, dt.datetime(2026, 10, 1))["open_now"] == 1


def test_closed_query_is_bounded_by_the_issue_window_start_date():
    seen = []

    def fake(cmd, **kwargs):
        seen.append(arg(cmd, "searchQuery"))
        return page([], False)

    collect_issues("o/r", run=fake, now=dt.datetime(2026, 10, 1))
    closed = [q for q in seen if "sort:updated-desc" in q]
    assert closed == ["repo:o/r is:issue is:closed closed:>=2025-10-02 sort:updated-desc"]


def test_issue_weekly_bins_cover_52_weeks_ending_at_now():
    from issues import issue_weekly_bins

    now = dt.datetime(2026, 10, 1)
    bins = issue_weekly_bins(now)
    assert len(bins) == 53
    assert bins[-1] == now
    assert bins[0] == now - dt.timedelta(weeks=52)
    assert all(b2 - b1 == dt.timedelta(days=7) for b1, b2 in zip(bins, bins[1:]))


def test_local_collection_does_not_keep_cross_repo_closing_pr_details():
    row = node(7)
    row['closedByPullRequestsReferences'] = {'totalCount': 3, 'nodes': [
        {'number': 19, 'repository': {'nameWithOwner': 'O/R', 'isPrivate': False}},
        {'number': 22, 'repository': {'nameWithOwner': 'o/private', 'isPrivate': True}},
        {'number': 23, 'repository': {'nameWithOwner': 'o/other', 'isPrivate': False}},
    ]}

    def fake(cmd, **kwargs):
        return page([row], False) if 'is:open' in arg(cmd, 'searchQuery') else page([], False)

    linked = collect_issues('o/r', run=fake)['open'][0]['closedByPullRequestsReferences']
    assert linked == {'totalCount': 1, 'nodes': [{'number': 19}]}


def test_unsampled_closing_prs_leave_linkage_unknown():
    row = node(7)
    row['closedAt'] = '2026-09-02T00:00:00Z'
    row['closedByPullRequestsReferences'] = {'totalCount': 4, 'nodes': [
        {'number': number, 'repository': {'nameWithOwner': 'o/other', 'isPrivate': False}}
        for number in (1, 2, 3)
    ]}

    def fake(cmd, **kwargs):
        return page([row], False) if 'sort:updated-desc' in arg(cmd, 'searchQuery') else page([], False)

    now = dt.datetime(2026, 9, 5)
    collected = collect_issues('o/r', run=fake, now=now)
    result = compute_issue_metrics(collected, [now - dt.timedelta(days=7), now], now)
    assert result['resolution'] == {
        'closed_total': 1, 'with_pr': 0, 'without_pr': 0, 'unknown': 1, 'pct_with_pr': None,
    }
