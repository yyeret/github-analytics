"""GitHub caller identity and OAuth boundary checks, with no provider traffic."""
import asyncio
import base64
import hashlib
import functools
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from starlette.requests import Request

import github_auth as auth


def run_async(function):
    @functools.wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return run


class Statement:
    def __init__(self, db, sql):
        self.db, self.sql, self.args = db, sql, ()

    def bind(self, *args):
        self.args = args
        return self

    async def first(self):
        result = self.db.connection.execute(self.sql, self.args).fetchone()
        self.db.connection.commit()
        return dict(result) if result else None

    async def run(self):
        self.db.connection.execute(self.sql, self.args)
        self.db.connection.commit()


class Database:
    def __init__(self):
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript((Path(__file__).resolve().parents[2] / 'migrations/0002_github_auth.sql').read_text())

    def prepare(self, sql):
        return Statement(self, sql)


@pytest.fixture
def env():
    return SimpleNamespace(USERS_DB=Database(), GITHUB_OAUTH_CLIENT_ID='client',
                           GITHUB_OAUTH_CLIENT_SECRET='secret',
                           GITHUB_OAUTH_REDIRECT_URI='https://analytics.test/api/auth/github/callback')


def request(env, headers=None):
    return Request({'type': 'http', 'method': 'GET', 'path': '/', 'scheme': 'https',
                    'server': ('analytics.test', 443), 'headers':
                    [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()], 'env': env})


@pytest.fixture
def provider(monkeypatch):
    calls = []

    async def call(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith('/access_token'):
            return 200, {}, {'access_token': 'provider-token', 'expires_in': 100}
        return 200, {}, {'id': 7, 'login': 'owner'}

    monkeypatch.setattr(auth, 'request_json', call)
    return calls


@run_async
async def test_bearer_identity_and_no_deployment_fallback(env, provider):
    env.GITHUB_TOKEN = 'shared-token'
    assert await auth.authenticate(request(env), optional=True) is None
    identity = await auth.authenticate(request(env, {'Authorization': 'Bearer caller-token'}))
    assert (identity.github_id, identity.login, identity.token) == (7, 'owner', 'caller-token')
    assert 'caller-token' not in repr(identity)
    assert provider[0][1]['headers']['Authorization'] == 'Bearer caller-token'


@run_async
async def test_revoked_bearer_is_rejected_not_optional(monkeypatch, env):
    async def revoked(*args, **kwargs):
        return 401, {}, {'message': 'secret upstream text'}
    monkeypatch.setattr(auth, 'request_json', revoked)
    with pytest.raises(auth.HTTPException) as error:
        await auth.authenticate(request(env, {'Authorization': 'Bearer revoked'}), optional=True)
    assert error.value.status_code == 401
    assert 'secret' not in error.value.detail


@run_async
async def test_oauth_pkce_session_replay_and_logout(env, provider):
    app = FastAPI()
    app.include_router(auth.router)
    @app.middleware('http')
    async def environment(req, next_call):
        req.scope['env'] = env
        return await next_call(req)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='https://analytics.test',
                                 follow_redirects=False) as client:
        start = await client.get('/api/auth/github/start', headers={'Host': 'spoof.test'})
        assert start.status_code == 302
        params = parse_qs(urlsplit(start.headers['location']).query)
        assert params['redirect_uri'] == [env.GITHUB_OAUTH_REDIRECT_URI]
        assert params['scope'] == ['repo']
        assert params['code_challenge_method'] == ['S256']
        state = params['state'][0]
        cookie = start.headers['set-cookie']
        assert 'HttpOnly' in cookie and 'Secure' in cookie and 'SameSite=lax' in cookie
        bad = await client.get('/api/auth/github/callback?state=bad&code=code')
        assert bad.status_code == 400
        callback = await client.get(f'/api/auth/github/callback?state={state}&code=code')
        assert callback.status_code == 302
        assert callback.headers['location'] == '/'
        assert 'provider-token' not in callback.text + str(callback.headers)
        exchange = provider[0][1]['body']
        assert params['code_challenge'] == [base64.urlsafe_b64encode(
            hashlib.sha256(exchange['code_verifier'].encode()).digest()).decode().rstrip('=')]
        assert exchange['redirect_uri'] == env.GITHUB_OAUTH_REDIRECT_URI
        replay = await client.get(f'/api/auth/github/callback?state={state}&code=code')
        assert replay.status_code == 400
        session = client.cookies.get(auth.GITHUB_SESSION_COOKIE)
        identity = await auth.authenticate(request(env, {'Cookie': f'{auth.GITHUB_SESSION_COOKIE}={session}'}))
        assert identity.token == 'provider-token'
        row = env.USERS_DB.connection.execute('SELECT * FROM github_sessions').fetchone()
        assert row['token_hash'] != session
        assert row['expires_at'] <= auth.time.time() + 101
        denied = await client.post('/api/auth/github/logout', headers={'Origin': 'https://evil.test'})
        assert denied.status_code == 403
        logout = await client.post('/api/auth/github/logout', headers={'Origin': 'https://analytics.test'})
        assert logout.status_code == 200
        assert env.USERS_DB.connection.execute('SELECT COUNT(*) FROM github_sessions').fetchone()[0] == 0


@run_async
async def test_expired_cookie_and_revoked_session(env, provider, monkeypatch):
    now = int(auth.time.time())
    db = env.USERS_DB.connection
    db.execute('INSERT INTO github_sessions VALUES (?, ?, ?, ?, ?)',
               (auth.token_hash('expired'), 7, 'owner', 'old', now - 1))
    db.execute('INSERT INTO github_sessions VALUES (?, ?, ?, ?, ?)',
               (auth.token_hash('active'), 7, 'owner', 'revoked', now + 100))
    db.commit()
    for session in ('expired',):
        with pytest.raises(auth.HTTPException) as error:
            await auth.authenticate(request(env, {'Cookie': f'{auth.GITHUB_SESSION_COOKIE}={session}'}))
        assert error.value.status_code == 401
    assert not provider
    async def revoked(*args, **kwargs):
        return 401, {}, {}
    monkeypatch.setattr(auth, 'request_json', revoked)
    with pytest.raises(auth.HTTPException):
        await auth.authenticate(request(env, {'Cookie': f'{auth.GITHUB_SESSION_COOKIE}=active'}))
    assert db.execute('SELECT COUNT(*) FROM github_sessions WHERE token_hash = ?',
                      (auth.token_hash('active'),)).fetchone()[0] == 0


@run_async
async def test_expired_oauth_state_never_exchanges(env, provider):
    state = 'expired-state'
    env.USERS_DB.connection.execute('INSERT INTO github_oauth_states VALUES (?, ?, ?, ?)',
        (auth.token_hash(state), 'verifier', env.GITHUB_OAUTH_REDIRECT_URI, int(auth.time.time()) - 1))
    env.USERS_DB.connection.commit()
    req = request(env, {'Cookie': f'{auth.STATE_COOKIE}={state}'})
    req.scope['query_string'] = f'state={state}&code=code'.encode()
    with pytest.raises(auth.HTTPException) as error:
        await auth.callback(req)
    assert error.value.status_code == 400
    assert not provider
    assert env.USERS_DB.connection.execute('SELECT COUNT(*) FROM github_oauth_states').fetchone()[0] == 0


@run_async
async def test_transport_failure_sanitized_and_consumes_oauth_state(env, monkeypatch):
    state = 'valid-state'
    env.USERS_DB.connection.execute('INSERT INTO github_oauth_states VALUES (?, ?, ?, ?)',
        (auth.token_hash(state), 'verifier', env.GITHUB_OAUTH_REDIRECT_URI, int(auth.time.time()) + 100))
    env.USERS_DB.connection.commit()
    async def failed(*args, **kwargs):
        raise RuntimeError('secret token from provider transport')
    monkeypatch.setattr(auth, 'request_json', failed)
    req = request(env, {'Cookie': f'{auth.STATE_COOKIE}={state}'})
    req.scope['query_string'] = f'state={state}&code=code'.encode()
    with pytest.raises(auth.HTTPException) as error:
        await auth.callback(req)
    assert error.value.status_code == 503
    assert 'secret' not in error.value.detail
    with pytest.raises(auth.HTTPException) as replay:
        await auth.callback(req)
    assert replay.value.status_code == 400


@run_async
async def test_oauth_state_must_belong_to_same_browser(env, provider):
    state = 'other-browser-state'
    env.USERS_DB.connection.execute('INSERT INTO github_oauth_states VALUES (?, ?, ?, ?)',
        (auth.token_hash(state), 'verifier', env.GITHUB_OAUTH_REDIRECT_URI, int(auth.time.time()) + 100))
    env.USERS_DB.connection.commit()
    req = request(env, {'Cookie': f'{auth.STATE_COOKIE}=unrelated'})
    req.scope['query_string'] = f'state={state}&code=code'.encode()
    with pytest.raises(auth.HTTPException) as error:
        await auth.callback(req)
    assert error.value.status_code == 400
    assert not provider
    assert env.USERS_DB.connection.execute('SELECT COUNT(*) FROM github_oauth_states').fetchone()[0] == 1


@run_async
async def test_scheduled_cleanup_removes_expired_secrets_without_new_sign_in(env, monkeypatch):
    monkeypatch.setattr(auth.time, 'time', lambda: 1000)
    db = env.USERS_DB.connection
    for expiry in (999, 1000, 1001):
        db.execute('INSERT INTO github_sessions VALUES (?, ?, ?, ?, ?)',
                   (f'session-{expiry}', 7, 'owner', f'token-{expiry}', expiry))
        db.execute('INSERT INTO github_oauth_states VALUES (?, ?, ?, ?)',
                   (f'state-{expiry}', f'verifier-{expiry}', env.GITHUB_OAUTH_REDIRECT_URI, expiry))
    db.commit()

    await auth.cleanup_expired_credentials(env.USERS_DB)
    await auth.cleanup_expired_credentials(env.USERS_DB)

    assert [row['access_token'] for row in db.execute('SELECT * FROM github_sessions')] == ['token-1001']
    assert [row['verifier'] for row in db.execute('SELECT * FROM github_oauth_states')] == ['verifier-1001']


@run_async
async def test_worker_scheduled_handler_awaits_cleanup_and_preserves_asgi(env, monkeypatch):
    import runpy
    import sys

    class AsgiEntrypoint:
        async def fetch(self, request):
            return request

    monkeypatch.setitem(sys.modules, 'workers', SimpleNamespace(
        asgi=SimpleNamespace(entrypoint=lambda app: AsgiEntrypoint)))
    monkeypatch.setitem(sys.modules, 'cloudflare_app', SimpleNamespace(app=object()))
    module = runpy.run_path(str(Path(__file__).resolve().parents[2] / 'cloudflare_worker.py'))
    calls = []

    async def cleanup(database):
        calls.append(database)

    monkeypatch.setitem(module['Default'].scheduled.__globals__, 'cleanup_expired_credentials', cleanup)
    worker = module['Default']()
    worker.env = env
    await worker.scheduled(SimpleNamespace(), env, SimpleNamespace())
    assert calls == [env.USERS_DB]
    assert await worker.fetch('request') == 'request'
