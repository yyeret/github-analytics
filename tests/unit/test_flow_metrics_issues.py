from analyze import compute_flow_metrics
from issues import compute_issue_metrics  # noqa: F401  (import check)


def minimal_bundle(**extra):
    bundle = {"repo": "o/r", "pre_ai_prs": [], "recent_prs": [], "open_prs": [], "captured_at": "2026-10-01T00:00:00"}
    bundle.update(extra)
    return bundle


def test_bundle_without_issue_key_returns_issues_none_and_keeps_existing_keys():
    out = compute_flow_metrics(minimal_bundle())
    assert out["issues"] is None
    for key in ("cfd_data", "throughput_data", "recent_stats", "pre_stats", "upstream_breakdown"):
        assert key in out


def test_bundle_with_issues_returns_the_issue_payload():
    issues = {"open": [], "closed": [], "pre_ai": [], "closed_truncated": False, "open_truncated": False}
    out = compute_flow_metrics(minimal_bundle(issues=issues))
    assert out["issues"]["open_now"] == 0
    assert "weekly" in out["issues"]
