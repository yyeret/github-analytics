"""Tests for listing the signed-in GitHub user's most active private repositories."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import cloudflare_app as hosted
import github_auth
from tests.unit.test_github_auth import Database


def hosted_client(env):
    async def wrapped(scope, receive, send):
        scope["env"] = env
        await hosted.app(scope, receive, send)
    return TestClient(wrapped)


def test_list_private_repos_requires_github_identity(monkeypatch):
    env = SimpleNamespace(GITHUB_TOKEN="deployment-token-must-not-be-used")
    called = False

    async def fail_request(*args, **kwargs):
        nonlocal called
        called = True
        return 200, {}, []

    monkeypatch.setattr(hosted, "request_json", fail_request)
    response = hosted_client(env).get("/api/github/repos")
    assert response.status_code == 401
    assert not called


def test_list_private_repos_returns_active_private_only(monkeypatch):
    env = SimpleNamespace(USERS_DB=Database())

    async def fake_auth(request, optional=False):
        return github_auth.GitHubIdentity(github_id=42, login="yyeret", token="caller-gh-token")

    captured = []

    async def fake_request_json(url, *, headers=None, **kwargs):
        captured.append((url, headers))
        return 200, {}, [
            {
                "full_name": "yyeret/active-app",
                "private": True,
                "archived": False,
                "disabled": False,
                "description": "  Main private app  ",
                "pushed_at": "2026-11-20T10:00:00Z",
                "language": "Python",
                "open_issues_count": 5,
            },
            {
                "full_name": "yyeret/archived-app",
                "private": True,
                "archived": True,
                "disabled": False,
                "pushed_at": "2026-11-19T10:00:00Z",
            },
            {
                "full_name": "yyeret/public-app",
                "private": False,
                "archived": False,
                "disabled": False,
                "pushed_at": "2026-11-19T09:00:00Z",
            },
            {
                "full_name": "org/second-private",
                "private": True,
                "archived": False,
                "disabled": False,
                "description": None,
                "pushed_at": "2026-11-18T08:00:00Z",
                "language": "TypeScript",
                "open_issues_count": 2,
            },
            {
                "full_name": "../bad/slug",
                "private": True,
                "archived": False,
                "disabled": False,
            },
        ]

    monkeypatch.setattr(github_auth, "authenticate", fake_auth)
    monkeypatch.setattr(hosted, "request_json", fake_request_json)

    response = hosted_client(env).get("/api/github/repos")
    assert response.status_code == 200
    assert response.headers.get("cache-control") == "no-store"
    data = response.json()
    assert data["login"] == "yyeret"
    assert data["repos"] == [
        {
            "full_name": "yyeret/active-app",
            "description": "Main private app",
            "pushed_at": "2026-11-20T10:00:00Z",
            "language": "Python",
            "open_issues_count": 5,
        },
        {
            "full_name": "org/second-private",
            "description": None,
            "pushed_at": "2026-11-18T08:00:00Z",
            "language": "TypeScript",
            "open_issues_count": 2,
        },
    ]
    url, headers = captured[0]
    assert "visibility=private" in url
    assert "sort=pushed" in url
    assert headers["Authorization"] == "Bearer caller-gh-token"


def test_list_private_repos_respects_limit_and_handles_upstream_errors(monkeypatch):
    env = SimpleNamespace(USERS_DB=Database())

    async def fake_auth(request, optional=False):
        return github_auth.GitHubIdentity(github_id=42, login="yyeret", token="caller-gh-token")

    monkeypatch.setattr(github_auth, "authenticate", fake_auth)

    async def fake_many(url, **kwargs):
        return 200, {}, [
            {"full_name": f"yyeret/repo-{i}", "private": True, "pushed_at": f"2026-11-{20 - i:02d}T00:00:00Z"}
            for i in range(5)
        ]

    monkeypatch.setattr(hosted, "request_json", fake_many)
    response = hosted_client(env).get("/api/github/repos?limit=2")
    assert response.status_code == 200
    assert [r["full_name"] for r in response.json()["repos"]] == ["yyeret/repo-0", "yyeret/repo-1"]

    assert hosted_client(env).get("/api/github/repos?limit=0").status_code == 422
    assert hosted_client(env).get("/api/github/repos?limit=31").status_code == 422

    async def fake_rate_limit(url, **kwargs):
        return 403, {}, {"message": "API rate limit exceeded"}

    monkeypatch.setattr(hosted, "request_json", fake_rate_limit)
    assert hosted_client(env).get("/api/github/repos").status_code == 429

    async def fake_upstream_fail(url, **kwargs):
        return 500, {}, {"message": "internal"}

    monkeypatch.setattr(hosted, "request_json", fake_upstream_fail)
    assert hosted_client(env).get("/api/github/repos").status_code == 502


@pytest.mark.parametrize("status,expected", [(401, 401)])
def test_list_private_repos_clears_session_on_revoked_token(monkeypatch, status, expected):
    env = SimpleNamespace(USERS_DB=Database())
    deleted = []

    async def fake_auth(request, optional=False):
        return github_auth.GitHubIdentity(github_id=42, login="yyeret", token="revoked-token")

    async def fake_delete(request):
        deleted.append(True)

    async def fake_revoked(url, **kwargs):
        return status, {}, {"message": "Bad credentials"}

    monkeypatch.setattr(github_auth, "authenticate", fake_auth)
    monkeypatch.setattr(github_auth, "delete_session", fake_delete)
    monkeypatch.setattr(hosted, "request_json", fake_revoked)

    response = hosted_client(env).get("/api/github/repos")
    assert response.status_code == expected
    assert deleted == [True]
    assert "Bad credentials" not in response.text
