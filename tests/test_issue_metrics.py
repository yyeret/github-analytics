import datetime as dt

from issues import compute_issue_metrics

BASE = dt.datetime(2026, 8, 1)
NOW = dt.datetime(2026, 10, 1)


def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def weekly_bins(start=BASE, weeks=8):
    return [start + dt.timedelta(days=7 * i) for i in range(weeks + 1)]


def issue(number, created, closed=None, updated=None, reason=None, prs=0, last_comment=None):
    comments = [{"createdAt": iso(last_comment)}] if last_comment else []
    return {
        "number": number,
        "title": f"Issue {number}",
        "createdAt": iso(created),
        "closedAt": iso(closed) if closed else None,
        "updatedAt": iso(updated or closed or created),
        "state": "CLOSED" if closed else "OPEN",
        "stateReason": reason,
        "comments": {"totalCount": len(comments), "nodes": comments},
        "closedByPullRequestsReferences": {"totalCount": prs},
    }


def raw(open_=(), closed=(), pre_ai=(), closed_truncated=False, open_truncated=False):
    return {
        "open": list(open_),
        "closed": list(closed),
        "pre_ai": list(pre_ai),
        "closed_truncated": closed_truncated,
        "open_truncated": open_truncated,
    }


def test_ae1_not_planned_close_without_pr_counts_in_cycle_time_and_share():
    closed = [issue(1, BASE + dt.timedelta(days=1), BASE + dt.timedelta(days=3), reason="NOT_PLANNED", prs=0)]
    out = compute_issue_metrics(raw(closed=closed), weekly_bins(), NOW)
    assert out["cycle_time"]["modern"]["n"] == 1
    assert out["resolution"]["without_pr"] == 1
    assert out["resolution"]["with_pr"] == 0
    assert out["resolution"]["pct_with_pr"] == 0.0


def test_ae2_eight_weeks_of_net_arrivals_raise_the_open_count_every_week():
    open_, closed = [], []
    n = 0
    for i in range(8):
        week_start = BASE + dt.timedelta(days=7 * i)
        for k in range(3):
            n += 1
            created = week_start + dt.timedelta(days=1)
            if k == 0:
                closed.append(issue(n, created, week_start + dt.timedelta(days=2)))
            else:
                open_.append(issue(n, created))
    out = compute_issue_metrics(raw(open_=open_, closed=closed), weekly_bins(), NOW)
    weeks = out["weekly"]
    assert len(weeks) == 8
    assert all(w["net"] == 2 for w in weeks)
    counts = [w["open_count"] for w in weeks]
    assert counts == [2, 4, 6, 8, 10, 12, 14, 16]


def test_ae3_open_issue_with_no_recent_activity_is_stale_with_its_age():
    stale_comment = issue(10, dt.datetime(2026, 5, 1), last_comment=NOW - dt.timedelta(days=120))
    fresh = issue(11, dt.datetime(2026, 5, 1), last_comment=NOW - dt.timedelta(days=10))
    no_comments = issue(12, NOW - dt.timedelta(days=100))
    out = compute_issue_metrics(raw(open_=[stale_comment, fresh, no_comments]), weekly_bins(), NOW)
    stale = out["stale"]
    assert stale["threshold_days"] == 90
    assert stale["count"] == 2
    assert [i["number"] for i in stale["items"]] == [10, 12]
    assert round(stale["items"][0]["days_since_activity"]) == 120


def test_percentiles_use_the_existing_index_rule():
    closed = [
        issue(d, BASE + dt.timedelta(days=1), BASE + dt.timedelta(days=1 + d)) for d in range(1, 11)
    ]
    out = compute_issue_metrics(raw(closed=closed), weekly_bins(), NOW)
    modern = out["cycle_time"]["modern"]
    assert modern["n"] == 10
    assert modern["median_hours"] == 6 * 24
    assert modern["p85_hours"] == 9 * 24


def test_open_count_excludes_issues_created_later_or_closed_at_the_week_edge():
    bins = weekly_bins(weeks=2)
    edge = bins[1]
    created_later = issue(1, bins[1] + dt.timedelta(hours=1))
    closed_at_edge = issue(2, bins[0] + dt.timedelta(days=1), closed=edge)
    out = compute_issue_metrics(raw(open_=[created_later], closed=[closed_at_edge]), bins, NOW)
    first, second = out["weekly"]
    assert first["open_count"] == 0
    assert second["open_count"] == 1


def test_truncated_closed_sample_drops_weeks_before_the_oldest_sampled_update():
    cutoff = BASE + dt.timedelta(days=14)
    closed = [issue(1, BASE, closed=cutoff + dt.timedelta(days=1), updated=cutoff)]
    out = compute_issue_metrics(raw(closed=closed, closed_truncated=True), weekly_bins(), NOW)
    assert out["closed_truncated"] is True
    assert out["window_start"] == iso(cutoff)
    assert len(out["weekly"]) == 6
    assert out["weekly"][0]["week"] == (BASE + dt.timedelta(days=21)).strftime("%Y-%m-%d")


def test_open_issue_aged_exactly_seven_days_lands_in_the_seven_to_thirty_bucket():
    out = compute_issue_metrics(raw(open_=[issue(1, NOW - dt.timedelta(days=7))]), weekly_bins(), NOW)
    buckets = {b["label"]: b["count"] for b in out["age_buckets"]}
    assert buckets["7-30d"] == 1
    assert buckets["<7d"] == 0


def test_empty_input_returns_zeroed_stats_and_none_returns_none():
    out = compute_issue_metrics(raw(), weekly_bins(), NOW)
    assert out["open_now"] == 0
    assert out["cycle_time"]["modern"] == {"n": 0, "median_hours": 0, "p85_hours": 0}
    assert out["stale"]["count"] == 0 and out["stale"]["items"] == []
    assert out["resolution"]["closed_total"] == 0
    assert compute_issue_metrics(None, weekly_bins(), NOW) is None


def test_baseline_era_issues_only_affect_baseline_stats():
    pre = [issue(1, dt.datetime(2021, 7, 1), dt.datetime(2021, 7, 11))]
    out = compute_issue_metrics(raw(pre_ai=pre), weekly_bins(), NOW)
    assert out["cycle_time"]["baseline"]["n"] == 1
    assert out["cycle_time"]["baseline"]["median_hours"] == 240
    assert out["cycle_time"]["modern"]["n"] == 0
    assert all(w["arrived"] == 0 and w["open_count"] == 0 for w in out["weekly"])
