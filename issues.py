"""Issue collection and backlog-health metrics.

Local collection uses the `gh` CLI; Workers use GitHub REST.
Metric calculations are independent of the transport.
"""

import asyncio
import concurrent.futures
import datetime
import json
import subprocess
from itertools import groupby
from urllib.parse import urlencode

import httpx

import github_api
from flow_metrics_core import count_events_in_windows, count_intervals, elapsed, percentiles

STALE_DAYS = 90
STALE_ITEM_LIMIT = 25
NET_WINDOW_WEEKS = 8
REST_ISSUE_PAGE_CAP = 3
REST_EVENT_PAGE_CAP = 3
OPEN_CAP = 1000
CLOSED_CAP = 1000
ISSUE_WINDOW_WEEKS = 52
BASELINE_CAP = 100
PAGE_SIZE = 100
BASELINE_WINDOW = "2021-06-01..2021-12-31"

FLOW_STAGES = ["Unobserved", "Triage", "Ready", "In Progress", "Review", "Done"]
WORKFLOW_LABEL_PROFILES = {
    "Fission-AI/OpenSpec": {
        "needs-triage": "Triage",
        "needs-info": "Triage",
        "design-review": "Triage",
        "future-roadmap": "Triage",
        "backlog": "Ready",
        "community-fix": "Review",
    },
    "github/spec-kit": {
        "needs-triage": "Triage",
        "needs-info": "Triage",
        "needs-reproduction": "Triage",
        "needs-author-response": "Triage",
        "author-awaiting": "Triage",
        "author-needs-disclosure": "Triage",
        "author-needs-info": "Triage",
        "author-needs-proof": "Triage",
        "author-needs-rebase": "Triage",
        "author-needs-rescope": "Triage",
        "author-needs-tests": "Triage",
        "feature-assess": "Triage",
        "feature-needs-clarification": "Triage",
        "bug-assess": "Triage",
        "feature-go": "Ready",
        "triage-nice-to-have": "Ready",
        "triage-must-have": "Ready",
        "bug-fix": "Ready",
        "validation-failed": "In Progress",
        "needs-verification": "Review",
        "fix-proposed": "Review",
        "bug-test": "Review",
        "validation-passed": "Review",
    },
    "kubernetes/kubernetes": {
        "needs-triage": "Triage",
        "triage/needs-information": "Triage",
        "priority/awaiting-more-evidence": "Triage",
        "needs-sig": "Triage",
        "needs-priority": "Triage",
        "triage/accepted": "Ready",
        "priority/backlog": "Ready",
        "priority/important-longterm": "Ready",
        "priority/important-soon": "Ready",
        "priority/critical-urgent": "Ready",
        "good first issue": "Ready",
        "help wanted": "Ready",
        "lifecycle/active": "In Progress",
        "api-review": "Review",
        "lgtm": "Review",
        "approved": "Review",
    },
}
WORKFLOW_PROFILE_NAMES = {name.lower(): name for name in WORKFLOW_LABEL_PROFILES}

ISSUE_QUERY = """
query($searchQuery: String!, $first: Int!, $cursor: String) {
  search(query: $searchQuery, type: ISSUE, first: $first, after: $cursor) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on Issue {
        body
        number
        title
        createdAt
        closedAt
        updatedAt
        state
        stateReason
        comments(last: 1) { totalCount nodes { createdAt } }
        timelineItems(
          first: 50
          itemTypes: [LABELED_EVENT, UNLABELED_EVENT, CLOSED_EVENT, REOPENED_EVENT]
        ) {
          pageInfo { hasNextPage }
          nodes {
            __typename
            ... on LabeledEvent { createdAt label { name } }
            ... on UnlabeledEvent { createdAt label { name } }
            ... on ClosedEvent { createdAt }
            ... on ReopenedEvent { createdAt }
          }
        }
        closedByPullRequestsReferences(first: 3, includeClosedPrs: true) {
          totalCount
          nodes {
            repository { nameWithOwner isPrivate fullDatabaseId }
            number
            createdAt
            mergedAt
            closedAt
            isDraft
            commits(first: 1) { nodes { commit { committedDate } } }
            reviews(first: 5, states: [APPROVED, CHANGES_REQUESTED, COMMENTED, DISMISSED]) { nodes { createdAt: submittedAt } }
            timelineItems(first: 5, itemTypes: [READY_FOR_REVIEW_EVENT]) {
              nodes { ... on ReadyForReviewEvent { createdAt } }
            }
          }
        }
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
    if not isinstance(date_str, str) or not date_str:
        return None
    try:
        return datetime.datetime.strptime(date_str.replace("Z", "")[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def _cycle_stats(issues):
    hours = []
    for it in issues:
        created, closed = _parse(it.get("createdAt")), _parse(it.get("closedAt"))
        if created and closed and closed >= created:
            hours.append(elapsed(created, closed, 3600.0))
    median, p85 = percentiles(hours, (0.5, 0.85), method="upper-index") if hours else (0, 0)
    return {"n": len(hours), "median_hours": median, "p85_hours": p85}


def _last_activity(it):
    nodes = (it.get("comments") or {}).get("nodes") or []
    stamps = [s for s in (_parse(n.get("createdAt")) for n in nodes) if s]
    return max(stamps) if stamps else _parse(it.get("createdAt"))


def _workflow_profile(repo):
    return WORKFLOW_PROFILE_NAMES.get((repo or "").lower(), "signals-only")


def _workflow_label_stages(repo):
    return WORKFLOW_LABEL_PROFILES.get(_workflow_profile(repo), {})


def _label_stage(active_labels, label_stages):
    mapped = [label_stages[label] for label in active_labels if label in label_stages]
    priority = {"Triage": 1, "Ready": 2, "In Progress": 3, "Review": 4}
    return max(mapped, key=lambda stage: priority[stage]) if mapped else "Unobserved"


def _first_timestamp(values):
    parsed = [value for value in (_parse(raw) for raw in values) if value]
    return min(parsed) if parsed else None


def build_github_issue_lifecycle(issue, repo=None):
    """Normalize GitHub labels, linked-PR activity, and close/reopen events."""
    label_stages = _workflow_label_stages(repo)
    created = _parse(issue.get("createdAt"))
    events = []
    if not created:
        return [], False
    events.append((created, 0, "created", None))

    timeline = issue.get("timelineItems") or {}
    for node in (n for n in timeline.get("nodes") or [] if n):
        event_at = _parse(node.get("createdAt"))
        if not event_at or event_at < created:
            continue
        typename = node.get("__typename")
        if typename in ("LabeledEvent", "UnlabeledEvent"):
            label = ((node.get("label") or {}).get("name") or "").lower()
            if label in label_stages:
                kind = "label_add" if typename == "LabeledEvent" else "label_remove"
                events.append((event_at, 10, kind, label))
        elif typename == "ClosedEvent":
            events.append((event_at, 50, "closed", None))
        elif typename == "ReopenedEvent":
            events.append((event_at, 60, "reopened", None))

    linked_prs = (issue.get("closedByPullRequestsReferences") or {}).get("nodes") or []
    for pr in linked_prs:
        if not pr or not pr.get("number"):
            continue
        pr_number = pr["number"]
        pr_created = _parse(pr.get("createdAt"))
        commit_dates = [
            ((node.get("commit") or {}).get("committedDate"))
            for node in (pr.get("commits") or {}).get("nodes") or []
        ]
        work_started = _first_timestamp(commit_dates) or pr_created
        if work_started and work_started < created:
            work_started = created
        pr_ended = _first_timestamp([pr.get("mergedAt"), pr.get("closedAt")])
        if not work_started or (pr_ended and pr_ended <= work_started):
            continue
        events.append((work_started, 20, "pr_work", pr_number))

        ready_dates = [
            node.get("createdAt")
            for node in (pr.get("timelineItems") or {}).get("nodes") or []
        ]
        review_dates = [
            node.get("createdAt")
            for node in (pr.get("reviews") or {}).get("nodes") or []
        ]
        review_started = _first_timestamp(ready_dates + review_dates)
        if not review_started and pr.get("isDraft") is False:
            review_started = pr_created
        if review_started:
            review_started = max(review_started, work_started)
        if review_started and (not pr_ended or review_started < pr_ended):
            events.append((review_started, 30, "pr_review", pr_number))

        if pr_ended:
            events.append((pr_ended, 40, "pr_end", pr_number))

    closed_at = _parse(issue.get("closedAt"))
    if closed_at and closed_at >= created:
        events.append((closed_at, 50, "closed", None))

    active_labels = set()
    pr_phases = {}
    issue_closed = False
    transitions = []
    events.sort(key=lambda event: (event[0], event[1]))
    for event_at, same_time in groupby(events, key=lambda event: event[0]):
        for _, _, kind, value in same_time:
            if kind == "label_add":
                active_labels.add(value)
            elif kind == "label_remove":
                active_labels.discard(value)
            elif kind == "pr_work":
                pr_phases[value] = "In Progress"
            elif kind == "pr_review":
                pr_phases[value] = "Review"
            elif kind == "pr_end":
                pr_phases.pop(value, None)
            elif kind == "closed":
                issue_closed = True
            elif kind == "reopened":
                issue_closed = False

        if issue_closed:
            stage = "Done"
        elif "Review" in pr_phases.values():
            stage = "Review"
        elif "In Progress" in pr_phases.values():
            stage = "In Progress"
        else:
            stage = _label_stage(active_labels, label_stages)

        if not transitions or stage != transitions[-1][1]:
            transitions.append((event_at, stage))

    return transitions, bool((timeline.get("pageInfo") or {}).get("hasNextPage"))


def _compute_lifecycle_cfd(issues, samples, repo, event_sample_truncated):
    if len(samples) < 2:
        return None
    intervals = {stage: [] for stage in FLOW_STAGES}
    observed, truncated = 0, 0
    for issue in issues:
        transitions, timeline_truncated = build_github_issue_lifecycle(issue, repo)
        truncated += timeline_truncated
        if timeline_truncated:
            continue  # Missing events could leave an old label active incorrectly.
        has_workflow = False
        for i, (start, stage) in enumerate(transitions):
            end = transitions[i + 1][0] if i + 1 < len(transitions) else None
            intervals[stage].append((start, end))
            if stage not in ("Unobserved", "Done"):
                has_workflow |= start <= samples[-1] and (end is None or end > samples[0])
        observed += has_workflow
    if not observed:
        return None
    stage_counts = [count_intervals(intervals[stage], samples) for stage in FLOW_STAGES]
    if not any(any(series) for stage, series in zip(FLOW_STAGES, stage_counts) if stage not in ("Unobserved", "Done")):
        return None
    return {
        "profile": _workflow_profile(repo),
        "stages": FLOW_STAGES,
        "points": [{"date": sample.date().isoformat(), "counts": dict(zip(FLOW_STAGES, counts))}
                   for sample, counts in zip(samples, zip(*stage_counts))],
        "coverage": {"issues": len(issues), "with_workflow_signal": observed,
                     "timeline_truncated": truncated, "event_sample_truncated": event_sample_truncated},
    }


def _window_start(issues_raw, weekly_bins):
    start = weekly_bins[0]
    if issues_raw.get("closed_truncated"):
        updated = [_parse(i.get("updatedAt")) for i in issues_raw.get("closed") or []]
        updated = [u for u in updated if u]
        if updated:
            start = max(start, min(updated))
    return start


def _issue_open_intervals(issue, created, closed, *, history_capped):
    if created is None:
        return []
    timeline = issue.get("timelineItems") or {}
    if history_capped or (timeline.get("pageInfo") or {}).get("hasNextPage"):
        return [(created, closed)]
    events = []
    for event in timeline.get("nodes") or []:
        kind = (event or {}).get("__typename")
        if kind in ("ClosedEvent", "ReopenedEvent"):
            stamp = _parse(event.get("createdAt"))
            if stamp and stamp >= created:
                events.append((stamp, kind))
    if closed is not None:
        events.append((closed, "ClosedEvent"))
    intervals = []
    start = created
    for stamp, kind in sorted(events):
        if kind == "ClosedEvent" and start is not None:
            intervals.append((start, stamp))
            start = None
        elif kind == "ReopenedEvent" and start is None:
            start = stamp
    if (start is None) != (closed is not None):
        return [(created, closed)]
    if start is not None:
        intervals.append((start, None))
    return intervals


def compute_issue_metrics(issues_raw, weekly_bins, now, stale_days=STALE_DAYS, *, repo=None):
    """Turn raw issue records into the `issues` payload, or None when absent."""
    if issues_raw is None:
        return None

    open_issues = issues_raw.get("open") or []
    closed_issues = issues_raw.get("closed") or []
    rest = issues_raw.get("source") == "rest"
    open_available = issues_raw.get("open_available", True)
    closed_available = issues_raw.get("closed_available", True)
    flow_available = open_available and closed_available
    window_start = _window_start(issues_raw, weekly_bins)
    # (issue, created, closed) rows, parsed once
    open_rows = [(i, _parse(i.get("createdAt")), None) for i in open_issues]
    closed_rows = [(i, _parse(i.get("createdAt")), _parse(i.get("closedAt"))) for i in closed_issues]
    sampled = open_rows + closed_rows

    history_start = _parse(issues_raw.get("history_start"))
    intervals = [interval for issue, made, done in sampled
                 for interval in _issue_open_intervals(issue, made, done, history_capped=history_start is not None)]
    arrivals = [made for made, _ in intervals]
    resolutions = [done for _, done in intervals if done] + [done for _, made, done in closed_rows if made is None and done]
    weekly = []
    for start, end, arrived, resolved, open_count in zip(
        weekly_bins,
        weekly_bins[1:],
        count_events_in_windows(arrivals, weekly_bins),
        count_events_in_windows(resolutions, weekly_bins),
        count_intervals(intervals, weekly_bins[1:]),
    ):
        if not flow_available or start < window_start:
            continue
        weekly.append({
            "week": end.strftime("%Y-%m-%d"),
            "arrived": arrived,
            "resolved": resolved,
            "net": arrived - resolved,
            "open_count": open_count,
        })

    modern_closed = [i for i, _, done in closed_rows if done and done >= window_start]

    age_counts = {label: 0 for label, _, _ in AGE_BUCKETS}
    stale_items = []
    for it, made, _ in open_rows:
        if not made:
            continue
        age_days = elapsed(made, now, 86400.0)
        for label, low, high in AGE_BUCKETS:
            if age_days >= low and (high is None or age_days < high):
                age_counts[label] += 1
                break
        last = _parse(it.get("updatedAt")) if rest else _last_activity(it)
        idle_days = elapsed(last, now, 86400.0) if last else age_days
        if idle_days > stale_days:
            stale_items.append({
                "number": it.get("number"),
                "title": it.get("title", ""),
                "age_days": age_days,
                "days_since_activity": idle_days,
            })
    stale_items.sort(key=lambda s: s["days_since_activity"], reverse=True)

    lifecycle_samples = [t for t in weekly_bins[1:] if t >= window_start and (history_start is None or t >= history_start)]
    if rest and history_start and len(lifecycle_samples) < 2:
        start = max(window_start, history_start)
        days = min(7, (now - start).days)
        lifecycle_samples = [now - datetime.timedelta(days=i) for i in range(days, -1, -1)]
    return {
        "sample_size": len(sampled),
        "open_available": open_available,
        "closed_available": closed_available,
        "stale_basis": "updated" if rest else "comment",
        "window_start": window_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "closed_truncated": bool(issues_raw.get("closed_truncated")),
        "open_truncated": bool(issues_raw.get("open_truncated")),
        "open_now": len(open_issues) if open_available else None,
        "cycle_time": {
            "baseline": _cycle_stats(issues_raw.get("pre_ai") or []),
            "modern": _cycle_stats(modern_closed),
        },
        "weekly": weekly,
        "net_8w": sum(w["net"] for w in weekly[-NET_WINDOW_WEEKS:]) if len(weekly) >= NET_WINDOW_WEEKS else None,
        "age_buckets": [{"label": label, "count": age_counts[label]} for label, _, _ in AGE_BUCKETS],
        "stale": {"threshold_days": stale_days, "count": len(stale_items), "items": stale_items[:STALE_ITEM_LIMIT]},
        "lifecycle_cfd": (_compute_lifecycle_cfd(open_issues + modern_closed, lifecycle_samples, repo, bool(history_start))
                          if flow_available else None),
        "resolution": None if rest else _resolution_summary(modern_closed),
    }


def _resolution_summary(closed):
    total = len(closed)
    with_pr = sum(bool((issue.get("closedByPullRequestsReferences") or {}).get("totalCount")) for issue in closed)
    return {"closed_total": total, "with_pr": with_pr, "without_pr": total - with_pr,
            "pct_with_pr": with_pr * 100.0 / total if total else 0.0}


def _same_public_repo(item, repo):
    source = item.get("repository") or {}
    return source.get("isPrivate") is False and (source.get("nameWithOwner") or "").lower() == repo.lower()


def _search_issues(repo, search_query, cap, run):
    """Page through an issue search; returns (nodes, truncated). Raises on failure."""
    nodes, cursor, total_count = [], None, 0
    while len(nodes) < cap:
        cmd = [
            "gh", "api", "graphql", "--hostname", "github.com",
            "-F", f"query={ISSUE_QUERY}",
            "-f", f"searchQuery={search_query}",
            "-F", f"first={PAGE_SIZE}",
        ]
        if cursor:
            cmd.extend(["-f", f"cursor={cursor}"])
        res = run(cmd, capture_output=True, text=True, encoding="utf-8", check=True)
        search = json.loads(res.stdout)["data"]["search"]
        for node in search["nodes"]:
            if not node:
                continue
            linked = node.get("closedByPullRequestsReferences")
            if linked and "nodes" in linked:
                linked["nodes"] = [
                    {key: value for key, value in pr.items() if key != "repository"}
                    for pr in linked["nodes"] if pr and _same_public_repo(pr, repo)
                ]
                linked["totalCount"] = len(linked["nodes"])
            nodes.append(node)
        total_count = max(total_count, search.get("issueCount") or 0)
        info = search["pageInfo"]
        if not info["hasNextPage"]:
            return nodes[:cap], total_count > len(nodes[:cap])
        cursor = info["endCursor"]
    return nodes[:cap], info["hasNextPage"] or total_count > cap


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
            futures = {name: pool.submit(_search_issues, repo, q, cap, run) for name, (q, cap) in searches.items()}
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


async def _rest_json(url, headers):
    status, _, payload = await asyncio.wait_for(github_api.request_json(url, headers=headers, timeout=12), 12)
    if status != 200:
        raise ValueError("GitHub issue collection failed")
    return payload


async def _search_rest_issues(query, headers, sort):
    items, total, incomplete = {}, 0, False
    for page in range(1, REST_ISSUE_PAGE_CAP + 1):
        url = "https://api.github.com/search/issues?" + urlencode(
            {"q": query, "sort": sort, "order": "desc", "per_page": PAGE_SIZE, "page": page}
        )
        try:
            payload = await _rest_json(url, headers)
        except (ValueError, TimeoutError, httpx.HTTPError):
            if not items:
                raise
            return list(items.values()), True
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise ValueError("GitHub returned an invalid issue page")
        rows = payload["items"]
        count = payload.get("total_count")
        total = max(total, count if type(count) is int else len(rows))
        incomplete |= payload.get("incomplete_results", False) is not False
        for row in rows:
            if (not isinstance(row, dict) or type(row.get("number")) is not int or row["number"] < 1
                    or row.get("state") not in ("open", "closed") or "pull_request" in row):
                raise ValueError("GitHub returned an invalid issue")
            items[row["number"]] = {
                "number": row["number"], "title": row.get("title"), "body": row.get("body"),
                "createdAt": row.get("created_at"), "updatedAt": row.get("updated_at"),
                "closedAt": row.get("closed_at"), "state": row["state"].upper(),
            }
        if len(rows) < PAGE_SIZE or len(items) >= total:
            break
    return list(items.values()), incomplete or len(items) < total


async def _enrich_rest_issue_history(repo, headers, issues):
    """Use bounded repository-wide event history; never make a request per issue."""
    by_number = {issue["number"]: issue for issue in issues}
    events_by_type = {"labeled": "LabeledEvent", "unlabeled": "UnlabeledEvent",
                      "closed": "ClosedEvent", "reopened": "ReopenedEvent"}
    relevant = []
    oldest = None
    capped = True
    for page in range(1, REST_EVENT_PAGE_CAP + 1):
        payload = await _rest_json(
            f"https://api.github.com/repos/{repo}/issues/events?per_page=100&page={page}", headers
        )
        if not isinstance(payload, list) or any(not isinstance(event, dict) for event in payload):
            raise ValueError("GitHub returned invalid issue history")
        for event in payload:
            stamp = _parse(event.get("created_at"))
            if stamp is None:
                continue
            oldest = stamp if oldest is None else min(oldest, stamp)
            subject = event.get("issue")
            kind = events_by_type.get(event.get("event"))
            if kind and isinstance(subject, dict) and "pull_request" not in subject:
                number = subject.get("number")
                if number in by_number:
                    relevant.append((number, {"__typename": kind, "createdAt": event["created_at"],
                                             "label": event.get("label")}))
        if len(payload) < PAGE_SIZE:
            capped = False
            break
    if capped and oldest is None:
        raise ValueError("GitHub did not timestamp the issue history")
    for number, event in reversed(relevant):
        by_number[number].setdefault("timelineItems", {"nodes": []})["nodes"].append(event)
    return oldest.isoformat() if capped else None


async def collect_rest_issues(repo, headers, now):
    """Collect bounded issue details independently of PRs or model providers."""
    since = (now - datetime.timedelta(weeks=ISSUE_WINDOW_WEEKS)).date().isoformat()
    queries = ((f"repo:{repo} is:issue is:open", "created"),
               (f"repo:{repo} is:issue is:closed closed:>={since}", "updated"))
    results = await asyncio.gather(
        *(_search_rest_issues(query, headers, sort) for query, sort in queries),
        return_exceptions=True,
    )
    def available(result):
        if isinstance(result, (ValueError, TimeoutError, httpx.HTTPError)):
            return None
        if isinstance(result, BaseException):
            raise result
        return result

    open_result, closed_result = map(available, results)
    if open_result is None and closed_result is None:
        return None
    opened, open_truncated = open_result or ([], False)
    closed, closed_truncated = closed_result or ([], False)
    # A reopened issue can move between the concurrent searches. Prefer the open observation.
    open_numbers = {item["number"] for item in opened}
    closed = [item for item in closed if item["number"] not in open_numbers]
    history_start = None
    if open_result is not None and closed_result is not None and (opened or closed) and _workflow_label_stages(repo):
        try:
            history_start = await _enrich_rest_issue_history(repo, headers, opened + closed)
        except (ValueError, TimeoutError, httpx.HTTPError):
            pass
    return {"open": opened, "closed": closed, "pre_ai": [],
            "open_truncated": open_truncated, "closed_truncated": closed_truncated,
            "source": "rest", "history_start": history_start,
            "open_available": open_result is not None, "closed_available": closed_result is not None}
