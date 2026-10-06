import datetime

import pytest
from freezegun import freeze_time

from analyze import compute_flow_metrics, parse_date
from tests.factories import make_commit, make_issue, make_pr, make_review

NOW = "2024-03-01T00:00:00"


def _recent_set():
    """A: Human 24h/size10/coding6h; B: Human 48h/size100/coding12h; C: bot 12h/size5."""
    a = make_pr(1, "2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z", additions=10, deletions=0,
                ready_for_review="2024-01-01T06:00:00Z",
                reviews=[make_review("2024-01-01T12:00:00Z", "CHANGES_REQUESTED"), make_review("2024-01-01T18:00:00Z", "APPROVED")])
    b = make_pr(2, "2024-01-03T00:00:00Z", "2024-01-05T00:00:00Z", additions=60, deletions=40, ready_for_review="2024-01-03T12:00:00Z")
    c = make_pr(3, "2024-01-08T00:00:00Z", "2024-01-08T12:00:00Z", additions=5, deletions=0, login="dependabot")
    return [a, b, c]


def _open_set():
    drafty = make_pr(10, "2024-02-20T00:00:00Z", None, title="[draft] wip")
    approved = make_pr(11, "2024-02-25T00:00:00Z", None, reviews=[make_review("2024-02-26T00:00:00Z", "APPROVED")])
    queued = make_pr(12, "2024-02-26T00:00:00Z", None)
    ancient = make_pr(13, "2023-12-01T00:00:00Z", None)
    return [drafty, approved, queued, ancient]


@pytest.fixture
def metrics():
    with freeze_time(NOW):
        return compute_flow_metrics({"repo": "o/r", "captured_at": "t", "pre_ai_prs": [], "recent_prs": _recent_set(), "open_prs": _open_set()})


@freeze_time(NOW)
def test_empty_input_gives_defaults_without_error():
    m = compute_flow_metrics({})
    assert m["recent_stats"]["total"] == 0
    assert m["recent_stats"]["avg_cycle"] == 0
    assert (m["p50_sle"], m["p85_sle"]) == (12.0, 24.0)
    assert m["spearman_corr"] == 0.0
    assert m["recent_prs_summary"] == [] and m["comet_points"] == [] and m["active_wip_points"] == []
    assert m["stage_percentiles"]["coding"]["p50"] == 0.5


def test_identity_fields_pass_through(metrics):
    assert metrics["repo"] == "o/r"
    assert metrics["captured_at"] == "t"


def test_recent_era_stats(metrics):
    s = metrics["recent_stats"]
    assert s["total"] == 3
    assert s["avg_cycle"] == pytest.approx(28.0)
    assert s["avg_size"] == pytest.approx(115 / 3)
    assert s["avg_coding"] == pytest.approx(9.0)  # only A and B have coding times
    assert s["flow_efficiency"] == pytest.approx(20.0)  # A: 6/30, B: 12/60
    assert s["avg_reviews"] == pytest.approx(2 / 3)
    assert s["avg_loops"] == pytest.approx(1 / 3)
    assert s["sdd_ratio"] == pytest.approx(2 / 3)
    assert s["human_ratio"] == pytest.approx(2 / 3)
    assert s["agentic_ratio"] == pytest.approx(1 / 3)
    assert s["assisted_ratio"] == 0
    assert s["human_ratio"] + s["assisted_ratio"] + s["agentic_ratio"] == pytest.approx(1.0)


def test_pre_era_is_computed_independently():
    with freeze_time(NOW):
        m = compute_flow_metrics({"pre_ai_prs": [make_pr(1, "2021-07-01T00:00:00Z", "2021-07-03T00:00:00Z")], "recent_prs": _recent_set()})
    assert m["pre_stats"]["total"] == 1
    assert m["pre_stats"]["avg_cycle"] == pytest.approx(48.0)
    assert m["recent_stats"]["total"] == 3


def test_sle_uses_human_prs_with_upper_median_index(metrics):
    # Human cycle times sorted: [24, 48]; index int(2*0.5)=1 and int(2*0.85)=1
    assert metrics["p50_sle"] == 48
    assert metrics["p85_sle"] == 48


@freeze_time(NOW)
def test_sle_falls_back_to_all_prs_when_no_humans():
    bots = [make_pr(i, "2024-01-01T00:00:00Z", f"2024-01-0{i + 1}T00:00:00Z", login="dependabot") for i in (1, 2)]
    m = compute_flow_metrics({"recent_prs": bots})
    assert m["p50_sle"] == 48  # all-PR sorted [24, 48]


@freeze_time(NOW)
def test_single_pr_sle_p85_equals_p50():
    m = compute_flow_metrics({"recent_prs": [make_pr(1, "2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z")]})
    assert m["p50_sle"] == m["p85_sle"] == 24


def test_spearman_of_size_vs_cycle_time(metrics):
    assert metrics["spearman_corr"] == pytest.approx(1.0)  # sizes [10,100,5] vs cycle [24,48,12]


def test_stage_percentiles(metrics):
    sp = metrics["stage_percentiles"]
    assert sp["coding"]["p50"] == pytest.approx(0.5)  # coding days sorted [0.25, 0.5]
    assert sp["coding"]["p85"] == pytest.approx(0.5)
    assert sp["review"]["p50"] == pytest.approx(0.5)  # only A has a review: 12h wait
    assert sp["merge"]["p50"] == pytest.approx(0.5)  # A: (24-12)h


@freeze_time(NOW)
def test_stage_percentile_floors():
    pr = make_pr(1, "2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z", reviews=[make_review("2024-01-01T00:00:00Z")],
                 ready_for_review="2024-01-01T00:00:00Z")
    sp = compute_flow_metrics({"recent_prs": [pr]})["stage_percentiles"]
    # 0h waits are clamped to 0.5h = 1/48 day, never below the 0.02-day floor
    assert sp["coding"]["p50"] == pytest.approx(0.5 / 24)
    assert sp["review"]["p50"] == pytest.approx(0.5 / 24)


def test_open_prs_are_staged_and_old_ones_excluded(metrics):
    wip = {p["num"]: p for p in metrics["active_wip_points"]}
    assert set(wip) == {10, 11, 12}  # 13 predates the oldest merge
    assert wip[10]["x"] == "1. Active Coding"
    assert wip[11]["x"] == "3. Merge Delay"
    assert wip[12]["x"] == "2. Review Queue"
    assert wip[10]["y"] == pytest.approx(10.0)  # 2024-02-20 -> 2024-03-01
    assert wip[11]["y"] == pytest.approx(5.0)


def test_cfd_rows_are_a_consistent_closed_system(metrics):
    rows = metrics["cfd_data"]
    assert len(rows) == 10  # 9 weekly bins from 2024-01-02 plus the final "now" row
    for r in rows:
        assert r["opened"] == r["merged"] + r["wip"]
        assert r["wip"] == r["review"] + r["coding"] + r["backlog"]
    merged = [r["merged"] for r in rows]
    assert merged == sorted(merged)
    assert merged[0] == 1  # PR A merged exactly at the first bin
    assert merged[-1] == 3


def test_throughput_bins_sum_to_classes_and_skip_first_bin_boundary(metrics):
    rows = metrics["throughput_data"]
    assert len(rows) == 9
    for r in rows:
        assert r["total"] == r["human"] + r["assisted"] + r["agentic"]
    # Bins are (start, end]; PR A merged exactly at the first bin's start, so it is not counted.
    assert sum(r["total"] for r in rows) == 2
    assert sum(r["agentic"] for r in rows) == 1


def test_comet_lanes_never_overlap_on_one_lane():
    prs = [
        make_pr(1, "2024-01-02T00:00:00Z", "2024-01-05T00:00:00Z"),
        make_pr(2, "2024-01-03T00:00:00Z", "2024-01-04T00:00:00Z"),
        make_pr(3, "2024-01-06T00:00:00Z", "2024-01-07T00:00:00Z"),
    ]
    with freeze_time(NOW):
        pts = compute_flow_metrics({"recent_prs": prs})["comet_points"]
    lanes = {}
    for p in pts:
        lanes.setdefault(p["lane"], []).append((p["start"], p["end"]))
    assert len(lanes) == 2  # PRs 1 and 2 overlap, 3 reuses lane 1
    for spans in lanes.values():
        spans.sort()
        for (_, end), (start, _) in zip(spans, spans[1:]):
            assert end <= start
    by_key = {p["key"]: p for p in pts}
    assert by_key[1]["lane"] == 1 and by_key[2]["lane"] == 2 and by_key[3]["lane"] == 1


@freeze_time(NOW)
def test_comet_start_uses_two_hour_default_without_coding_time():
    m = compute_flow_metrics({"recent_prs": [make_pr(1, "2024-01-02T12:00:00Z", "2024-01-03T00:00:00Z")]})
    p = m["comet_points"][0]
    assert p["end"] - p["start"] == (12 + 2) * 3600 * 1000


@freeze_time(NOW)
def test_summary_is_capped_at_fifty():
    prs = [make_pr(i, "2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z") for i in range(1, 61)]
    m = compute_flow_metrics({"recent_prs": prs})
    assert len(m["recent_prs_summary"]) == 50
    assert m["recent_stats"]["total"] == 60


@freeze_time(NOW)
def test_upstream_breakdown_defaults_without_commits():
    issue = make_issue(created="2024-01-01T00:00:00Z")
    pr = make_pr(1, "2024-01-05T00:00:00Z", "2024-01-06T00:00:00Z", issues=[issue])
    ub = compute_flow_metrics({"recent_prs": [pr]})["upstream_breakdown"]
    assert ub["total"] == 1
    assert ub["backlog_wait"] == 24.0  # default when no usable first commit
    assert ub["coding"] == pytest.approx(24 * 0.3)
    assert ub["review_wait"] == pytest.approx(12.0)  # no review: half the cycle
    assert ub["merge_delay"] == pytest.approx(12.0)


@freeze_time(NOW)
def test_upstream_breakdown_uses_first_commit_when_present():
    issue = make_issue(created="2024-01-01T00:00:00Z")
    pr = make_pr(1, "2024-01-05T00:00:00Z", "2024-01-06T00:00:00Z", issues=[issue],
                 commits=[make_commit("2024-01-03T00:00:00Z")], reviews=[make_review("2024-01-05T06:00:00Z")])
    ub = compute_flow_metrics({"recent_prs": [pr]})["upstream_breakdown"]
    assert ub["backlog_wait"] == 48.0
    assert ub["coding"] == 48.0
    assert ub["review_wait"] == 6.0
    assert ub["merge_delay"] == 18.0


@freeze_time(NOW)
def test_no_upstream_prs_leave_breakdown_zeroed():
    ub = compute_flow_metrics({"recent_prs": _recent_set()})["upstream_breakdown"]
    assert ub == {"backlog_wait": 0.0, "coding": 0.0, "review_wait": 0.0, "merge_delay": 0.0, "total": 0}


def test_result_is_json_serialisable(metrics):
    import json
    json.dumps(metrics)


@freeze_time(NOW)
def test_sle_percentile_indexes_on_ten_prs():
    # cycle times 10h..100h; sorted index int(10*0.5)=5 -> 60h, int(10*0.85)=8 -> 90h
    prs = [make_pr(i, "2024-01-01T00:00:00Z", (datetime.datetime(2024, 1, 1) + datetime.timedelta(hours=10 * i)).strftime("%Y-%m-%dT%H:%M:%SZ"))
           for i in range(1, 11)]
    m = compute_flow_metrics({"recent_prs": prs})
    assert m["p50_sle"] == 60
    assert m["p85_sle"] == 90
