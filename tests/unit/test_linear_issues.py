"""Unit tests for Linear issue collection and lifecycle CFD metrics."""

import asyncio
import datetime

import pytest

import github_api
import issues as issues_mod
import linear_issues as li


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://linear.app/yeret-agility/team/CRM/overview", {"org": "yeret-agility", "team_key": "CRM"}),
        ("https://linear.app/yeret-agility/team/crm/active/", {"org": "yeret-agility", "team_key": "CRM"}),
        ("linear.app/yeret-agility/team/CRM", {"org": "yeret-agility", "team_key": "CRM"}),
        ("yeret-agility/crm", {"org": "yeret-agility", "team_key": "CRM"}),
        ("CRM", {"org": None, "team_key": "CRM"}),
    ],
)
def test_parse_linear_team_valid(raw, expected):
    assert li.parse_linear_team(raw) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "https://example.com/yeret-agility/team/CRM",
        "https://linear.app/yeret-agility/settings",
        "a/b/c",
        "123bad",
        "bad org/CRM",
        "yeret-agility/999",
    ],
)
def test_parse_linear_team_invalid(raw):
    with pytest.raises(li.LinearError) as exc:
        li.parse_linear_team(raw)
    assert exc.value.status_code == 400


@pytest.mark.unit
def test_order_workflow_states_sorts_by_type_then_position():
    states = [
        {"name": "Done", "type": "completed", "position": 1},
        {"name": "Review PR", "type": "started", "position": 200},
        {"name": "Building", "type": "started", "position": 10},
        {"name": "Backlog", "type": "backlog", "position": 0},
        {"name": "Ready", "type": "unstarted", "position": 5},
        {"name": "Canceled", "type": "canceled", "position": 1},
        {"name": "Duplicate", "type": "duplicate", "position": 2},
        {"name": "Building", "type": "started", "position": 999},  # duplicate name ignored
    ]
    ordered = li.order_workflow_states(states)
    assert [s["name"] for s in ordered] == [
        "Backlog", "Ready", "Building", "Review PR", "Done", "Canceled", "Duplicate",
    ]


@pytest.mark.unit
def test_extract_linked_pr_numbers_from_attachments_and_description():
    attachments = [
        {"url": "https://github.com/yyeret/mini-crm/pull/195"},
        {"url": "https://github.com/other/repo/pull/999"},
    ]
    description = "Linked PR: https://github.com/YYERET/mini-crm/pull/196 and duplicate https://github.com/yyeret/mini-crm/pull/195"
    assert li.extract_linked_pr_numbers(attachments, description, "yyeret/mini-crm") == [195, 196]
    assert li.extract_linked_pr_numbers(attachments, description, None) == []


@pytest.mark.unit
def test_collect_linear_issues_paginates_and_normalizes(monkeypatch):
    calls = []

    async def fake_request_json(url, *, method="GET", headers=None, body=None, timeout=None):
        assert url == li.LINEAR_API_URL
        assert method == "POST"
        assert headers["Authorization"] == "lin_api_test"
        calls.append(body["variables"])
        if "teams(" in body["query"]:
            return 200, {}, {
                "data": {
                    "teams": {
                        "nodes": [
                            {
                                "id": "wrong-org-team",
                                "key": "CRM",
                                "name": "Other",
                                "organization": {"urlKey": "other-org", "name": "Other"},
                                "states": {"nodes": []},
                            },
                            {
                                "id": "team-1",
                                "key": "CRM",
                                "name": "mini-crm",
                                "organization": {"urlKey": "yeret-agility", "name": "Yeret Agility"},
                                "states": {
                                    "nodes": [
                                        {"id": "s1", "name": "Backlog", "type": "backlog", "position": 0},
                                        {"id": "s2", "name": "Building", "type": "started", "position": 1},
                                        {"id": "s3", "name": "Done", "type": "completed", "position": 2},
                                    ]
                                },
                            },
                        ]
                    }
                }
            }
        after = body["variables"].get("after")
        if after is None:
            return 200, {}, {
                "data": {
                    "team": {
                        "issues": {
                            "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                            "nodes": [
                                {
                                    "id": "i1",
                                    "identifier": "CRM-1",
                                    "number": 1,
                                    "title": "Build connector",
                                    "description": "See https://github.com/yyeret/mini-crm/pull/42",
                                    "createdAt": "2026-11-01T00:00:00Z",
                                    "updatedAt": "2026-11-10T00:00:00Z",
                                    "completedAt": "2026-11-10T00:00:00Z",
                                    "url": "https://linear.app/yeret-agility/issue/CRM-1",
                                    "state": {"name": "Done", "type": "completed", "position": 2},
                                    "labels": {"nodes": [{"name": "backend"}]},
                                    "attachments": {"nodes": []},
                                    "history": {
                                        "pageInfo": {"hasNextPage": False},
                                        "nodes": [
                                            {"createdAt": "2026-11-05T00:00:00Z", "fromState": {"name": "Backlog", "type": "backlog"}, "toState": {"name": "Building", "type": "started"}},
                                            {"createdAt": "2026-11-10T00:00:00Z", "fromState": {"name": "Building", "type": "started"}, "toState": {"name": "Done", "type": "completed"}},
                                        ],
                                    },
                                }
                            ],
                        }
                    }
                }
            }
        return 200, {}, {
            "data": {
                "team": {
                    "issues": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [
                            {
                                "id": "i2",
                                "identifier": "CRM-2",
                                "number": 2,
                                "title": "Open work",
                                "description": "",
                                "createdAt": "2026-11-02T00:00:00Z",
                                "updatedAt": "2026-11-03T00:00:00Z",
                                "url": "https://linear.app/yeret-agility/issue/CRM-2",
                                "state": {"name": "Building", "type": "started", "position": 1},
                                "labels": {"nodes": []},
                                "attachments": {"nodes": []},
                                "history": {"pageInfo": {"hasNextPage": False}, "nodes": []},
                            },
                            {
                                "id": "i3",
                                "identifier": "CRM-3",
                                "number": 3,
                                "title": "Ancient closed",
                                "description": "",
                                "createdAt": "2024-01-01T00:00:00Z",
                                "updatedAt": "2024-02-01T00:00:00Z",
                                "completedAt": "2024-02-01T00:00:00Z",
                                "state": {"name": "Done", "type": "completed", "position": 2},
                                "labels": {"nodes": []},
                                "attachments": {"nodes": []},
                                "history": {"pageInfo": {"hasNextPage": False}, "nodes": []},
                            },
                        ],
                    }
                }
            }
        }

    monkeypatch.setattr(github_api, "request_json", fake_request_json)
    now = datetime.datetime(2026, 11, 20, 12, 0, 0)
    raw = asyncio.run(
        li.collect_linear_issues(
            "https://linear.app/yeret-agility/team/CRM/overview",
            "lin_api_test",
            now=now,
            repo="yyeret/mini-crm",
        )
    )
    assert raw["source"] == "linear"
    assert raw["linear_team"]["key"] == "CRM"
    assert raw["linear_team"]["org"] == "yeret-agility"
    assert len(raw["open"]) == 1 and raw["open"][0]["identifier"] == "CRM-2"
    assert len(raw["closed"]) == 1 and raw["closed"][0]["identifier"] == "CRM-1"
    assert raw["closed"][0]["linkedPullRequests"] == [42]
    assert raw["open_truncated"] is False
    assert len(calls) == 3


@pytest.mark.unit
@pytest.mark.parametrize(
    ("status", "payload", "expected_code"),
    [
        (401, {}, 401),
        (429, {}, 429),
        (500, {}, 502),
        (200, {"errors": [{"message": "Authentication required"}]}, 401),
        (200, {"errors": [{"message": "Query syntax error"}]}, 502),
        (200, {"data": {"teams": {"nodes": []}}}, 404),
    ],
)
def test_collect_linear_issues_error_mapping(monkeypatch, status, payload, expected_code):
    async def fake_request_json(*args, **kwargs):
        return status, {}, payload

    monkeypatch.setattr(github_api, "request_json", fake_request_json)
    with pytest.raises(li.LinearError) as exc:
        asyncio.run(li.collect_linear_issues("CRM", "lin_api_test"))
    assert exc.value.status_code == expected_code


@pytest.mark.unit
def test_collect_linear_issues_missing_key_and_transport_error(monkeypatch):
    with pytest.raises(li.LinearError) as exc:
        asyncio.run(li.collect_linear_issues("CRM", ""))
    assert exc.value.status_code == 400

    async def boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(github_api, "request_json", boom)
    with pytest.raises(li.LinearError) as exc:
        asyncio.run(li.collect_linear_issues("CRM", "lin_api_test"))
    assert exc.value.status_code == 502


@pytest.mark.unit
def test_linear_lifecycle_cfd_weekly_daily_and_same_day():
    # 1. Multi-week + stale + PR resolution
    now = datetime.datetime(2026, 11, 20, 12, 0, 0)
    raw = {
        "source": "linear",
        "linear_team": {"key": "CRM", "name": "mini-crm", "org": "yeret-agility"},
        "linear_states": [
            {"name": "Backlog", "type": "backlog"},
            {"name": "Building", "type": "started"},
            {"name": "Review PR", "type": "started"},
            {"name": "Done", "type": "completed"},
        ],
        "open": [
            {
                "number": 1,
                "identifier": "CRM-1",
                "title": "Stale item",
                "createdAt": "2026-07-01T00:00:00Z",
                "updatedAt": "2026-07-10T00:00:00Z",
                "state": "OPEN",
                "linearState": "Backlog",
                "url": "https://linear.app/yeret-agility/issue/CRM-1",
                "linearHistory": {"hasNextPage": False, "transitions": []},
            },
            {
                "number": 2,
                "identifier": "CRM-2",
                "title": "Truncated history",
                "createdAt": "2026-11-01T00:00:00Z",
                "updatedAt": "2026-11-19T00:00:00Z",
                "state": "OPEN",
                "linearState": "Review PR",
                "linearHistory": {"hasNextPage": True, "transitions": []},
            },
        ],
        "closed": [
            {
                "number": 3,
                "identifier": "CRM-3",
                "title": "Finished item",
                "createdAt": "2026-11-01T00:00:00Z",
                "updatedAt": "2026-11-15T00:00:00Z",
                "closedAt": "2026-11-15T00:00:00Z",
                "state": "CLOSED",
                "linearState": "Done",
                "closedByPullRequestsReferences": {"totalCount": 1, "nodes": [{"number": 10}]},
                "linearHistory": {
                    "hasNextPage": False,
                    "transitions": [
                        {"createdAt": "2026-11-05T00:00:00Z", "fromState": "Backlog", "toState": "Building"},
                        {"createdAt": "2026-11-15T00:00:00Z", "fromState": "Building", "toState": "Done"},
                    ],
                },
            }
        ],
        "pre_ai": [],
    }
    metrics = issues_mod.compute_issue_metrics(raw, issues_mod.issue_weekly_bins(now), now, repo="yyeret/mini-crm")
    assert metrics["source"] == "linear"
    assert metrics["linear_team"]["key"] == "CRM"
    assert metrics["stale"]["count"] == 1
    assert metrics["stale"]["items"][0]["identifier"] == "CRM-1"
    assert metrics["resolution"] is None
    cfd = metrics["lifecycle_cfd"]
    assert cfd["source"] == "linear"
    assert cfd["profile"] == "CRM · mini-crm"
    assert cfd["stages"] == ["Backlog", "Building", "Done"]
    assert cfd["coverage"]["timeline_truncated"] == 1
    assert cfd["points"][-1]["counts"] == {"Backlog": 1, "Building": 0, "Done": 1}

    # 2. Same-day newly created Linear team (e.g. CRM created hours ago)
    same_day_now = datetime.datetime(2026, 11, 22, 6, 0, 0)
    same_day_raw = {
        "source": "linear",
        "linear_team": {"key": "CRM", "name": "CRM"},
        "linear_states": [
            {"name": "Backlog", "type": "backlog"},
            {"name": "Review PR", "type": "started"},
            {"name": "Done", "type": "completed"},
        ],
        "open": [
            {
                "number": 24,
                "identifier": "CRM-24",
                "title": "Imported PR review",
                "createdAt": "2026-11-22T03:33:59Z",
                "updatedAt": "2026-11-22T03:33:59Z",
                "state": "OPEN",
                "linearState": "Review PR",
                "linearHistory": {"hasNextPage": False, "transitions": []},
            }
        ],
        "closed": [
            {
                "number": 25,
                "identifier": "CRM-25",
                "title": "Deploy failure",
                "createdAt": "2026-11-22T03:48:29Z",
                "updatedAt": "2026-11-22T03:51:04Z",
                "closedAt": "2026-11-22T03:51:04Z",
                "state": "CLOSED",
                "linearState": "Done",
                "linearHistory": {
                    "hasNextPage": False,
                    "transitions": [
                        {"createdAt": "2026-11-22T03:51:04Z", "fromState": "Backlog", "toState": "Done"},
                    ],
                },
            }
        ],
        "pre_ai": [],
    }
    same_day_metrics = issues_mod.compute_issue_metrics(
        same_day_raw, issues_mod.issue_weekly_bins(same_day_now), same_day_now, repo="yyeret/mini-crm"
    )
    same_cfd = same_day_metrics["lifecycle_cfd"]
    assert same_cfd is not None
    assert same_cfd["profile"] == "CRM"
    assert len(same_cfd["points"]) >= 2
    assert same_cfd["points"][-1]["counts"]["Review PR"] == 1
    assert same_cfd["points"][-1]["counts"]["Done"] == 1

    # 3. Multi-day (<14 days) daily cadence + reopened transition + edge cases
    daily_now = datetime.datetime(2026, 11, 10, 12, 0, 0)
    daily_raw = {
        "source": "linear",
        "linear_team": {"key": "CRM", "name": "mini-crm"},
        "linear_states": [
            {"name": "Backlog", "type": "backlog"},
            {"name": "Building", "type": "started"},
            {"name": "Done", "type": "completed"},
        ],
        "open": [
            {
                "number": 10,
                "identifier": "CRM-10",
                "title": "Reopened item",
                "createdAt": "2026-11-05T00:00:00Z",
                "updatedAt": "2026-11-09T00:00:00Z",
                "state": "OPEN",
                "linearState": "Building",
                "linearHistory": {
                    "hasNextPage": False,
                    "transitions": [
                        {"createdAt": "2026-11-06T00:00:00Z", "fromState": "Backlog", "toState": "Done"},
                        {"createdAt": "2026-11-08T00:00:00Z", "fromState": "Done", "toState": "Building"},
                    ],
                },
            },
            {
                "number": 11,
                "identifier": "CRM-11",
                "title": "Future created ignored",
                "createdAt": "2026-11-15T00:00:00Z",
                "updatedAt": "2026-11-15T00:00:00Z",
                "state": "OPEN",
                "linearState": "Backlog",
                "linearHistory": {"hasNextPage": False, "transitions": []},
            },
        ],
        "closed": [],
        "pre_ai": [],
    }
    daily_metrics = issues_mod.compute_issue_metrics(
        daily_raw, issues_mod.issue_weekly_bins(daily_now), daily_now, repo="yyeret/mini-crm"
    )
    daily_cfd = daily_metrics["lifecycle_cfd"]
    assert daily_cfd is not None
    assert daily_cfd["stages"] == ["Backlog", "Building", "Done"]
    assert daily_cfd["points"][-1]["counts"] == {"Backlog": 0, "Building": 1, "Done": 0}


@pytest.mark.unit
def test_build_state_events_handles_reopen_and_sync_wrapper(monkeypatch):
    transitions, timeline = li._build_state_events(
        [
            {"createdAt": "2026-11-05T00:00:00Z", "fromState": {"name": "Building", "type": "started"}, "toState": {"name": "Done", "type": "completed"}},
            {"createdAt": "2026-11-06T00:00:00Z", "fromState": {"name": "Done", "type": "completed"}, "toState": {"name": "Building", "type": "started"}},
            "not-a-dict",
        ],
        has_next_page=False,
    )
    assert len(transitions) == 2
    assert [e["__typename"] for e in timeline] == ["ClosedEvent", "ReopenedEvent"]

    async def fake_collect(team_ref, api_key, now=None, *, repo=None):
        return {"source": "linear", "team_ref": team_ref, "repo": repo}

    monkeypatch.setattr(li, "collect_linear_issues", fake_collect)
    assert li.collect_linear_issues_sync("CRM", "key", repo="o/r") == {"source": "linear", "team_ref": "CRM", "repo": "o/r"}


@pytest.mark.unit
def test_linear_cfd_returns_none_when_empty_or_subminute_and_rest_skips_issues(monkeypatch):
    import analyze

    now = datetime.datetime(2026, 11, 22, 6, 0, 30)
    assert issues_mod._linear_lifecycle_cfd([], {"source": "linear"}, now - datetime.timedelta(days=7), now) is None
    too_short = {
        "source": "linear",
        "linear_team": {"key": "CRM"},
        "linear_states": [{"name": "Backlog", "type": "backlog"}],
        "open": [
            {
                "number": 1,
                "identifier": "CRM-1",
                "title": "Just created",
                "createdAt": "2026-11-22T06:00:10Z",
                "updatedAt": "2026-11-22T06:00:10Z",
                "state": "OPEN",
                "linearState": "Backlog",
                "linearHistory": {"hasNextPage": False, "transitions": []},
            }
        ],
        "closed": [],
        "pre_ai": [],
    }
    assert issues_mod._linear_lifecycle_cfd(too_short["open"], too_short, now - datetime.timedelta(days=7), now) is None

    async def fake_prs(repo, headers):
        return {"recent_prs": [], "open_prs": []}

    async def fail_issues(*args, **kwargs):
        raise AssertionError("collect_rest_issues should not be called when include_issues=False")

    monkeypatch.setattr(analyze, "_collect_rest_pull_requests", fake_prs)
    monkeypatch.setattr(analyze, "collect_rest_issues", fail_issues)
    res = asyncio.run(analyze.collect_public_data("o/r", include_issues=False))
    assert res["issues"] is None
    assert res["recent_prs"] == []


@pytest.mark.unit
def test_detect_and_discover_linear_team_from_github(monkeypatch):
    # Weighted linear-linkback comment outweighs a stray mention in a PR body
    detected = li.detect_linear_team_from_texts(
        ["Mentioned https://linear.app/other-org/issue/OTH-1 once"],
        weighted_texts=[
            "<!-- linear-linkback -->\n<a href=\"https://linear.app/magnitude-by-commit/issue/MAG-285/slug\">MAG-285</a>"
        ],
    )
    assert detected == {
        "org": "magnitude-by-commit",
        "team_key": "MAG",
        "url": "https://linear.app/magnitude-by-commit/team/MAG/overview",
        "mentions": 1,
    }
    assert li.detect_linear_team_from_texts(["no linear links here"]) is None

    async def fake_comments(url, *, headers=None, timeout=None, **kwargs):
        assert "/repos/commit-magnitude/magnitude-prototype/issues/comments" in url
        return 200, {}, [
            {
                "user": {"login": "linear-code[bot]"},
                "body": "<!-- linear-linkback --><a href=\"https://linear.app/magnitude-by-commit/issue/MAG-285\">MAG-285</a>",
            },
            {"user": {"login": "alice"}, "body": "See https://linear.app/magnitude-by-commit/issue/MAG-284"},
            "ignored",
        ]

    monkeypatch.setattr(github_api, "request_json", fake_comments)
    found = asyncio.run(
        li.discover_linear_team_from_github(
            "commit-magnitude/magnitude-prototype",
            "gh-token",
            pr_bodies=["Owning Linear issue: https://linear.app/magnitude-by-commit/issue/MAG-223"],
        )
    )
    assert found["org"] == "magnitude-by-commit"
    assert found["team_key"] == "MAG"
    assert found["mentions"] == 3

    # Fail-soft on GitHub comment fetch error while still using PR bodies
    async def boom(*args, **kwargs):
        raise RuntimeError("network")

    monkeypatch.setattr(github_api, "request_json", boom)
    fallback = asyncio.run(
        li.discover_linear_team_from_github(
            "commit-magnitude/magnitude-prototype",
            pr_bodies=["https://linear.app/magnitude-by-commit/issue/MAG-223"],
        )
    )
    assert fallback["team_key"] == "MAG"

    # Local gh CLI sync discovery
    import subprocess
    import json as _json

    def fake_gh(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=_json.dumps([
                {"user": {"login": "linear[bot]"}, "body": "<!-- linear-linkback --> https://linear.app/magnitude-by-commit/issue/MAG-10"}
            ]),
        )

    sync_found = li.discover_linear_team_from_github_sync("commit-magnitude/magnitude-prototype", run=fake_gh)
    assert sync_found["team_key"] == "MAG"


@pytest.mark.unit
def test_work_item_aging_uses_active_start_and_cumulative_stage_exit_percentiles():
    now = datetime.datetime(2026, 11, 23, 0, 0)
    weekly_bins = [now - datetime.timedelta(weeks=w) for w in range(8, -1, -1)]
    linear_states = [
        {"name": "Backlog", "type": "backlog", "position": 0},
        {"name": "Next", "type": "unstarted", "position": 1},
        {"name": "Specify", "type": "started", "position": 2},
        {"name": "Spec Review", "type": "started", "position": 3},
        {"name": "Build", "type": "started", "position": 4},
        {"name": "Verify", "type": "started", "position": 5},
        {"name": "Done", "type": "completed", "position": 6},
        {"name": "Canceled", "type": "canceled", "position": 7},
    ]

    # Item A: 5d in Backlog, enters Specify on Nov 1 (active day 0), Spec Review on Nov 11 (day 10),
    # finishes Spec Review -> Build on Nov 12 (day 11), Done on Nov 15 (day 14).
    item_a = {
        "number": 1,
        "identifier": "MAG-1",
        "title": "Item A",
        "createdAt": "2026-10-27T00:00:00Z",
        "startedAt": "2026-11-01T00:00:00Z",
        "updatedAt": "2026-11-15T00:00:00Z",
        "closedAt": "2026-11-15T00:00:00Z",
        "state": "CLOSED",
        "linearState": "Done",
        "linearStateType": "completed",
        "linearHistory": {
            "hasNextPage": False,
            "transitions": [
                {"createdAt": "2026-11-01T00:00:00Z", "fromState": "Backlog", "toState": "Specify", "fromType": "backlog", "toType": "started"},
                {"createdAt": "2026-11-11T00:00:00Z", "fromState": "Specify", "toState": "Spec Review", "fromType": "started", "toType": "started"},
                {"createdAt": "2026-11-12T00:00:00Z", "fromState": "Spec Review", "toState": "Build", "fromType": "started", "toType": "started"},
                {"createdAt": "2026-11-15T00:00:00Z", "fromState": "Build", "toState": "Done", "fromType": "started", "toType": "completed"},
            ],
        },
    }

    # Item B: enters Specify on Nov 10 (active day 0), Spec Review on Nov 13 (day 3),
    # finishes Spec Review -> Build on Nov 15 (day 5), still open in Build (active age 13d on Nov 23).
    item_b = {
        "number": 2,
        "identifier": "MAG-2",
        "title": "Item B",
        "url": "https://linear.app/magnitude/issue/MAG-2/item-b",
        "createdAt": "2026-11-01T00:00:00Z",
        "startedAt": "2026-11-10T00:00:00Z",
        "updatedAt": "2026-11-15T00:00:00Z",
        "closedAt": None,
        "state": "OPEN",
        "linearState": "Build",
        "linearStateType": "started",
        "linearHistory": {
            "hasNextPage": False,
            "transitions": [
                {"createdAt": "2026-11-10T00:00:00Z", "fromState": "Backlog", "toState": "Specify", "fromType": "backlog", "toType": "started"},
                {"createdAt": "2026-11-13T00:00:00Z", "fromState": "Specify", "toState": "Spec Review", "fromType": "started", "toType": "started"},
                {"createdAt": "2026-11-15T00:00:00Z", "fromState": "Spec Review", "toState": "Build", "fromType": "started", "toType": "started"},
            ],
        },
    }

    # Item C: still in Backlog — must be excluded from active aging chart
    item_c = {
        "number": 3,
        "identifier": "MAG-3",
        "title": "Backlog item",
        "createdAt": "2026-10-01T00:00:00Z",
        "updatedAt": "2026-10-01T00:00:00Z",
        "closedAt": None,
        "state": "OPEN",
        "linearState": "Backlog",
        "linearStateType": "backlog",
        "linearHistory": {"hasNextPage": False, "transitions": []},
    }

    # Item D: canceled out of Spec Review — must not count as finishing Spec Review
    item_d = {
        "number": 4,
        "identifier": "MAG-4",
        "title": "Canceled item",
        "createdAt": "2026-11-10T00:00:00Z",
        "startedAt": "2026-11-10T00:00:00Z",
        "updatedAt": "2026-11-11T00:00:00Z",
        "closedAt": "2026-11-11T00:00:00Z",
        "state": "CLOSED",
        "linearState": "Canceled",
        "linearStateType": "canceled",
        "linearHistory": {
            "hasNextPage": False,
            "transitions": [
                {"createdAt": "2026-11-10T12:00:00Z", "fromState": "Specify", "toState": "Spec Review", "fromType": "started", "toType": "started"},
                {"createdAt": "2026-11-11T00:00:00Z", "fromState": "Spec Review", "toState": "Canceled", "fromType": "started", "toType": "canceled"},
            ],
        },
    }

    metrics = issues_mod.compute_issue_metrics(
        {
            "source": "linear",
            "linear_states": linear_states,
            "open": [item_b, item_c],
            "closed": [item_a, item_d],
            "pre_ai": [],
        },
        weekly_bins,
        now,
    )
    aging = metrics["work_item_aging"]
    assert aging["start_basis"] == "active_work"
    assert aging["stages"] == ["Specify", "Spec Review", "Build", "Verify"]
    # Only active open item MAG-2 is plotted, with age from active start (Nov 10 -> Nov 23 = 13d), not createdAt (22d)
    assert len(aging["items"]) == 1
    assert aging["items"][0]["identifier"] == "MAG-2"
    assert aging["items"][0]["age_days"] == 13.0
    assert aging["items"][0]["url"] == "https://linear.app/magnitude/issue/MAG-2/item-b"

    by_stage = {sp["stage"]: sp for sp in aging["stage_percentiles"]}
    # Spec Review exits: Item A = 11.0d (from active start, NOT 1d in stage), Item B = 5.0d; Item D canceled excluded
    assert by_stage["Spec Review"]["n"] == 2
    assert by_stage["Spec Review"]["p50_days"] == 11.0  # upper-index on [5.0, 11.0]
    assert by_stage["Spec Review"]["p85_days"] == 11.0
    # Build exits: only Item A finished Build -> Done at 14.0d
    assert by_stage["Build"] == {"stage": "Build", "n": 1, "p50_days": 14.0, "p85_days": 14.0}
    # Verify has no exits yet
    assert by_stage["Verify"] == {"stage": "Verify", "n": 0, "p50_days": None, "p85_days": None}




