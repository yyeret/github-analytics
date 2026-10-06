"""Issue collection and backlog-health metrics.

Collection shells out to the `gh` CLI like analyze.py does; computation is a
pure function so it can be tested without GitHub.
"""

import concurrent.futures
import datetime
import json
import subprocess

STALE_DAYS = 90
STALE_ITEM_LIMIT = 25
OPEN_CAP = 1000
CLOSED_CAP = 1000
ISSUE_WINDOW_WEEKS = 52
BASELINE_CAP = 100
PAGE_SIZE = 100
BASELINE_WINDOW = "2021-06-01..2021-12-31"

ISSUE_QUERY = """
query($searchQuery: String!, $first: Int!, $cursor: String) {
  search(query: $searchQuery, type: ISSUE, first: $first, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on Issue {
        number
        title
        createdAt
        closedAt
        updatedAt
        state
        stateReason
        comments(last: 1) { totalCount nodes { createdAt } }
        closedByPullRequestsReferences(first: 1, includeClosedPrs: true) { totalCount }
      }
    }
  }
}
"""

AGE_BUCKETS = [
    ("<7d", 0, 7),
    ("7-30d", 7, 30),
    ("30-90d", 30, 90),
    ("90-180d", 90, 180),
    ("180-365d", 180, 365),
    (">365d", 365, None),
]


def issue_weekly_bins(now, weeks=ISSUE_WINDOW_WEEKS):
    """Weekly bin edges ending at `now`, independent of the PR window."""
    return [now - datetime.timedelta(days=7 * i) for i in range(weeks, -1, -1)]


def _parse(date_str):
    if not date_str:
        return None
    try:
        return datetime.datetime.strptime(date_str.replace("Z", "")[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def _percentile(sorted_vals, p):
    if not sorted_vals:
        return 0
    return sorted_vals[min(int(len(sorted_vals) * p), len(sorted_vals) - 1)]


def _cycle_stats(issues):
    hours = []
    for it in issues:
        created, closed = _parse(it.get("createdAt")), _parse(it.get("closedAt"))
        if created and closed and closed >= created:
            hours.append((closed - created).total_seconds() / 3600.0)
    hours.sort()
    return {"n": len(hours), "median_hours": _percentile(hours, 0.5), "p85_hours": _percentile(hours, 0.85)}


def _last_activity(it):
    nodes = (it.get("comments") or {}).get("nodes") or []
    stamps = [_parse(n.get("createdAt")) for n in nodes if _parse(n.get("createdAt"))]
    return max(stamps) if stamps else _parse(it.get("createdAt"))


def _window_start(issues_raw, weekly_bins):
    start = weekly_bins[0]
    if issues_raw.get("closed_truncated"):
        updated = [_parse(i.get("updatedAt")) for i in issues_raw.get("closed") or []]
        updated = [u for u in updated if u]
        if updated:
            start = max(start, min(updated))
    return start


def compute_issue_metrics(issues_raw, weekly_bins, now, stale_days=STALE_DAYS):
    """Turn raw issue records into the `issues` payload, or None when absent."""
    if issues_raw is None:
        return None

    open_issues = issues_raw.get("open") or []
    closed_issues = issues_raw.get("closed") or []
    window_start = _window_start(issues_raw, weekly_bins)
    sampled = open_issues + closed_issues

    created = {id(i): _parse(i.get("createdAt")) for i in sampled}
    closed_at = {id(i): _parse(i.get("closedAt")) for i in sampled}

    weekly = []
    for idx in range(1, len(weekly_bins)):
        w_start, w_end = weekly_bins[idx - 1], weekly_bins[idx]
        if w_start < window_start:
            continue
        arrived = sum(1 for i in sampled if created[id(i)] and w_start < created[id(i)] <= w_end)
        resolved = sum(1 for i in closed_issues if closed_at[id(i)] and w_start < closed_at[id(i)] <= w_end)
        open_count = sum(
            1 for i in sampled
            if created[id(i)] and created[id(i)] <= w_end and (not closed_at[id(i)] or closed_at[id(i)] > w_end)
        )
        weekly.append({
            "week": w_end.strftime("%Y-%m-%d"),
            "arrived": arrived,
            "resolved": resolved,
            "net": arrived - resolved,
            "open_count": open_count,
        })

    modern_closed = [i for i in closed_issues if closed_at[id(i)] and closed_at[id(i)] >= window_start]

    age_counts = {label: 0 for label, _, _ in AGE_BUCKETS}
    stale_items = []
    for it in open_issues:
        made = created[id(it)]
        if not made:
            continue
        age_days = (now - made).total_seconds() / 86400.0
        for label, low, high in AGE_BUCKETS:
            if age_days >= low and (high is None or age_days < high):
                age_counts[label] += 1
                break
        last = _last_activity(it)
        idle_days = (now - last).total_seconds() / 86400.0 if last else age_days
        if idle_days > stale_days:
            stale_items.append({
                "number": it.get("number"),
                "title": it.get("title", ""),
                "age_days": age_days,
                "days_since_activity": idle_days,
            })
    stale_items.sort(key=lambda s: s["days_since_activity"], reverse=True)

    with_pr = sum(1 for i in modern_closed if ((i.get("closedByPullRequestsReferences") or {}).get("totalCount") or 0) > 0)
    total_closed = len(modern_closed)

    return {
        "window_start": window_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "closed_truncated": bool(issues_raw.get("closed_truncated")),
        "open_truncated": bool(issues_raw.get("open_truncated")),
        "open_now": len(open_issues),
        "cycle_time": {
            "baseline": _cycle_stats(issues_raw.get("pre_ai") or []),
            "modern": _cycle_stats(modern_closed),
        },
        "weekly": weekly,
        "age_buckets": [{"label": label, "count": age_counts[label]} for label, _, _ in AGE_BUCKETS],
        "stale": {"threshold_days": stale_days, "count": len(stale_items), "items": stale_items[:STALE_ITEM_LIMIT]},
        "resolution": {
            "closed_total": total_closed,
            "with_pr": with_pr,
            "without_pr": total_closed - with_pr,
            "pct_with_pr": (with_pr / total_closed * 100.0) if total_closed else 0.0,
        },
    }


def _search_issues(search_query, cap, run):
    """Page through an issue search; returns (nodes, truncated). Raises on failure."""
    nodes, cursor, truncated = [], None, False
    while len(nodes) < cap:
        cmd = [
            "gh", "api", "graphql",
            "-F", f"query={ISSUE_QUERY}",
            "-f", f"searchQuery={search_query}",
            "-F", f"first={PAGE_SIZE}",
        ]
        if cursor:
            cmd.extend(["-f", f"cursor={cursor}"])
        res = run(cmd, capture_output=True, text=True, encoding="utf-8", check=True)
        search = json.loads(res.stdout)["data"]["search"]
        nodes.extend(n for n in search["nodes"] if n)
        info = search["pageInfo"]
        if not info["hasNextPage"]:
            return nodes, False
        cursor = info["endCursor"]
    truncated = True
    return nodes[:cap], truncated


def collect_issues(repo, run=subprocess.run, now=None):
    """Fetch open, recent-closed, and baseline-era issues; None on any failure."""
    now = now or datetime.datetime.now()
    since = (now - datetime.timedelta(weeks=ISSUE_WINDOW_WEEKS)).strftime("%Y-%m-%d")
    searches = {
        "open": (f"repo:{repo} is:issue is:open", OPEN_CAP),
        "closed": (f"repo:{repo} is:issue is:closed closed:>={since} sort:updated-desc", CLOSED_CAP),
        "baseline": (f"repo:{repo} is:issue is:closed closed:{BASELINE_WINDOW}", BASELINE_CAP),
    }
    try:
        # The searches are independent, and each pages sequentially, so run them side by side
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(searches)) as pool:
            futures = {name: pool.submit(_search_issues, q, cap, run) for name, (q, cap) in searches.items()}
            results = {name: f.result() for name, f in futures.items()}
        open_nodes, open_truncated = results["open"]
        closed_nodes, closed_truncated = results["closed"]
        baseline_nodes, _ = results["baseline"]
    except Exception as e:
        print("Error fetching issues:", e)
        return None
    return {
        "open": open_nodes,
        "closed": closed_nodes,
        "pre_ai": baseline_nodes,
        "closed_truncated": closed_truncated,
        "open_truncated": open_truncated,
    }
