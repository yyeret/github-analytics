"""Verified caller GitHub credentials and server-side OAuth sessions.

No deployment credential is consulted. OAuth sessions are local grants: logout
removes this application's session without revoking the caller's GitHub token.
"""
import asyncio
import base64
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from github_api import request_json

GITHUB_SESSION_COOKIE = 'gha_github_session'
STATE_COOKIE = 'gha_github_oauth_state'
SESSION_SECONDS = 8 * 60 * 60
STATE_SECONDS = 600
router = APIRouter()


@dataclass(frozen=True)
class GitHubIdentity:
    github_id: int
    login: str
    token: str = field(repr=False)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _environment(request: Request):
    return request.scope.get('env')


def _database(request: Request):
    database = getattr(_environment(request), 'USERS_DB', None)
    if database is None:
        raise HTTPException(503, 'GitHub sign-in storage is not configured.')
    return database


async def _provider(url, *, headers, method='GET', body=None):
    try:
        return await asyncio.wait_for(request_json(url, headers=headers, method=method, body=body, timeout=10), timeout=10)
    except Exception:
        # Do not include upstream error text, which may contain credentials.
        raise HTTPException(503, 'GitHub authentication is temporarily unavailable.') from None


async def identify_token(token: str) -> GitHubIdentity:
    if not isinstance(token, str) or not token or len(token) > 4096 or any(char.isspace() for char in token):
        raise HTTPException(401, 'A valid GitHub credential is required.')
    status, _, payload = await _provider('https://api.github.com/user', headers={
        'Accept': 'application/vnd.github+json', 'Authorization': f'Bearer {token}',
        'User-Agent': 'github-flow-analytics', 'X-GitHub-Api-Version': '2022-11-28',
    })
    if status == 401:
        raise HTTPException(401, 'Your GitHub credential expired or was revoked. Sign in again.')
    if status != 200 or not isinstance(payload, dict):
        raise HTTPException(503, 'GitHub authentication is temporarily unavailable.')
    github_id, login = payload.get('id'), payload.get('login')
    if type(github_id) is not int or github_id <= 0 or not isinstance(login, str) or not login or len(login) > 100:
        raise HTTPException(503, 'GitHub identity could not be verified.')
    return GitHubIdentity(github_id, login, token)


async def delete_session(request: Request) -> None:
    session = request.cookies.get(GITHUB_SESSION_COOKIE)
    if session:
        await _database(request).prepare('DELETE FROM github_sessions WHERE token_hash = ?').bind(token_hash(session)).run()


async def cleanup_expired_credentials(database) -> None:
    """Erase expired tokens and PKCE verifiers even when nobody signs in again."""
    now = int(time.time())
    await database.prepare('DELETE FROM github_sessions WHERE expires_at <= ?').bind(now).run()
    await database.prepare('DELETE FROM github_oauth_states WHERE expires_at <= ?').bind(now).run()


async def authenticate(request: Request, optional: bool = False) -> GitHubIdentity | None:
    authorization = request.headers.get('authorization')
    if authorization is not None:
        scheme, _, token = authorization.partition(' ')
        if scheme.lower() != 'bearer':
            raise HTTPException(401, 'Use a GitHub bearer credential.')
        return await identify_token(token)
    session = request.cookies.get(GITHUB_SESSION_COOKIE)
    if not session:
        if optional:
            return None
        raise HTTPException(401, 'Connect your GitHub identity to continue.')
    row = await _database(request).prepare(
        'SELECT github_id, login, access_token FROM github_sessions WHERE token_hash = ? AND expires_at > ?'
    ).bind(token_hash(session), int(time.time())).first()
    if not row:
        raise HTTPException(401, 'Your GitHub session expired. Sign in again.')
    try:
        identity = await identify_token(row['access_token'])
        if identity.github_id != row['github_id']:
            raise HTTPException(401, 'Your GitHub session could not be verified. Sign in again.')
    except HTTPException as exc:
        if exc.status_code == 401:
            await delete_session(request)
        raise
    return identity


def _configuration(request: Request):
    env = _environment(request)
    client_id = getattr(env, 'GITHUB_OAUTH_CLIENT_ID', '')
    client_secret = getattr(env, 'GITHUB_OAUTH_CLIENT_SECRET', '')
    redirect_uri = getattr(env, 'GITHUB_OAUTH_REDIRECT_URI', '')
    parsed = urlsplit(redirect_uri)
    if (not client_id or not client_secret or parsed.scheme != 'https' or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path != '/api/auth/github/callback'):
        raise HTTPException(503, 'GitHub OAuth is not configured.')
    return client_id, client_secret, redirect_uri, getattr(env, 'GITHUB_OAUTH_SCOPE', 'repo')


@router.get('/api/auth/github/start')
async def start(request: Request):
    client_id, _, redirect_uri, scope = _configuration(request)
    limiter = getattr(_environment(request), 'ANALYZE_LIMIT', None)
    if limiter is None:
        raise HTTPException(503, 'GitHub sign-in rate limiting is not configured.')
    allowed = await limiter.limit({'key': 'github-oauth-start:' + request.headers.get('CF-Connecting-IP', 'unknown')})
    if not allowed.get('success', False):
        raise HTTPException(429, 'Too many sign-in attempts. Please try again shortly.')
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    now = int(time.time())
    db = _database(request)
    await db.prepare('DELETE FROM github_oauth_states WHERE expires_at <= ?').bind(now).run()
    await db.prepare('INSERT INTO github_oauth_states (state_hash, verifier, redirect_uri, expires_at) VALUES (?, ?, ?, ?)').bind(
        token_hash(state), verifier, redirect_uri, now + STATE_SECONDS).run()
    response = RedirectResponse('https://github.com/login/oauth/authorize?' + urlencode({
        'client_id': client_id, 'redirect_uri': redirect_uri, 'scope': scope,
        'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256',
    }), status_code=302)
    response.set_cookie(STATE_COOKIE, state, max_age=STATE_SECONDS, httponly=True, secure=True, samesite='lax', path='/')
    return response


@router.get('/api/auth/github/callback')
async def callback(request: Request):
    client_id, client_secret, redirect_uri, _ = _configuration(request)
    state, code = request.query_params.get('state', ''), request.query_params.get('code', '')
    cookie = request.cookies.get(STATE_COOKIE, '')
    if not state or len(state) > 128 or not cookie or not secrets.compare_digest(state, cookie) or not code or len(code) > 1024:
        raise HTTPException(400, 'GitHub sign-in could not be verified. Start again.')
    now = int(time.time())
    db = _database(request)
    row = await db.prepare('DELETE FROM github_oauth_states WHERE state_hash = ? RETURNING verifier, redirect_uri, expires_at').bind(
        token_hash(state)).first()
    if not row or row['expires_at'] <= now or row['redirect_uri'] != redirect_uri:
        raise HTTPException(400, 'GitHub sign-in expired or was already used. Start again.')
    status, _, payload = await _provider('https://github.com/login/oauth/access_token', method='POST',
        headers={'Accept': 'application/json', 'User-Agent': 'github-flow-analytics'}, body={
            'client_id': client_id, 'client_secret': client_secret, 'code': code,
            'redirect_uri': redirect_uri, 'code_verifier': row['verifier'],
        })
    if status != 200 or not isinstance(payload, dict) or not isinstance(payload.get('access_token'), str):
        raise HTTPException(400, 'GitHub did not authorize sign-in. Start again.')
    identity = await identify_token(payload['access_token'])
    duration = SESSION_SECONDS
    if 'expires_in' in payload:
        provider_expiry = payload['expires_in']
        if type(provider_expiry) is not int or provider_expiry <= 0:
            raise HTTPException(400, 'GitHub returned an expired sign-in. Start again.')
        duration = min(duration, provider_expiry)
    session = secrets.token_urlsafe(32)
    await delete_session(request)
    await db.prepare('DELETE FROM github_sessions WHERE expires_at <= ?').bind(now).run()
    await db.prepare('INSERT INTO github_sessions (token_hash, github_id, login, access_token, expires_at) VALUES (?, ?, ?, ?, ?)').bind(
        token_hash(session), identity.github_id, identity.login, identity.token, now + duration).run()
    response = RedirectResponse('/', status_code=302)
    response.set_cookie(GITHUB_SESSION_COOKIE, session, max_age=duration, httponly=True, secure=True, samesite='lax', path='/')
    response.delete_cookie(STATE_COOKIE, path='/', secure=True, samesite='lax')
    return response


@router.post('/api/auth/github/logout')
async def logout(request: Request):
    _, _, redirect_uri, _ = _configuration(request)
    parsed = urlsplit(redirect_uri)
    if request.headers.get('origin') != f'{parsed.scheme}://{parsed.netloc}':
        raise HTTPException(403, 'Request origin could not be verified.')
    await delete_session(request)
    response = JSONResponse({'signed_in': False})
    response.delete_cookie(GITHUB_SESSION_COOKIE, path='/', secure=True, samesite='lax')
    return response
