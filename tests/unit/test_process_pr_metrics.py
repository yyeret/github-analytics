import datetime

import pytest
from freezegun import freeze_time

from analyze import process_pr_metrics
from tests.factories import make_commit, make_issue, make_pr, make_review


def test_missing_created_at_returns_none():
    assert process_pr_metrics({"number": 1}) is None


def test_cycle_time_size_and_merged_timestamp():
    m = process_pr_metrics(make_pr(created="2024-01-10T00:00:00Z", merged="2024-01-11T00:00:00Z", additions=30, deletions=12))
    assert m["cycle_time_hours"] == 24.0
    assert m["pr_size"] == 42
    assert m["merged_ts"] == datetime.datetime(2024, 1, 11).timestamp() * 1000.0
    assert m["classification"] == "Human"


def test_unmerged_pr_has_no_cycle_time():
    m = process_pr_metrics(make_pr(merged=None))
    assert m["cycle_time_hours"] is None
    assert m["merged_ts"] == 0.0


def test_review_counts_and_first_review_wait():
    reviews = [
        make_review("2024-01-10T06:00:00Z", "CHANGES_REQUESTED"),
        make_review("2024-01-10T03:00:00Z", "COMMENTED"),
        make_review("2024-01-10T09:00:00Z", "APPROVED"),
    ]
    m = process_pr_metrics(make_pr(created="2024-01-10T00:00:00Z", reviews=reviews))
    assert m["review_count"] == 3
    assert m["changes_requested"] == 1
    assert m["approvals"] == 1
    assert m["wait_time_to_first_review"] == 3.0


def test_no_reviews_means_no_wait_time():
    m = process_pr_metrics(make_pr())
    assert m["review_count"] == 0
    assert m["wait_time_to_first_review"] is None


def test_ready_for_review_event_defines_coding_time_and_sdd_flag():
    m = process_pr_metrics(make_pr(created="2024-01-10T00:00:00Z", ready_for_review="2024-01-10T05:00:00Z"))
    assert m["is_sdd_workflow"] is True
    assert m["coding_time_hours"] == 5.0
    assert m["ready_for_review_dt"] == "2024-01-10T05:00:00"


def test_ready_before_created_is_clamped_to_zero():
    m = process_pr_metrics(make_pr(created="2024-01-10T00:00:00Z", ready_for_review="2024-01-09T00:00:00Z"))
    assert m["coding_time_hours"] == 0.0


def test_coding_time_falls_back_to_first_commit_before_creation():
    commits = [make_commit("2024-01-09T20:00:00Z"), make_commit("2024-01-09T22:00:00Z")]
    m = process_pr_metrics(make_pr(created="2024-01-10T00:00:00Z", commits=commits))
    assert m["is_sdd_workflow"] is False
    assert m["coding_time_hours"] == 4.0
    assert m["commit_count"] == 2


def test_first_commit_after_creation_gives_zero_coding_time():
    m = process_pr_metrics(make_pr(created="2024-01-10T00:00:00Z", commits=[make_commit("2024-01-10T03:00:00Z")]))
    assert m["coding_time_hours"] == 0.0


def test_no_commits_and_no_event_means_coding_time_unknown():
    assert process_pr_metrics(make_pr())["coding_time_hours"] is None


def test_closing_issue_lead_time_and_backlog_wait():
    issue = make_issue(number=7, created="2024-01-01T00:00:00Z", title="Bug")
    commits = [make_commit("2024-01-03T00:00:00Z"), make_commit("2024-01-04T00:00:00Z")]
    m = process_pr_metrics(make_pr(created="2024-01-05T00:00:00Z", merged="2024-01-06T00:00:00Z", issues=[issue], commits=commits))
    assert m["has_upstream"] is True
    assert m["upstream_issue_number"] == 7
    assert m["upstream_issue_title"] == "Bug"
    assert m["upstream_issue_lead_time_hours"] == 5 * 24.0
    assert m["upstream_backlog_wait_hours"] == 2 * 24.0


def test_backlog_wait_is_zero_when_work_started_before_issue():
    issue = make_issue(created="2024-01-05T00:00:00Z")
    m = process_pr_metrics(make_pr(created="2024-01-06T00:00:00Z", merged="2024-01-07T00:00:00Z", issues=[issue], commits=[make_commit("2024-01-01T00:00:00Z")]))
    assert m["upstream_backlog_wait_hours"] == 0.0


def test_issue_without_commits_has_lead_time_but_no_backlog_wait():
    issue = make_issue(created="2024-01-01T00:00:00Z")
    m = process_pr_metrics(make_pr(created="2024-01-02T00:00:00Z", merged="2024-01-03T00:00:00Z", issues=[issue]))
    assert m["upstream_issue_lead_time_hours"] == 48.0
    assert m["upstream_backlog_wait_hours"] is None


@freeze_time("2024-02-01T00:00:00")
def test_open_pr_lead_time_runs_to_now():
    issue = make_issue(created="2024-01-30T00:00:00Z")
    m = process_pr_metrics(make_pr(created="2024-01-31T00:00:00Z", merged=None, issues=[issue]))
    assert m["upstream_issue_lead_time_hours"] == 48.0


def test_no_closing_issue():
    m = process_pr_metrics(make_pr())
    assert m["has_upstream"] is False
    assert m["upstream_issue_number"] is None
    assert m["upstream_issue_lead_time_hours"] is None
