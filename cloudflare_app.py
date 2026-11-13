import asyncio
import hashlib
import json
import re
import secrets
import time
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from analyze import collect_public_data, compute_flow_metrics
from github_api import request_json
from google_auth import verify_id_token
from product_value import assess_product_value, assess_issue_value
import github_auth
from issue_candidates import collect_issue_candidates
from mcp_oauth import delete_mcp_grants, router as mcp_oauth_router
from mcp_analytics import collect_dashboard_data, check_private_dashboard_access
from mcp_server import install_mcp

app = FastAPI(title="GitHub Flow Analytics")
app.include_router(github_auth.router)
app.include_router(mcp_oauth_router)
METRICS_CACHE_PREFIX = "metrics:v7:"
SESSION_COOKIE = "gha_session"
NONCE_COOKIE = "gha_nonce"
SESSION_SECONDS = 60 * 60 * 24 * 14


@app.middleware("http")
async def response_security(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
    if request.url.path.startswith(("/api/", "/mcp", "/.well-known/oauth-")):
        response.headers["Cache-Control"] = "no-store"
    return response


class AnalyzeRequest(BaseModel):
    repo_url: str = Field(max_length=300)
    force_refresh: bool = False
    allow_private: bool = False


class OwnerEvidence(BaseModel):
    source_id: str = Field(min_length=1, max_length=60, pattern=r'^[A-Za-z0-9_-]+$')
    text: str = Field(min_length=1, max_length=4000)
    path: str | None = Field(default=None, max_length=300)
    url: str | None = Field(default=None, max_length=500)


class IssueValueRequest(AnalyzeRequest):
    max_issues: int = Field(default=20, ge=1, le=50)
    issue_numbers: list[int] | None = Field(default=None, min_length=1, max_length=50)
    owner_context: list[OwnerEvidence] = Field(default_factory=list, max_length=8)
    owner_profile: dict[str, Any] | None = Field(default=None, max_length=20)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatRequest(BaseModel):
    repo: str = Field(max_length=200)
    analysis_id: str | None = Field(default=None, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    message: str = Field(max_length=2000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=10)


class GoogleLoginRequest(BaseModel):
    credential: str = Field(max_length=16_000)


def parse_repo(value: str) -> str:
    value = value.strip().rstrip("/")
    match = re.fullmatch(r"(?:https?://github\.com/)?([A-Za-z0-9][A-Za-z0-9-]*)/([A-Za-z0-9_][A-Za-z0-9_.-]*?)(?:\.git)?", value)
    if not match or match.group(2) in (".", ".."):
        raise HTTPException(status_code=400, detail="Enter a GitHub repository as owner/name or a github.com URL.")
    return f"{match.group(1)}/{match.group(2)}"


def client_ip(request: Request) -> str:
    return request.headers.get("CF-Connecting-IP", "unknown")


async def enforce_limit(request: Request, kind: str, limit: int, *, identity=None) -> None:
    env = request.scope.get("env")
    binding_name = "ANALYZE_LIMIT" if kind == "analyze" else "CHAT_LIMIT"
    binding = getattr(env, binding_name, None) if env else None
    if binding is None:
        raise HTTPException(status_code=503, detail="Request rate limiting is not configured.")
    key = f'mcp:github:{identity.github_id}' if identity else client_ip(request)
    result = await binding.limit({"key": key})
    if not result.get("success", False):
        raise HTTPException(status_code=429, detail="Too many requests. Please try again shortly.")


def auth_required(request: Request) -> bool:
    return getattr(environment(request), "AUTH_REQUIRED", "true") != "false"


def environment(request: Request):
    return request.scope.get("env")


def d1(request: Request):
    database = getattr(environment(request), "USERS_DB", None)
    if database is None:
        raise HTTPException(status_code=503, detail="Account sign-in is not configured yet.")
    return database


async def signed_in_user(request: Request):
    session = request.cookies.get(SESSION_COOKIE)
    if not session:
        raise HTTPException(status_code=401, detail="Sign in with Google to continue.")
    digest = hashlib.sha256(session.encode()).hexdigest()
    row = await d1(request).prepare(
        "SELECT users.google_sub, users.email FROM sessions JOIN users USING (google_sub) "
        "WHERE sessions.token_hash = ? AND sessions.expires_at > ?"
    ).bind(digest, int(time.time())).first()
    if not row:
        raise HTTPException(status_code=401, detail="Your session expired. Sign in again.")
    return row


def check_same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    expected = f"{request.url.scheme}://{request.url.netloc}"
    if not origin or origin != expected:
        raise HTTPException(status_code=403, detail="Request origin could not be verified.")


@app.get("/api/auth/config")
async def auth_config(request: Request):
    env = environment(request)
    return {
        "client_id": getattr(env, "GOOGLE_OAUTH_CLIENT_ID", "") if env else "",
        "required": auth_required(request),
        "github_enabled": bool(env and getattr(env, 'GITHUB_OAUTH_CLIENT_ID', '')
            and getattr(env, 'GITHUB_OAUTH_CLIENT_SECRET', '') and getattr(env, 'GITHUB_OAUTH_REDIRECT_URI', '')),
    }


@app.get("/api/auth/nonce")
async def issue_nonce(request: Request):
    nonce = secrets.token_urlsafe(32)
    response = Response(content=json.dumps({"nonce": nonce}), media_type="application/json")
    response.set_cookie(NONCE_COOKIE, nonce, max_age=300, httponly=True,
                        secure=request.url.scheme == "https", samesite="strict", path="/")
    return response


@app.get("/api/auth/session")
async def auth_session(request: Request):
    identity = await caller_github(request)
    if identity:
        return {'signed_in': True, 'provider': 'github', 'login': identity.login}
    try:
        user = await signed_in_user(request)
        return {"signed_in": True, "email": user["email"]}
    except HTTPException as exc:
        if exc.status_code == 401:
            return {"signed_in": False}
        raise


@app.post("/api/auth/google")
async def google_login(req: GoogleLoginRequest, request: Request):
    check_same_origin(request)
    expected_nonce = request.cookies.get(NONCE_COOKIE)
    client_id = getattr(environment(request), "GOOGLE_OAUTH_CLIENT_ID", "") if environment(request) else ""
    if not client_id or not expected_nonce:
        raise HTTPException(status_code=503, detail="Google sign-in is not configured yet.")
    try:
        identity = await verify_id_token(req.credential, client_id, expected_nonce)
    except (ValueError, httpx.HTTPError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=401, detail="Google could not verify this sign-in. Please try again.") from exc

    db = d1(request)
    now = int(time.time())
    await db.prepare(
        "INSERT INTO users (google_sub, email, first_signed_in, last_signed_in) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(google_sub) DO UPDATE SET email = excluded.email, "
        "last_signed_in = excluded.last_signed_in, sign_in_count = users.sign_in_count + 1"
    ).bind(identity["sub"], identity["email"], now, now).run()
    session = secrets.token_urlsafe(32)
    digest = hashlib.sha256(session.encode()).hexdigest()
    expires_at = now + SESSION_SECONDS
    await db.prepare("DELETE FROM sessions WHERE expires_at <= ?").bind(now).run()
    await db.prepare("INSERT INTO sessions (token_hash, google_sub, expires_at) VALUES (?, ?, ?)")\
        .bind(digest, identity["sub"], expires_at).run()

    response = Response(content=json.dumps({"signed_in": True, "email": identity["email"]}),
                        media_type="application/json")
    response.set_cookie(SESSION_COOKIE, session, max_age=SESSION_SECONDS, httponly=True,
                        secure=request.url.scheme == "https", samesite="lax", path="/")
    response.delete_cookie(NONCE_COOKIE, path="/", secure=request.url.scheme == "https", samesite="strict")
    return response


@app.post("/api/auth/logout")
async def logout(request: Request):
    check_same_origin(request)
    await github_auth.delete_session(request)
    session = request.cookies.get(SESSION_COOKIE)
    if session:
        digest = hashlib.sha256(session.encode()).hexdigest()
        await d1(request).prepare("DELETE FROM sessions WHERE token_hash = ?").bind(digest).run()
    response = Response(content="{}", media_type="application/json")
    response.delete_cookie(SESSION_COOKIE, path="/", secure=request.url.scheme == "https", samesite="lax")
    response.delete_cookie(github_auth.GITHUB_SESSION_COOKIE, path='/', secure=True, samesite='lax')
    return response


@app.delete("/api/auth/account")
async def delete_account(request: Request):
    check_same_origin(request)
    identity, user = None, None
    try:
        identity = await caller_github(request)
    except HTTPException as exc:
        if exc.status_code != 401:
            raise
    if request.cookies.get(SESSION_COOKIE):
        try:
            user = await signed_in_user(request)
        except HTTPException as exc:
            if exc.status_code != 401:
                raise
    if not identity and not user:
        raise HTTPException(401, 'Sign in to delete your account.')
    if identity:
        db = d1(request)
        await delete_mcp_grants(db, identity.github_id)
        await db.prepare('DELETE FROM github_sessions WHERE github_id = ?').bind(identity.github_id).run()
    if user:
        await d1(request).prepare("DELETE FROM users WHERE google_sub = ?").bind(user["google_sub"]).run()
    response = Response(content="{}", media_type="application/json")
    response.delete_cookie(SESSION_COOKIE, path="/", secure=request.url.scheme == "https", samesite="lax")
    response.delete_cookie(github_auth.GITHUB_SESSION_COOKIE, path='/', secure=True, samesite='lax')
    return response


async def public_repo_exists(repo: str, token: str | None, *, allow_private=False) -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "github-flow-analytics/0.1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        status, _, data = await asyncio.wait_for(
            request_json(f"https://api.github.com/repos/{repo}", headers=headers, timeout=10), 10)
    except (TimeoutError, httpx.HTTPError) as exc:
        raise HTTPException(502, 'GitHub could not be reached. Please try again.') from exc
    if status == 404:
        raise HTTPException(status_code=404, detail="Repository not found or unavailable to the connected GitHub identity.")
    if status == 403:
        raise HTTPException(status_code=429,
            detail="GitHub is currently refusing analysis requests from this service. Please try again later.")
    if status < 200 or status >= 300:
        raise HTTPException(status_code=502, detail="GitHub could not be reached. Please try again.")
    if not isinstance(data, dict) or type(data.get('private')) is not bool:
        raise HTTPException(status_code=502, detail='GitHub returned unreadable repository access information.')
    if data['private'] and not allow_private:
        raise HTTPException(status_code=400, detail='Private analysis requires a connected GitHub identity and allow_private=true. Repository text is sent to Gemini and Jev.')
    return data


async def caller_github(request):
    if request.headers.get('authorization') is not None or getattr(request, 'cookies', {}).get(github_auth.GITHUB_SESSION_COOKIE):
        try:
            return await github_auth.authenticate(request, optional=True)
        except HTTPException as exc:
            if request.headers.get('authorization') is not None or exc.status_code != 401:
                raise
    return None


async def authorize_analysis(request):
    identity = await caller_github(request)
    if not identity and auth_required(request):
        await signed_in_user(request)
    return identity


async def repository_access(repo, request, identity, allow_private):
    token = identity.token if identity else getattr(environment(request), 'GITHUB_TOKEN', None)
    metadata = await public_repo_exists(repo, token, allow_private=allow_private)
    if metadata['private'] and not identity:
        raise HTTPException(401, 'Connect your GitHub identity or supply its bearer credential for private analysis.')
    # Anonymous content reads cannot cross a later public-to-private transition.
    return token if metadata['private'] else None, metadata['private']


def private_analysis_key(repo, identity, analysis_id, full_dashboard=False):
    digest = hashlib.sha256(repo.lower().encode()).hexdigest()
    namespace = 'dashboard:v1:' if full_dashboard else ''
    return f'{METRICS_CACHE_PREFIX}{namespace}private:{identity.github_id}:{digest}:{analysis_id}'


def session_analysis_key(repo, request, analysis_id):
    session = getattr(request, "cookies", {}).get(SESSION_COOKIE)
    if not session:
        raise HTTPException(status_code=401, detail="Sign in to use this analysis.")
    digest = hashlib.sha256(session.encode()).hexdigest()
    return f"{METRICS_CACHE_PREFIX}{repo.lower()}:session:{digest}:{analysis_id}"


@app.get("/api/health")
async def health():
    return {"ok": True}


@app.post("/api/analyze")
async def analyze_repo(req: AnalyzeRequest, request: Request):
    return await analyze_for_identity(req, request, await authorize_analysis(request))


async def analyze_for_identity(req, request, identity, *, full_dashboard=False):
    await enforce_limit(request, "analyze", 10, identity=identity if full_dashboard else None)
    repo = parse_repo(req.repo_url)
    env = request.scope.get("env")
    if full_dashboard:
        metadata = await public_repo_exists(repo, identity.token, allow_private=req.allow_private)
        private = metadata['private']
        token = identity.token if private else None
        repository_id = metadata.get('id')
        if type(repository_id) is not int or repository_id < 1:
            raise HTTPException(502, 'GitHub returned unreadable repository access information.')
    else:
        token, private = await repository_access(repo, request, identity, req.allow_private)
    gemini_override = None if full_dashboard else request.headers.get("X-Gemini-Api-Key")
    jev_override = None if full_dashboard else request.headers.get("X-TypeSafe-Api-Key")
    caller_keys = gemini_override or jev_override
    cache = getattr(env, "ANALYTICS_CACHE", None) if env and not caller_keys and not private else None
    cache_key = public_analysis_key(repo, full_dashboard)
    if cache and not req.force_refresh:
        saved = await cache.get(cache_key)
        if saved:
            cached = json.loads(saved)
            if not full_dashboard:
                return cached
            if cached.get('repository_id') == repository_id and cached.get('analysis_id'):
                snapshot = await cache.get(public_dashboard_snapshot_key(repo, cached['analysis_id']))
                if snapshot:
                    return json.loads(snapshot)
    try:
        raw = (await collect_dashboard_data(repo, identity.token, private, repository_id)
               if full_dashboard else await collect_public_data(repo, token))
        metrics = compute_flow_metrics(raw)
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Repository analysis failed. Please try again.") from exc
    gemini_key = gemini_override or getattr(env, "GEMINI_API_KEY", None)
    jev_key = jev_override or getattr(env, "TYPESAFE_API_KEY", None)
    metrics['repository_visibility'] = 'private' if private else 'public'
    options = {'allow_private': True} if private else {}
    if full_dashboard:
        metrics['dashboard_source'] = 'github_graphql'
        metrics['repository_id'] = repository_id
        metrics['analysis_id'] = secrets.token_urlsafe(24)
        if not metrics.get('recent_stats', {}).get('total'):
            metrics['p50_sle'] = metrics['p85_sle'] = None
    metrics["product_value"] = (
        await assess_product_value(repo, raw, gemini_key, token, jev_key=jev_key, **options)
        if metrics.get("recent_stats", {}).get("total") or raw.get("open_prs") else None
    )
    if private or (caller_keys and identity):
        if not full_dashboard:
            metrics['analysis_id'] = secrets.token_urlsafe(24)
        cache = getattr(env, 'ANALYTICS_CACHE', None)
        cache_key = private_analysis_key(repo, identity, metrics['analysis_id'], full_dashboard)
    elif caller_keys:
        metrics["analysis_id"] = secrets.token_urlsafe(24)
        if env and request.cookies.get(SESSION_COOKIE):
            cache = getattr(env, "ANALYTICS_CACHE", None)
            if cache:
                cache_key = session_analysis_key(repo, request, metrics["analysis_id"])
    if cache:
        value = metrics["product_value"] or {}
        # Keep provider failures briefly so a later analysis can recover once the service does.
        issue_metrics = metrics.get("issues") or {}
        partial = (("issues" in raw and raw["issues"] is None) or metrics.get("pr_collection_unavailable")
                   or not issue_metrics.get("open_available", True) or not issue_metrics.get("closed_available", True)
                   or value.get("status") in ("failed", "unavailable") or value.get("failed"))
        ttl = 600 if partial else 21600
        payload = json.dumps(metrics, separators=(",", ":"))
        if full_dashboard and not private:
            await cache.put(public_dashboard_snapshot_key(repo, metrics['analysis_id']), payload, expiration_ttl=ttl)
            await cache.put(cache_key, json.dumps({'repository_id': repository_id, 'analysis_id': metrics['analysis_id']}),
                            expiration_ttl=ttl)
        else:
            await cache.put(cache_key, payload, expiration_ttl=ttl)
    return metrics


def public_dashboard_snapshot_key(repo, analysis_id):
    return f'{public_analysis_key(repo, True)}:{analysis_id}'


def public_analysis_key(repo, full_dashboard):
    namespace = 'dashboard:v1:' if full_dashboard else ''
    return f'{METRICS_CACHE_PREFIX}{namespace}{repo.lower()}'


@app.post('/api/issues/value')
async def issue_value(req: IssueValueRequest, request: Request):
    return await issue_value_for_identity(req, request, await authorize_analysis(request))


async def issue_value_for_identity(req, request, identity, *, mcp=False):
    await enforce_limit(request, 'analyze', 10, identity=identity if mcp else None)
    repo = parse_repo(req.repo_url)
    token, private = await repository_access(repo, request, identity, req.allow_private)
    numbers = req.issue_numbers
    if numbers and (len(set(numbers)) != len(numbers) or min(numbers) < 1 or len(numbers) > req.max_issues):
        raise HTTPException(422, 'Choose distinct positive issue numbers within max_issues.')
    if sum(len(source.text) for source in req.owner_context) > 12000:
        raise HTTPException(422, 'Owner context exceeds the 12,000-character evidence budget.')
    try:
        selection = await collect_issue_candidates(repo, token, max_issues=req.max_issues, issue_numbers=numbers)
    except (ValueError, httpx.HTTPError, TimeoutError) as exc:
        raise HTTPException(502, 'Issue selection failed or selected issues are unavailable. Refresh the selection.') from exc
    env = environment(request)
    gemini_key = (None if mcp else request.headers.get('X-Gemini-Api-Key')) or getattr(env, 'GEMINI_API_KEY', None)
    jev_key = (None if mcp else request.headers.get('X-TypeSafe-Api-Key')) or getattr(env, 'TYPESAFE_API_KEY', None)
    value = await assess_issue_value(repo, selection['items'], gemini_key, token, jev_key=jev_key,
        max_issues=req.max_issues, allow_private=private, owner_context=[source.model_dump(exclude_none=True) for source in req.owner_context],
        **({'owner_profile': req.owner_profile} if req.owner_profile is not None else {}))
    return {'repo': repo, 'repository_visibility': 'private' if private else 'public',
            'captured_at': int(time.time()), 'selection': {key: val for key, val in selection.items() if key != 'items'},
            'selected_issues': [{key: item.get(key) for key in ('number', 'state', 'item_type', 'url', 'updated_at', 'milestone')}
                                for item in selection['items']],
            'product_value': value}


def chat_prompt(metrics: dict[str, Any]) -> str:
    stats = metrics.get("recent_stats", {})
    throughput = metrics.get("throughput_data", [])
    active = metrics.get("active_wip_points", [])
    stage_counts: dict[str, int] = {}
    for item in active:
        stage = item.get("x", "Unknown")
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
    context = {
        "repo": metrics.get("repo"),
        "repository_visibility": metrics.get("repository_visibility", "public"),
        "captured_at": metrics.get("captured_at"),
        "recent_merged_prs": stats.get("total"),
        "average_cycle_hours": stats.get("avg_cycle"),
        "p50_cycle_hours": metrics.get("p50_sle"),
        "p85_cycle_hours": metrics.get("p85_sle"),
        "throughput_last_12_complete_weeks": [
            {"week": item.get("week"), "merged_prs": item.get("total")}
            for item in throughput[1:-1][-12:]
        ],
        "active_pr_count": len(active),
        "active_pr_stage_counts": stage_counts,
        "oldest_active_prs": [
            {"number": item.get("num"), "age_days": item.get("y"),
             "stage_estimate": item.get("x"), "title": str(item.get("title", ""))[:120]}
            for item in sorted(active, key=lambda item: item.get("y") or 0, reverse=True)[:12]
        ],
        "recent_pr_examples": [
            {"number": item.get("number"), "cycle_hours": item.get("cycle_time_hours"),
             "title": str(item.get("title", ""))[:120]}
            for item in metrics.get("recent_prs_summary", [])[:12]
        ],
    }
    issues = metrics.get("issues")
    if issues:
        context["issues"] = {
            "open_count": issues.get("open_now") if issues.get("open_available", True) else None,
            "closed_available": issues.get("closed_available", True), "open_truncated": issues.get("open_truncated"),
            "closed_truncated": issues.get("closed_truncated"), "net_backlog_8_weeks": issues.get("net_8w"),
            "stale": {"count": (issues.get("stale") or {}).get("count") if issues.get("open_available", True) else None, "basis": issues.get("stale_basis")},
            "cycle_time": issues.get("cycle_time"), "weekly": issues.get("weekly", [])[-12:],
        }
    if not stats.get("total"):
        for key in ("p50_cycle_hours", "p85_cycle_hours", "average_cycle_hours", "throughput_last_12_complete_weeks"):
            context.pop(key, None)
    source = (
        "Contributor type and active PR stage are estimates based on sampled commits, reviews and events. "
        if metrics.get("dashboard_source") == "github_graphql" else
        "The hosted collector does not fetch change counts, review timelines, or commit details. "
    )
    return ("You are Flowy AI Coach. Answer concisely using only the supplied "
            "repository metrics. Cite specific dates, values, and PR numbers where useful. "
            "Throughput is merged PRs per complete week; the first and current partial weeks are excluded. "
            + source +
            "Do not treat missing batch size, review wait, flow efficiency, or AI-assistance values "
            "as zero or measured facts. Active PR stages are rough estimates, so distinguish a "
            "possible bottleneck from a confirmed one. If the data cannot identify the primary "
            "bottleneck, say so and recommend the next measure to collect.\n"
            + json.dumps(context, separators=(",", ":"), ensure_ascii=False))


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request):
    return await chat_for_identity(req, request, await authorize_analysis(request))


async def chat_for_identity(req, request, identity, *, full_dashboard=False):
    await enforce_limit(request, "chat", 20, identity=identity if full_dashboard else None)
    env = request.scope.get("env")
    key = getattr(env, "GEMINI_API_KEY", None) if env else None
    if not key:
        raise HTTPException(status_code=503, detail="AI chat is not configured yet.")
    repo = parse_repo(req.repo)
    cache = getattr(env, "ANALYTICS_CACHE", None) if env else None
    if cache is None:
        raise HTTPException(status_code=503, detail="Analysis cache is not configured yet.")
    if full_dashboard:
        if not req.analysis_id:
            raise HTTPException(409, 'Open the repository dashboard before asking the coach.')
        saved = await cache.get(public_dashboard_snapshot_key(repo, req.analysis_id))
        if not saved and req.allow_private:
            saved = await cache.get(private_analysis_key(repo, identity, req.analysis_id, True))
    else:
        cache_key = (private_analysis_key(repo, identity, req.analysis_id) if identity and req.analysis_id
            else session_analysis_key(repo, request, req.analysis_id) if req.analysis_id else public_analysis_key(repo, False))
        saved = await cache.get(cache_key)
        if not saved and identity and req.analysis_id and request.cookies.get(SESSION_COOKIE):
            try:
                await signed_in_user(request)
            except HTTPException as exc:
                if exc.status_code != 401:
                    raise
            else:
                candidate = await cache.get(session_analysis_key(repo, request, req.analysis_id))
                if candidate and json.loads(candidate).get('repository_visibility', 'public') == 'public':
                    saved = candidate
    if not saved:
        raise HTTPException(status_code=409, detail="This analysis expired. Click Analyze again to refresh the coach.")
    metrics = json.loads(saved)
    private = metrics.get('repository_visibility') == 'private'
    if full_dashboard:
        if private and not req.allow_private:
            raise HTTPException(403, 'Allow private analysis to coach against this snapshot.')
        metadata = await public_repo_exists(repo, identity.token, allow_private=private)
        if metadata['private'] != private or metadata.get('id') != metrics.get('repository_id'):
            raise HTTPException(403, 'Repository access changed. Analyze it again.')
        if private:
            try:
                await check_private_dashboard_access(repo, identity.token, metadata['id'])
            except (httpx.HTTPError, ValueError, TimeoutError):
                raise HTTPException(403, 'GitHub repository access could not be verified.') from None
    elif private:
        if not identity or not req.analysis_id:
            raise HTTPException(401, 'Connect the GitHub identity that owns this private analysis.')
        await repository_access(repo, request, identity, True)
    if metrics.get("repo", "").lower() != repo.lower():
        raise HTTPException(status_code=409, detail="This analysis expired. Click Analyze again to refresh the coach.")
    prompt = chat_prompt(metrics)
    contents = [{"role": "user" if m.role == "user" else "model", "parts": [{"text": m.content}]}
        for m in req.history if m.role in ("user", "assistant")]
    contents.append({"role": "user", "parts": [{"text": req.message}]})
    body = {"system_instruction": {"parts": [{"text": prompt}]}, "contents": contents,
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 700}}
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.post(
                "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent",
                headers={"x-goog-api-key": key}, json=body)
        if not response.is_success:
            raise HTTPException(status_code=502, detail="AI response failed. Please try again.")
        candidates = response.json().get("candidates", [])
        parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
        answer = "".join(part.get("text", "") for part in parts if isinstance(part.get("text"), str))
        if not answer:
            raise HTTPException(502, "The coach returned no answer. Please try again.")
        return {"reply": answer}
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="AI service is temporarily unavailable.") from exc


install_mcp(app, analyze_for_identity, issue_value_for_identity, chat_for_identity)


@app.api_route("/{path:path}", methods=["GET", "HEAD"])
async def frontend(path: str, request: Request):
    env = request.scope.get("env")
    assets = getattr(env, "ASSETS", None) if env else None
    if assets:
        asset_path = path or "index.html"
        response = await assets.fetch(f"https://assets.local/{asset_path}")
        return Response(content=await response.bytes(), status_code=response.status,
                        headers={"content-type": response.headers.get("content-type", "text/html; charset=utf-8")})
    return Response("Not found", status_code=404)
