"""Unit tests for per-user multi-workspace Linear OAuth."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import linear_auth
from tests.unit.test_github_auth import Database


def _make_app(monkeypatch, *, account_key="github:acct-1", configured=True):
    db = Database(check_same_thread=False)
    db.connection.executescript((Path(__file__).parents[2] / "migrations/0004_linear_auth.sql").read_text())
    app = FastAPI()

    async def resolve_key(request):
        return request.headers.get("X-Test-Account", account_key) or None

    linear_auth.install_linear_auth(app, resolve_key)

    async def allow(_):
        return {"success": True}

    env = SimpleNamespace(
        USERS_DB=db,
        ANALYZE_LIMIT=SimpleNamespace(limit=allow),
        LINEAR_OAUTH_CLIENT_ID="lin-client" if configured else "",
        LINEAR_OAUTH_CLIENT_SECRET="lin-secret" if configured else "",
        LINEAR_OAUTH_REDIRECT_URI="https://analytics.test/api/auth/linear/callback" if configured else "",
    )

    async def wrapped(scope, receive, send):
        scope["env"] = env
        await app(scope, receive, send)

    return TestClient(wrapped, base_url="https://analytics.test", follow_redirects=False), db, env


@pytest.mark.unit
def test_linear_oauth_multi_workspace_flow_and_disconnect(monkeypatch):
    client, db, _ = _make_app(monkeypatch)
    workspace = {"slug": "yeret-agility", "name": "Yeret Agility", "token": "lin_oauth_1"}

    async def fake_request_json(url, *, headers=None, method="POST", body=None, form=None, timeout=10):
        if url == linear_auth.LINEAR_TOKEN_URL:
            assert form["grant_type"] == "authorization_code"
            assert form["client_id"] == "lin-client"
            assert form["code_verifier"]
            return 200, {}, {"access_token": workspace["token"], "expires_in": 3600}
        assert url == linear_auth.LINEAR_GRAPHQL_URL
        assert headers["Authorization"] == f"Bearer {workspace['token']}"
        return 200, {}, {"data": {"organization": {"urlKey": workspace["slug"], "name": workspace["name"]}}}

    monkeypatch.setattr(linear_auth, "request_json", fake_request_json)

    # 1. Connect first workspace (yeret-agility) with safe return_to auto-resume path
    start1 = client.get("/api/auth/linear/start?return_to=%2F%3Frepo%3Do%2Fr%26autorun%3D1")
    assert start1.status_code == 302
    loc1 = urlsplit(start1.headers["location"])
    assert loc1.netloc == "linear.app" and loc1.path == "/oauth/authorize"
    state1 = parse_qs(loc1.query)["state"][0]
    assert parse_qs(loc1.query)["code_challenge_method"] == ["S256"]

    cb1 = client.get(f"/api/auth/linear/callback?state={state1}&code=code-1")
    assert cb1.status_code == 302
    assert cb1.headers["location"] == "/?repo=o/r&autorun=1"

    # Replay of same state fails
    client.cookies.set(linear_auth.STATE_COOKIE, state1)
    replay = client.get(f"/api/auth/linear/callback?state={state1}&code=code-1")
    assert replay.status_code == 400
    client.cookies.clear()

    # Single workspace resolves both by exact org and when org is omitted
    tok, org = asyncio.run(linear_auth.resolve_linear_token(db, "github:acct-1", "yeret-agility"))
    assert tok == "Bearer lin_oauth_1" and org == "yeret-agility"
    tok_default, org_default = asyncio.run(linear_auth.resolve_linear_token(db, "github:acct-1", None))
    assert tok_default == "Bearer lin_oauth_1" and org_default == "yeret-agility"

    # 2. Connect second workspace (magnitude-by-commit) under the SAME user
    workspace.update({"slug": "magnitude-by-commit", "name": "Commit Magnitude", "token": "lin_oauth_2"})
    start2 = client.get("/api/auth/linear/start")
    state2 = parse_qs(urlsplit(start2.headers["location"]).query)["state"][0]
    cb2 = client.get(f"/api/auth/linear/callback?state={state2}&code=code-2")
    assert cb2.status_code == 302

    conns = client.get("/api/auth/linear/connections").json()
    assert conns["connected"] is True
    assert [w["org"] for w in conns["workspaces"]] == ["magnitude-by-commit", "yeret-agility"]

    # With 2 workspaces, exact org resolves cleanly while bare None is ambiguous -> (None, None)
    tok2, org2 = asyncio.run(linear_auth.resolve_linear_token(db, "github:acct-1", "magnitude-by-commit"))
    assert tok2 == "Bearer lin_oauth_2" and org2 == "magnitude-by-commit"
    assert asyncio.run(linear_auth.resolve_linear_token(db, "github:acct-1", None)) == (None, None)

    # Another user cannot read acct-1's grants
    assert asyncio.run(linear_auth.resolve_linear_token(db, "github:acct-2", "yeret-agility")) == (None, None)

    # 3. Disconnect one workspace (requires same origin)
    bad_origin = client.post("/api/auth/linear/disconnect", json={"org": "yeret-agility"}, headers={"Origin": "https://evil.test"})
    assert bad_origin.status_code == 403
    ok_disc = client.post(
        "/api/auth/linear/disconnect",
        json={"org": "yeret-agility"},
        headers={"Origin": "https://analytics.test"},
    )
    assert ok_disc.status_code == 200
    assert [w["org"] for w in ok_disc.json()["workspaces"]] == ["magnitude-by-commit"]

    # 4. Cleanup expired credentials
    asyncio.run(linear_auth.cleanup_expired_linear_credentials(db))
    db.connection.close()


@pytest.mark.unit
def test_linear_oauth_requires_signin_and_config(monkeypatch):
    client_no_user, db1, _ = _make_app(monkeypatch, account_key="")
    assert client_no_user.get("/api/auth/linear/start").status_code == 401
    assert client_no_user.get("/api/auth/linear/connections").json() == {"connected": False, "workspaces": []}
    db1.connection.close()

    client_unconf, db2, _ = _make_app(monkeypatch, configured=False)
    assert client_unconf.get("/api/auth/linear/start").status_code == 503
    db2.connection.close()


@pytest.mark.unit
def test_linear_oauth_error_and_edge_paths(monkeypatch):
    client, db, env = _make_app(monkeypatch)

    # Rate limit denial
    async def deny(_):
        return {"success": False}

    env.ANALYZE_LIMIT = SimpleNamespace(limit=deny)
    assert client.get("/api/auth/linear/start").status_code == 429

    # Missing limiter -> 503
    env.ANALYZE_LIMIT = None
    assert client.get("/api/auth/linear/start").status_code == 503

    # Restore limiter
    async def allow(_):
        return {"success": True}

    env.ANALYZE_LIMIT = SimpleNamespace(limit=allow)

    # Bad state / missing cookie -> 400
    assert client.get("/api/auth/linear/callback?state=bad&code=x").status_code == 400

    # Provider failure on token exchange -> 503
    start = client.get("/api/auth/linear/start")
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]

    async def boom(*args, **kwargs):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(linear_auth, "request_json", boom)
    assert client.get(f"/api/auth/linear/callback?state={state}&code=c").status_code == 503

    # Invalid token payload -> 400
    start2 = client.get("/api/auth/linear/start")
    state2 = parse_qs(urlsplit(start2.headers["location"]).query)["state"][0]

    async def bad_token(*args, **kwargs):
        return 400, {}, {"error": "invalid_grant"}

    monkeypatch.setattr(linear_auth, "request_json", bad_token)
    assert client.get(f"/api/auth/linear/callback?state={state2}&code=c").status_code == 400

    # Invalid expires_in -> 400
    start3 = client.get("/api/auth/linear/start")
    state3 = parse_qs(urlsplit(start3.headers["location"]).query)["state"][0]

    async def bad_expiry(url, **kwargs):
        if url == linear_auth.LINEAR_TOKEN_URL:
            return 200, {}, {"access_token": "tok", "expires_in": 0}
        return 200, {}, {"data": {"organization": {"urlKey": "org", "name": "Org"}}}

    monkeypatch.setattr(linear_auth, "request_json", bad_expiry)
    assert client.get(f"/api/auth/linear/callback?state={state3}&code=c").status_code == 400

    # identify_workspace edge cases (401, 503, empty org, invalid token)
    with pytest.raises(linear_auth.HTTPException) as exc:
        asyncio.run(linear_auth.identify_workspace("bad token with space"))
    assert exc.value.status_code == 401

    async def rev(*args, **kwargs):
        return 401, {}, {}

    monkeypatch.setattr(linear_auth, "request_json", rev)
    with pytest.raises(linear_auth.HTTPException) as exc:
        asyncio.run(linear_auth.identify_workspace("valid-format-token"))
    assert exc.value.status_code == 401

    async def empty_org(*args, **kwargs):
        return 200, {}, {"data": {"organization": {"urlKey": ""}}}

    monkeypatch.setattr(linear_auth, "request_json", empty_org)
    with pytest.raises(linear_auth.HTTPException) as exc:
        asyncio.run(linear_auth.identify_workspace("valid-format-token"))
    assert exc.value.status_code == 503

    # Unauthenticated disconnect -> 401
    assert client.post(
        "/api/auth/linear/disconnect",
        json={},
        headers={"Origin": "https://analytics.test", "X-Test-Account": ""},
    ).status_code == 401

    # Delete all grants helper + None database guard + safe return_to / connect_url
    assert linear_auth._safe_return_to("/?repo=o/r&autorun=1") == "/?repo=o/r&autorun=1"
    assert linear_auth._safe_return_to("https://evil.test/phish") == "/"
    assert linear_auth._safe_return_to("//evil.test/phish") == "/"
    assert linear_auth._safe_return_to("/other-path") == "/"
    assert linear_auth._safe_return_to(None) == "/"
    assert linear_auth.connect_url(env, repo="o/r") == (
        "https://analytics.test/api/auth/linear/start?return_to=%2F%3Frepo%3Do%252Fr%26autorun%3D1"
    )
    assert linear_auth.connect_url(env) == "https://analytics.test/api/auth/linear/start"
    assert linear_auth.connect_url(SimpleNamespace()) is None

    asyncio.run(linear_auth.delete_account_grants(db, "github:acct-1"))
    assert asyncio.run(linear_auth.list_connected_workspaces(None, "github:acct-1")) == []
    assert asyncio.run(linear_auth.resolve_linear_token(None, "github:acct-1")) == (None, None)
    asyncio.run(linear_auth.cleanup_expired_linear_credentials(None))
    db.connection.close()

