"""Unit tests for Tessl Factory issue lifecycle detection and CFD generation."""
import datetime as dt

from issues import build_tessl_factory_issue_lifecycle, compute_issue_metrics


def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_tessl_factory_happy_path_and_ignores_post_pr_summary_comments():
    created = dt.datetime(2026, 11, 16, 10, 0)
    row = {
        "number": 184,
        "createdAt": iso(created),
        "closedAt": iso(created + dt.timedelta(hours=8)),
        "state": "CLOSED",
        "labels": ["enhancement", "tessl"],
        "timelineItems": {
            "pageInfo": {"hasNextPage": False},
            "nodes": [
                {"__typename": "LabeledEvent", "createdAt": iso(created + dt.timedelta(minutes=30)), "label": {"name": "tessl"}},
                {
                    "__typename": "IssueComment",
                    "createdAt": iso(created + dt.timedelta(minutes=31)),
                    "body": "Tessl is working on this. I'll post the pull request here when it's ready.\n\n<!-- tessl-factory -->",
                },
                {
                    "__typename": "IssueComment",
                    "createdAt": iso(created + dt.timedelta(hours=1)),
                    "body": "PR opened: https://github.com/yyeret/mini-crm/pull/192\n\n<!-- tessl-factory -->",
                },
                {
                    "__typename": "IssueComment",
                    "createdAt": iso(created + dt.timedelta(hours=1, minutes=5)),
                    "body": "Segments are built and in PR #192. Nobody has seen them on live yet; cannot deploy yet.\n\n<!-- tessl-factory -->",
                },
                {"__typename": "ClosedEvent", "createdAt": iso(created + dt.timedelta(hours=8))},
            ],
        },
    }
    transitions, truncated = build_tessl_factory_issue_lifecycle(row)
    assert not truncated
    assert [stage for _, stage in transitions] == [
        "Backlog",
        "Queued",
        "Agent Working",
        "PR Review",
        "Done",
    ]


def test_tessl_factory_blocked_then_resolved_by_linked_pr():
    created = dt.datetime(2026, 11, 18, 10, 0)
    row = {
        "number": 183,
        "createdAt": iso(created),
        "closedAt": None,
        "state": "OPEN",
        "labels": ["tessl"],
        "timelineItems": {
            "pageInfo": {"hasNextPage": False},
            "nodes": [
                {"__typename": "LabeledEvent", "createdAt": iso(created + dt.timedelta(minutes=2)), "label": {"name": "tessl"}},
                {
                    "__typename": "IssueComment",
                    "createdAt": iso(created + dt.timedelta(minutes=3)),
                    "body": "I have not started this yet, because it depends on two contracts.\n\n<!-- tessl-factory -->",
                },
            ],
        },
        "closedByPullRequestsReferences": {
            "nodes": [
                {
                    "number": 195,
                    "createdAt": iso(created + dt.timedelta(hours=7)),
                    "isDraft": False,
                    "mergedAt": None,
                    "closedAt": None,
                }
            ]
        },
    }
    transitions, _ = build_tessl_factory_issue_lifecycle(row)
    assert [stage for _, stage in transitions] == ["Backlog", "Queued", "Blocked", "PR Review"]


def test_structured_tessl_lifecycle_marker_drives_explicit_transitions():
    created = dt.datetime(2026, 11, 18, 10, 0)
    row = {
        "number": 200,
        "createdAt": iso(created),
        "closedAt": None,
        "state": "OPEN",
        "labels": ["factory:ce-pilot"],
        "timelineItems": {
            "pageInfo": {"hasNextPage": False},
            "nodes": [
                {
                    "__typename": "IssueComment",
                    "createdAt": iso(created + dt.timedelta(minutes=5)),
                    "body": "Starting run.\n\n<!-- tessl-lifecycle: stage=in_progress run=abc -->",
                },
                {
                    "__typename": "IssueComment",
                    "createdAt": iso(created + dt.timedelta(minutes=20)),
                    "body": "PR ready.\n\n<!-- tessl-lifecycle: stage=pr_review pr=201 -->",
                },
                {
                    "__typename": "IssueComment",
                    "createdAt": iso(created + dt.timedelta(minutes=45)),
                    "body": "CI blocked by billing.\n\n<!-- tessl-lifecycle: stage=blocked reason=ci -->",
                },
            ],
        },
    }
    transitions, _ = build_tessl_factory_issue_lifecycle(row)
    assert [stage for _, stage in transitions] == ["Backlog", "Agent Working", "PR Review", "Blocked"]


def test_compute_issue_metrics_uses_daily_sampling_for_short_tessl_factory_burst():
    now = dt.datetime(2026, 11, 20, 12, 0)
    weekly_bins = [now - dt.timedelta(days=7 * i) for i in range(8, -1, -1)]
    open_issue = {
        "number": 147,
        "title": "Pilot issue",
        "createdAt": "2026-11-16T18:15:00Z",
        "updatedAt": "2026-11-16T18:20:00Z",
        "closedAt": None,
        "state": "OPEN",
        "labels": ["factory:ce-pilot"],
        "timelineItems": {
            "pageInfo": {"hasNextPage": False},
            "nodes": [
                {"__typename": "LabeledEvent", "createdAt": "2026-11-16T18:15:41Z", "label": {"name": "factory:ce-pilot"}},
                {
                    "__typename": "IssueComment",
                    "createdAt": "2026-11-16T18:16:29Z",
                    "body": "Tessl is working on this.\n\n<!-- tessl-factory -->",
                },
                {
                    "__typename": "IssueComment",
                    "createdAt": "2026-11-16T18:18:49Z",
                    "body": "PR opened: https://github.com/yyeret/mini-crm/pull/148\n\n<!-- tessl-factory -->",
                },
            ],
        },
    }
    blocked_issue = {
        "number": 153,
        "title": "Blocked issue",
        "createdAt": "2026-11-17T00:21:00Z",
        "updatedAt": "2026-11-17T00:28:00Z",
        "closedAt": None,
        "state": "OPEN",
        "labels": ["tessl"],
        "timelineItems": {
            "pageInfo": {"hasNextPage": False},
            "nodes": [
                {"__typename": "LabeledEvent", "createdAt": "2026-11-17T00:21:04Z", "label": {"name": "tessl"}},
                {
                    "__typename": "IssueComment",
                    "createdAt": "2026-11-17T00:28:07Z",
                    "body": "I could not finish the trace from here.\n\n<!-- tessl-factory -->",
                },
            ],
        },
    }
    closed_issue = {
        "number": 144,
        "title": "Closed issue",
        "createdAt": "2026-11-16T10:53:00Z",
        "updatedAt": "2026-11-16T16:37:34Z",
        "closedAt": "2026-11-16T16:37:33Z",
        "state": "CLOSED",
        "labels": ["factory:ce-pilot"],
        "timelineItems": {
            "pageInfo": {"hasNextPage": False},
            "nodes": [
                {"__typename": "LabeledEvent", "createdAt": "2026-11-16T16:32:51Z", "label": {"name": "factory:ce-pilot"}},
                {"__typename": "ClosedEvent", "createdAt": "2026-11-16T16:37:33Z"},
            ],
        },
    }
    raw = {"open": [open_issue, blocked_issue], "closed": [closed_issue], "pre_ai": [], "source": "rest"}
    out = compute_issue_metrics(raw, weekly_bins, now, repo="yyeret/mini-crm")
    cfd = out["lifecycle_cfd"]
    assert cfd is not None
    assert cfd["source"] == "tessl_factory"
    assert cfd["profile"] == "Tessl Factory"
    assert cfd["coverage"]["issues"] == 3
    assert cfd["coverage"]["with_workflow_signal"] == 2
    dates = [row["date"] for row in cfd["points"]]
    assert dates == ["2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20"]
    assert cfd["points"][-1]["counts"] == {"Blocked": 1, "PR Review": 1, "Done": 1}


def test_explicit_blocked_marker_overrides_active_linked_pr_review():
    created = dt.datetime(2026, 11, 18, 10, 0)
    row = {
        "number": 210,
        "createdAt": iso(created),
        "closedAt": None,
        "state": "OPEN",
        "labels": ["tessl"],
        "closedByPullRequestsReferences": {
            "nodes": [
                {
                    "number": 211,
                    "createdAt": iso(created + dt.timedelta(minutes=30)),
                    "isDraft": False,
                    "mergedAt": None,
                    "closedAt": None,
                }
            ]
        },
        "timelineItems": {
            "pageInfo": {"hasNextPage": False},
            "nodes": [
                {
                    "__typename": "IssueComment",
                    "createdAt": iso(created + dt.timedelta(hours=1)),
                    "body": "CI refused.\n\n<!-- tessl-lifecycle: stage=blocked reason=ci_blocked -->",
                },
                {
                    "__typename": "IssueComment",
                    "createdAt": iso(created + dt.timedelta(hours=2)),
                    "body": "CI green.\n\n<!-- tessl-lifecycle: stage=pr_review pr=211 -->",
                },
            ],
        },
    }
    transitions, _ = build_tessl_factory_issue_lifecycle(row)
    assert [stage for _, stage in transitions] == ["Backlog", "PR Review", "Blocked", "PR Review"]


def test_pre_cutoff_evidence_initializes_stage_and_unknown_pre_cutoff_is_unobserved():
    cutoff = dt.datetime(2026, 11, 16, 0, 0)
    with_pre_cutoff_comment = {
        "number": 212,
        "createdAt": "2026-11-10T00:00:00Z",
        "closedAt": None,
        "state": "OPEN",
        "labels": ["tessl"],
        "timelineItems": {
            "pageInfo": {"hasNextPage": False},
            "nodes": [
                {
                    "__typename": "IssueComment",
                    "createdAt": "2026-11-15T12:00:00Z",
                    "body": "PR opened: https://github.com/o/r/pull/1\n\n<!-- tessl-factory -->",
                }
            ],
        },
    }
    without_pre_cutoff_evidence = {
        "number": 213,
        "createdAt": "2026-11-10T00:00:00Z",
        "closedAt": None,
        "state": "OPEN",
        "labels": ["tessl"],
        "timelineItems": {"pageInfo": {"hasNextPage": False}, "nodes": []},
    }
    t1, _ = build_tessl_factory_issue_lifecycle(with_pre_cutoff_comment, history_start=cutoff)
    t2, _ = build_tessl_factory_issue_lifecycle(without_pre_cutoff_evidence, history_start=cutoff)
    assert t1 == [(cutoff, "PR Review")]
    assert t2 == [(cutoff, "Unobserved")]


def test_open_issue_cannot_enter_done_via_comment_marker():
    created = dt.datetime(2026, 11, 18, 10, 0)
    row = {
        "number": 214,
        "createdAt": iso(created),
        "closedAt": None,
        "state": "OPEN",
        "labels": ["tessl"],
        "timelineItems": {
            "pageInfo": {"hasNextPage": False},
            "nodes": [
                {
                    "__typename": "IssueComment",
                    "createdAt": iso(created + dt.timedelta(minutes=5)),
                    "body": "Finished.\n\n<!-- tessl-lifecycle: stage=done -->",
                }
            ],
        },
    }
    transitions, _ = build_tessl_factory_issue_lifecycle(row)
    assert [stage for _, stage in transitions] == ["Backlog"]


def test_graphql_labels_without_collected_comments_do_not_select_factory_cfd():
    now = dt.datetime(2026, 11, 20, 12, 0)
    weekly_bins = [now - dt.timedelta(days=7 * i) for i in range(8, -1, -1)]
    labeled_only = {
        "number": 215,
        "title": "Labeled without comment collection",
        "createdAt": "2026-11-16T10:00:00Z",
        "updatedAt": "2026-11-16T10:05:00Z",
        "closedAt": None,
        "state": "OPEN",
        "timelineItems": {
            "pageInfo": {"hasNextPage": False},
            "nodes": [
                {"__typename": "LabeledEvent", "createdAt": "2026-11-16T10:05:00Z", "label": {"name": "tessl"}}
            ],
        },
    }
    raw = {"open": [labeled_only], "closed": [], "pre_ai": []}
    out = compute_issue_metrics(raw, weekly_bins, now, repo="yyeret/mini-crm")
    assert out["lifecycle_cfd"] is None
