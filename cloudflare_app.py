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
from product_value import assess_product_value

app = FastAPI(title="GitHub Flow Analytics")
METRICS_CACHE_PREFIX = "metrics:v5:"
SESSION_COOKIE = "gha_session"
NONCE_COOKIE = "gha_nonce"
SESSION_SECONDS = 60 * 60 * 24 * 14


@app.middleware("http")
async def response_security(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


class AnalyzeRequest(BaseModel):
    repo_url: str = Field(max_length=300)
    force_refresh: bool = False


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


async def enforce_limit(request: Request, kind: str, limit: int) -> None:
    env = request.scope.get("env")
    binding_name = "ANALYZE_LIMIT" if kind == "analyze" else "CHAT_LIMIT"
    binding = getattr(env, binding_name, None) if env else None
    if binding is None:
        raise HTTPException(status_code=503, detail="Request rate limiting is not configured.")
    result = await binding.limit({"key": client_ip(request)})
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
    session = request.cookies.get(SESSION_COOKIE)
    if session:
        digest = hashlib.sha256(session.encode()).hexdigest()
        await d1(request).prepare("DELETE FROM sessions WHERE token_hash = ?").bind(digest).run()
    response = Response(content="{}", media_type="application/json")
    response.delete_cookie(SESSION_COOKIE, path="/", secure=request.url.scheme == "https", samesite="lax")
    return response


@app.delete("/api/auth/account")
async def delete_account(request: Request):
    check_same_origin(request)
    user = await signed_in_user(request)
    await d1(request).prepare("DELETE FROM users WHERE google_sub = ?").bind(user["google_sub"]).run()
    response = Response(content="{}", media_type="application/json")
    response.delete_cookie(SESSION_COOKIE, path="/", secure=request.url.scheme == "https", samesite="lax")
    return response


async def public_repo_exists(repo: str, token: str | None) -> None:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "github-flow-analytics/0.1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    status, _, data = await request_json(f"https://api.github.com/repos/{repo}", headers=headers, timeout=10)
    if status == 404:
        raise HTTPException(status_code=404, detail="Repository not found or not public. Only public repositories are supported.")
    if status == 403:
        raise HTTPException(status_code=429,
            detail="GitHub is currently refusing analysis requests from this service. Please try again later.")
    if status < 200 or status >= 300:
        raise HTTPException(status_code=502, detail="GitHub could not be reached. Please try again.")
    if data.get("private"):
        raise HTTPException(status_code=400, detail="Only public repositories are supported.")


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
    if auth_required(request):
        await signed_in_user(request)
    await enforce_limit(request, "analyze", 10)
    repo = parse_repo(req.repo_url)
    env = request.scope.get("env")
    token = getattr(env, "GITHUB_TOKEN", None) if env else None
    await public_repo_exists(repo, token)
    caller_keys = request.headers.get("X-Gemini-Api-Key") or request.headers.get("X-TypeSafe-Api-Key")
    cache = getattr(env, "ANALYTICS_CACHE", None) if env and not caller_keys else None
    cache_key = f"{METRICS_CACHE_PREFIX}{repo.lower()}"
    if cache and not req.force_refresh:
        saved = await cache.get(cache_key)
        if saved:
            return json.loads(saved)
    try:
        raw = await collect_public_data(repo, token)
        metrics = compute_flow_metrics(raw)
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Repository analysis failed. Please try again.") from exc
    if not metrics.get("recent_stats", {}).get("total"):
        raise HTTPException(status_code=422, detail="No merged pull requests were found for this public repository.")
    gemini_key = request.headers.get("X-Gemini-Api-Key") or (getattr(env, "GEMINI_API_KEY", None) if env else None)
    jev_key = request.headers.get("X-TypeSafe-Api-Key") or (getattr(env, "TYPESAFE_API_KEY", None) if env else None)
    metrics["product_value"] = await assess_product_value(repo, raw, gemini_key, token, jev_key=jev_key)
    if caller_keys:
        metrics["analysis_id"] = secrets.token_urlsafe(24)
        if env and request.cookies.get(SESSION_COOKIE):
            cache = getattr(env, "ANALYTICS_CACHE", None)
            if cache:
                cache_key = session_analysis_key(repo, request, metrics["analysis_id"])
    if cache:
        value = metrics["product_value"]
        # Keep provider failures briefly so a later analysis can recover once the service does.
        ttl = 600 if value.get("status") in ("failed", "unavailable") or value.get("failed") else 21600
        await cache.put(cache_key, json.dumps(metrics, separators=(",", ":")), expiration_ttl=ttl)
    return metrics


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
    return ("You are the GitHub Flow Analytics coach. Answer concisely using only the supplied "
            "public repository metrics. Cite specific dates, values, and PR numbers where useful. "
            "Throughput is merged PRs per complete week; the first and current partial weeks are excluded. "
            "The hosted collector does not fetch change counts, review timelines, or commit details. "
            "Do not treat missing batch size, review wait, flow efficiency, or AI-assistance values "
            "as zero or measured facts. Active PR stages are rough estimates, so distinguish a "
            "possible bottleneck from a confirmed one. If the data cannot identify the primary "
            "bottleneck, say so and recommend the next measure to collect.\n"
            + json.dumps(context, separators=(",", ":"), ensure_ascii=False))


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request):
    if auth_required(request):
        await signed_in_user(request)
    await enforce_limit(request, "chat", 20)
    env = request.scope.get("env")
    key = getattr(env, "GEMINI_API_KEY", None) if env else None
    if not key:
        raise HTTPException(status_code=503, detail="AI chat is not configured yet.")
    repo = parse_repo(req.repo)
    cache = getattr(env, "ANALYTICS_CACHE", None) if env else None
    if cache is None:
        raise HTTPException(status_code=503, detail="Analysis cache is not configured yet.")
    cache_key = session_analysis_key(repo, request, req.analysis_id) if req.analysis_id else f"{METRICS_CACHE_PREFIX}{repo.lower()}"
    saved = await cache.get(cache_key)
    if not saved:
        raise HTTPException(status_code=409, detail="This analysis expired. Click Analyze again to refresh the coach.")
    metrics = json.loads(saved)
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
        answer = "".join(part.get("text", "") for part in candidates[0]["content"]["parts"])
        return {"reply": answer}
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="AI service is temporarily unavailable.") from exc


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
