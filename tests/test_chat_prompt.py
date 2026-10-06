import pytest

app = pytest.importorskip("app")


def base_metrics(**extra):
    m = {"repo": "o/r", "recent_stats": {}, "throughput_data": [], "active_wip_points": [], "stage_percentiles": {}, "recent_prs_summary": []}
    m.update(extra)
    return m


ISSUES = {
    "open_now": 42,
    "net_8w": 24,
    "cycle_time": {
        "baseline": {"n": 30, "median_hours": 240, "p85_hours": 960},
        "modern": {"n": 200, "median_hours": 120, "p85_hours": 720},
    },
    "weekly": [{"week": "2026-09-01", "arrived": 5, "resolved": 2, "net": 3, "open_count": 40}] * 8,
    "stale": {"threshold_days": 90, "count": 17, "items": [{"number": 7, "title": "Old bug", "days_since_activity": 400.0}]},
    "resolution": {"closed_total": 200, "with_pr": 150, "without_pr": 50, "pct_with_pr": 75.0},
    "closed_truncated": False,
    "open_truncated": False,
}


def test_prompt_with_issues_includes_open_and_stale_counts():
    prompt = app._build_system_prompt(base_metrics(issues=ISSUES))
    assert "42" in prompt and "17" in prompt
    assert "#7" in prompt
    assert "+24" in prompt


def test_prompt_without_issues_is_unchanged():
    without_key = app._build_system_prompt(base_metrics())
    with_none = app._build_system_prompt(base_metrics(issues=None))
    assert without_key == with_none
    assert "Issue Backlog" not in without_key
