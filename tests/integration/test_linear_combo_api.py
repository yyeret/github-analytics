"""Integration tests for GitHub + Linear combo analysis (hosted Worker and local FastAPI)."""

import subprocess
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app as local_app
import linear_issues as li
from tests.integration.test_private_issue_api import setup as hosted_setup
from tests.integration.test_security_boundaries import hosted


def _sample_linear_raw():
    return {
        "source": "linear",
        "linear_team": {
            "id": "team-crm",
            "key": "CRM",
            "name": "mini-crm",
            "org": "yeret-agility",
            "url": "https://linear.app/yeret-agility/team/CRM/overview",
        },
        "linear_states": [
            {"name": "Backlog", "type": "backlog"},
            {"name": "Review PR", "type": "started"},
            {"name": "Done", "type": "completed"},
        ],
        "open": [
            {
                "number": 24,
                "identifier": "CRM-24",
                "title": "Expose watchlist coverage",
                "createdAt": "2026-11-01T00:00:00Z",
                "updatedAt": "2026-11-10T00:00:00Z",
                "state": "OPEN",
                "linearState": "Review PR",
                "linearHistory": {
                    "hasNextPage": False,
                    "transitions": [
                        {"createdAt": "2026-11-05T00:00:00Z", "fromState": "Backlog", "toState": "Review PR"}
                    ],
                },
            }
        ],
        "closed": [
            {
                "number": 25,
                "identifier": "CRM-25",
                "title": "Fix deploy",
                "createdAt": "2026-11-02T00:00:00Z",
                "updatedAt": "2026-11-08T00:00:00Z",
                "closedAt": "2026-11-08T00:00:00Z",
                "state": "CLOSED",
                "linearState": "Done",
                "closedByPullRequestsReferences": {"totalCount": 1, "nodes": [{"number": 195}]},
                "linearHistory": {
                    "hasNextPage": False,
                    "transitions": [
                        {"createdAt": "2026-11-08T00:00:00Z", "fromState": "Backlog", "toState": "Done"}
                    ],
                },
            }
        ],
        "pre_ai": [],
        "open_truncated": False,
        "closed_truncated": False,
        "open_available": True,
        "closed_available": True,
        "retry_collection": False,
    }


@pytest.mark.integration
def test_hosted_linear_combo_skips_github_issues_and_isolates_cache(monkeypatch):
    client, _, cache, _ = hosted_setup(monkeypatch)
    seen = {}

    async def fake_collect_public(repo, token=None, *, include_issues=True):
        seen["include_issues"] = include_issues
        seen["token"] = token
        return {"repo": repo, "captured_at": "2026-11-20T12:00:00Z", "recent_prs": [], "open_prs": [], "issues": None}

    async def fake_collect_linear(team_ref, api_key, now=None, *, repo=None):
        seen["team_ref"] = team_ref
        seen["api_key"] = api_key
        seen["repo"] = repo
        return _sample_linear_raw()

    async def fail_project_history(*args, **kwargs):
        raise AssertionError("GitHub Project history should be skipped when Linear is the issue source")

    monkeypatch.setattr(hosted, "collect_public_data", fake_collect_public)
    monkeypatch.setattr(hosted, "collect_linear_issues", fake_collect_linear)
    monkeypatch.setattr(hosted, "collect_project_history", fail_project_history)

    response = client.post(
        "/api/analyze",
        headers={"Authorization": "Bearer caller-a", "X-Linear-Api-Key": "lin_api_secret"},
        json={
            "repo_url": "https://github.com/o/r",
            "allow_private": True,
            "linear_team_url": "https://linear.app/yeret-agility/team/CRM/overview",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert seen["include_issues"] is False
    assert seen["api_key"] == "lin_api_secret"
    assert seen["repo"] == "o/r"
    assert body["linear_team"]["key"] == "CRM"
    assert body["issues"]["source"] == "linear"
    assert body["issues"]["lifecycle_cfd"]["source"] == "linear"
    # Never stored under public cache key
    assert all(":private:1:" in key for key in cache.stored)


@pytest.mark.integration
def test_hosted_linear_combo_validates_url_and_key(monkeypatch):
    client, _, _, _ = hosted_setup(monkeypatch)

    bad_url = client.post(
        "/api/analyze",
        headers={"Authorization": "Bearer caller-a", "X-Linear-Api-Key": "lin_api_secret"},
        json={"repo_url": "o/r", "allow_private": True, "linear_team_url": "https://example.com/not-linear"},
    )
    assert bad_url.status_code == 400

    missing_key = client.post(
        "/api/analyze",
        headers={"Authorization": "Bearer caller-a"},
        json={"repo_url": "o/r", "allow_private": True, "linear_team_url": "CRM"},
    )
    assert missing_key.status_code == 400

    async def fake_collect_public(repo, token=None, *, include_issues=True):
        return {"repo": repo, "captured_at": "2026-11-20T12:00:00Z", "recent_prs": [], "open_prs": [], "issues": None}

    async def fake_linear_401(*args, **kwargs):
        raise li.LinearError("Linear rejected the API key.", status_code=401)

    monkeypatch.setattr(hosted, "collect_public_data", fake_collect_public)
    monkeypatch.setattr(hosted, "collect_linear_issues", fake_linear_401)
    rejected = client.post(
        "/api/analyze",
        headers={"Authorization": "Bearer caller-a", "X-Linear-Api-Key": "bad-key"},
        json={"repo_url": "o/r", "allow_private": True, "linear_team_url": "CRM"},
    )
    assert rejected.status_code == 401


@pytest.mark.integration
def test_local_api_supports_linear_combo_and_private_repo(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="true\n")

    seen = {}

    def fake_collect_data(repo, *, linear_team=None, linear_api_key=None):
        seen["repo"] = repo
        seen["linear_team"] = linear_team
        seen["linear_api_key"] = linear_api_key
        return {
            "repo": repo,
            "captured_at": "2026-11-20T12:00:00Z",
            "pre_ai_prs": [],
            "recent_prs": [],
            "open_prs": [],
            "issues": _sample_linear_raw(),
        }

    async def fake_assess(*args, **kwargs):
        return None

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(local_app, "collect_data", fake_collect_data)
    monkeypatch.setattr(local_app, "assess_product_value", fake_assess)

    client = TestClient(local_app.app, base_url="http://localhost")
    resp = client.post(
        "/api/analyze",
        headers={"X-Linear-Api-Key": "lin_local_key"},
        json={
            "repo_url": "https://github.com/yyeret/mini-crm",
            "allow_private": True,
            "linear_team_url": "https://linear.app/yeret-agility/team/CRM/overview",
        },
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert seen == {
        "repo": "yyeret/mini-crm",
        "linear_team": "https://linear.app/yeret-agility/team/CRM/overview",
        "linear_api_key": "lin_local_key",
    }
    assert payload["linear_team"]["key"] == "CRM"
    assert payload["issues"]["source"] == "linear"
    prompt = local_app._build_issue_block(payload["issues"])
    assert "Linear team: CRM" in prompt


@pytest.mark.integration
def test_hosted_auto_discovers_linear_team_without_url_and_hints_without_key(monkeypatch):
    client, _, cache, _ = hosted_setup(monkeypatch)

    async def fake_collect_public(repo, token=None, *, include_issues=True):
        return {
            "repo": repo,
            "captured_at": "2026-11-23T12:00:00Z",
            "recent_prs": [],
            "open_prs": [
                {
                    "number": 548,
                    "title": "MAG-285",
                    "body": "Owning Linear issue: https://linear.app/magnitude-by-commit/issue/MAG-285",
                    "createdAt": "2026-11-22T00:00:00Z",
                }
            ],
            "issues": {"open": [], "closed": [], "pre_ai": [], "source": "rest"},
        }

    async def fake_discover(repo, token=None, *, pr_bodies=None):
        return {
            "org": "magnitude-by-commit",
            "team_key": "MAG",
            "url": "https://linear.app/magnitude-by-commit/team/MAG/overview",
            "mentions": 2,
        }

    async def fake_collect_linear(team_ref, api_key, now=None, *, repo=None):
        assert team_ref == "https://linear.app/magnitude-by-commit/team/MAG/overview"
        assert api_key == "lin_mag_key"
        return _sample_linear_raw()

    monkeypatch.setattr(hosted, "collect_public_data", fake_collect_public)
    monkeypatch.setattr(hosted, "discover_linear_team_from_github", fake_discover)
    monkeypatch.setattr(hosted, "collect_linear_issues", fake_collect_linear)

    # 1. With X-Linear-Api-Key only (no linear_team_url): auto-discovers and loads Linear issues
    resp = client.post(
        "/api/analyze",
        headers={"Authorization": "Bearer caller-a", "X-Linear-Api-Key": "lin_mag_key"},
        json={"repo_url": "o/r", "allow_private": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["linear_team"]["auto_discovered"] is True
    assert body["issues"]["source"] == "linear"
    assert all(":private:1:" in key for key in cache.stored)

    # 2. Without any Linear key: returns linear_detected hint from PR bodies (zero extra API calls)
    resp_no_key = client.post(
        "/api/analyze",
        headers={"Authorization": "Bearer caller-a"},
        json={"repo_url": "o/r", "allow_private": True},
    )
    assert resp_no_key.status_code == 200, resp_no_key.text
    body_no_key = resp_no_key.json()
    assert body_no_key["linear_detected"]["org"] == "magnitude-by-commit"
    assert body_no_key["linear_detected"]["team_key"] == "MAG"
    assert body_no_key["linear_detected"]["status"] == "key_required"

