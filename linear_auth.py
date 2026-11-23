"""Per-user, multi-workspace Linear OAuth grants stored server-side in D1.

No deployment-wide Linear credential is consulted in the hosted Worker. Each
signed-in user connects one or more Linear workspaces under their own account
key, so both the web dashboard and MCP App read only that user's authorized
workspaces.
"""
import asyncio
import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode, urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from github_api import request_json

STATE_COOKIE = "gha_linear_oauth_state"
STATE_SECONDS = 600
DEFAULT_GRANT_SECONDS = 60 * 60 * 24 * 30  # 30 days max local retention

LINEAR_AUTHORIZE_URL = "https://linear.app/oauth/authorize"
LINEAR_TOKEN_URL = "https://api.linear.app/oauth/token"
LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"
_ORG_QUERY = "{ organization { urlKey name } }"


class DisconnectRequest(BaseModel):
    org: str | None = Field(default=None, max_length=64)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _environment(request: Request):
    return request.scope.get("env")


def _database(request: Request):
    database = getattr(_environment(request), "USERS_DB", None)
    if database is None:
        raise HTTPException(503, "Linear sign-in storage is not configured.")
    return database


def is_configured(env) -> bool:
    if not env:
        return False
    client_id = getattr(env, "LINEAR_OAUTH_CLIENT_ID", "")
    client_secret = getattr(env, "LINEAR_OAUTH_CLIENT_SECRET", "")
    redirect_uri = getattr(env, "LINEAR_OAUTH_REDIRECT_URI", "")
    parsed = urlsplit(redirect_uri)
    return bool(
        client_id
        and client_secret
        and parsed.scheme == "https"
        and parsed.hostname
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
        and parsed.path == "/api/auth/linear/callback"
    )


def _safe_return_to(value: str | None) -> str:
    """Allow only same-origin root paths (e.g. '/?repo=o/r&autorun=1'); never external redirects."""
    if not isinstance(value, str):
        return "/"
    raw = value.strip()
    if not raw or len(raw) > 300 or any(ord(ch) < 32 for ch in raw):
        return "/"
    parsed = urlsplit(raw)
    if parsed.scheme or parsed.netloc or parsed.path != "/" or raw.startswith("//"):
        return "/"
    return f"/?{parsed.query}" if parsed.query else "/"


def connect_url(env, *, repo: str | None = None) -> str | None:
    """Return the absolute /api/auth/linear/start URL when Linear OAuth is configured."""
    if not is_configured(env):
        return None
    redirect_uri = getattr(env, "LINEAR_OAUTH_REDIRECT_URI", "")
    parsed = urlsplit(redirect_uri)
    base = f"{parsed.scheme}://{parsed.netloc}/api/auth/linear/start"
    if repo:
        return_to = "/?" + urlencode({"repo": repo, "autorun": "1"})
        return f"{base}?{urlencode({'return_to': return_to})}"
    return base


def _configuration(request: Request):
    env = _environment(request)
    if not is_configured(env):
        raise HTTPException(503, "Linear OAuth is not configured.")
    return (
        getattr(env, "LINEAR_OAUTH_CLIENT_ID", ""),
        getattr(env, "LINEAR_OAUTH_CLIENT_SECRET", ""),
        getattr(env, "LINEAR_OAUTH_REDIRECT_URI", ""),
        getattr(env, "LINEAR_OAUTH_SCOPE", "read"),
    )


async def _provider(url, *, headers, method="POST", body=None, form=None):
    try:
        return await asyncio.wait_for(
            request_json(url, headers=headers, method=method, body=body, form=form, timeout=10),
            timeout=10,
        )
    except Exception:
        raise HTTPException(503, "Linear authentication is temporarily unavailable.") from None


async def identify_workspace(access_token: str) -> tuple[str, str]:
    """Verify the Linear OAuth token and return (org_slug, org_name)."""
    if not isinstance(access_token, str) or not access_token or len(access_token) > 4096 or any(c.isspace() for c in access_token):
        raise HTTPException(401, "A valid Linear credential is required.")
    status, _, payload = await _provider(
        LINEAR_GRAPHQL_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "User-Agent": "github-flow-analytics",
        },
        body={"query": _ORG_QUERY},
    )
    if status in (401, 403):
        raise HTTPException(401, "Your Linear credential expired or was revoked. Reconnect Linear.")
    if status != 200 or not isinstance(payload, dict) or payload.get("errors"):
        raise HTTPException(503, "Linear workspace could not be verified.")
    org = ((payload.get("data") or {}).get("organization")) or {}
    slug = (org.get("urlKey") or "").strip().lower()
    name = (org.get("name") or slug).strip()
    if not slug or len(slug) > 64:
        raise HTTPException(503, "Linear workspace could not be verified.")
    return slug, name[:120]


async def list_connected_workspaces(database, account_key: str | None) -> list[dict]:
    if not database or not account_key:
        return []
    now = int(time.time())
    try:
        result = await database.prepare(
            "SELECT org_slug, org_name FROM linear_grants WHERE account_key = ? AND expires_at > ? ORDER BY org_slug"
        ).bind(account_key, now).all()
    except Exception:
        return []
    rows = result.get("results") if isinstance(result, dict) else getattr(result, "results", None) or []
    return [{"org": row["org_slug"], "org_name": row["org_name"]} for row in rows if isinstance(row, dict)]


async def resolve_linear_token(database, account_key: str | None, org_slug: str | None = None) -> tuple[str | None, str | None]:
    """Return ('Bearer <token>', matched_org_slug) for the user's connected Linear workspace, or (None, None)."""
    if not database or not account_key:
        return None, None
    now = int(time.time())
    try:
        if org_slug:
            row = await database.prepare(
                "SELECT org_slug, access_token FROM linear_grants WHERE account_key = ? AND org_slug = ? AND expires_at > ?"
            ).bind(account_key, org_slug.lower(), now).first()
            if row and row.get("access_token"):
                return f"Bearer {row['access_token']}", row["org_slug"]
            return None, None
        result = await database.prepare(
            "SELECT org_slug, access_token FROM linear_grants WHERE account_key = ? AND expires_at > ? ORDER BY updated_at DESC"
        ).bind(account_key, now).all()
        rows = result.get("results") if isinstance(result, dict) else getattr(result, "results", None) or []
        if len(rows) == 1 and rows[0].get("access_token"):
            return f"Bearer {rows[0]['access_token']}", rows[0]["org_slug"]
    except Exception:
        return None, None
    return None, None


async def delete_account_grants(database, account_key: str | None, org_slug: str | None = None) -> None:
    if not database or not account_key:
        return
    try:
        if org_slug:
            await database.prepare(
                "DELETE FROM linear_grants WHERE account_key = ? AND org_slug = ?"
            ).bind(account_key, org_slug.lower()).run()
        else:
            await database.prepare("DELETE FROM linear_grants WHERE account_key = ?").bind(account_key).run()
    except Exception:
        pass


async def cleanup_expired_linear_credentials(database) -> None:
    if not database:
        return
    now = int(time.time())
    try:
        await database.prepare("DELETE FROM linear_grants WHERE expires_at <= ?").bind(now).run()
        await database.prepare("DELETE FROM linear_oauth_states WHERE expires_at <= ?").bind(now).run()
    except Exception:
        pass


def install_linear_auth(app, resolve_account_key):
    @app.get("/api/auth/linear/start")
    async def linear_start(request: Request):
        account_key = await resolve_account_key(request)
        if not account_key:
            raise HTTPException(401, "Sign in with GitHub or Google before connecting Linear.")
        client_id, _, redirect_uri, scope = _configuration(request)
        limiter = getattr(_environment(request), "ANALYZE_LIMIT", None)
        if limiter is None:
            raise HTTPException(503, "Linear sign-in rate limiting is not configured.")
        allowed = await limiter.limit({"key": "linear-oauth-start:" + request.headers.get("CF-Connecting-IP", "unknown")})
        if not allowed.get("success", False):
            raise HTTPException(429, "Too many sign-in attempts. Please try again shortly.")
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        return_to = _safe_return_to(request.query_params.get("return_to"))
        now = int(time.time())
        db = _database(request)
        await db.prepare("DELETE FROM linear_oauth_states WHERE expires_at <= ?").bind(now).run()
        await db.prepare(
            "INSERT INTO linear_oauth_states (state_hash, account_key, verifier, redirect_uri, return_to, expires_at) VALUES (?, ?, ?, ?, ?, ?)"
        ).bind(token_hash(state), account_key, verifier, redirect_uri, return_to, now + STATE_SECONDS).run()
        response = RedirectResponse(
            LINEAR_AUTHORIZE_URL + "?" + urlencode({
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": scope,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "prompt": "consent",
            }),
            status_code=302,
        )
        response.set_cookie(STATE_COOKIE, state, max_age=STATE_SECONDS, httponly=True, secure=True, samesite="lax", path="/")
        return response

    @app.get("/api/auth/linear/callback")
    async def linear_callback(request: Request):
        client_id, client_secret, redirect_uri, _ = _configuration(request)
        state, code = request.query_params.get("state", ""), request.query_params.get("code", "")
        cookie = request.cookies.get(STATE_COOKIE, "")
        if not state or len(state) > 128 or not cookie or not secrets.compare_digest(state, cookie) or not code or len(code) > 1024:
            raise HTTPException(400, "Linear sign-in could not be verified. Start again.")
        now = int(time.time())
        db = _database(request)
        row = await db.prepare(
            "DELETE FROM linear_oauth_states WHERE state_hash = ? RETURNING account_key, verifier, redirect_uri, return_to, expires_at"
        ).bind(token_hash(state)).first()
        if not row or row["expires_at"] <= now or row["redirect_uri"] != redirect_uri:
            raise HTTPException(400, "Linear sign-in expired or was already used. Start again.")
        status, _, payload = await _provider(
            LINEAR_TOKEN_URL,
            headers={"Accept": "application/json", "User-Agent": "github-flow-analytics"},
            form={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": row["verifier"],
            },
        )
        if status != 200 or not isinstance(payload, dict) or not isinstance(payload.get("access_token"), str):
            raise HTTPException(400, "Linear did not authorize sign-in. Start again.")
        org_slug, org_name = await identify_workspace(payload["access_token"])
        duration = DEFAULT_GRANT_SECONDS
        if "expires_in" in payload:
            provider_expiry = payload["expires_in"]
            if type(provider_expiry) is not int or provider_expiry <= 0:
                raise HTTPException(400, "Linear returned an expired sign-in. Start again.")
            duration = min(duration, provider_expiry)
        await db.prepare("DELETE FROM linear_grants WHERE expires_at <= ?").bind(now).run()
        await db.prepare(
            "INSERT INTO linear_grants (account_key, org_slug, org_name, access_token, expires_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(account_key, org_slug) DO UPDATE SET org_name = excluded.org_name, "
            "access_token = excluded.access_token, expires_at = excluded.expires_at, updated_at = excluded.updated_at"
        ).bind(row["account_key"], org_slug, org_name, payload["access_token"], now + duration, now).run()
        response = RedirectResponse(_safe_return_to(row.get("return_to")), status_code=302)
        response.delete_cookie(STATE_COOKIE, path="/", secure=True, samesite="lax")
        return response

    @app.get("/api/auth/linear/connections")
    async def linear_connections(request: Request):
        account_key = await resolve_account_key(request)
        if not account_key:
            return {"connected": False, "workspaces": []}
        db = getattr(_environment(request), "USERS_DB", None)
        workspaces = await list_connected_workspaces(db, account_key)
        return {"connected": bool(workspaces), "workspaces": workspaces}

    @app.post("/api/auth/linear/disconnect")
    async def linear_disconnect(req: DisconnectRequest, request: Request):
        _, _, redirect_uri, _ = _configuration(request)
        parsed = urlsplit(redirect_uri)
        if request.headers.get("origin") != f"{parsed.scheme}://{parsed.netloc}":
            raise HTTPException(403, "Request origin could not be verified.")
        account_key = await resolve_account_key(request)
        if not account_key:
            raise HTTPException(401, "Sign in to disconnect Linear.")
        db = _database(request)
        await delete_account_grants(db, account_key, req.org)
        workspaces = await list_connected_workspaces(db, account_key)
        return JSONResponse({"connected": bool(workspaces), "workspaces": workspaces})
