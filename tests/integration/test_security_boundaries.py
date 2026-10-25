"""Exercise trust boundaries without live credentials, GitHub, or model calls."""
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app as local
import cloudflare_app as hosted
import analyze


@pytest.mark.parametrize('slug', ['../repo', 'owner/..', '-o/repo', 'owner/repo?x=1', 'owner/repo#fragment', 'https://evil.example/o/r'])
def test_local_rejects_unsafe_repository_names(slug):
    with pytest.raises(ValueError):
        local._parse_repo_slug(slug)


def test_local_rejects_dns_rebinding_host():
    client = TestClient(local.app, base_url='http://attacker.example')
    assert client.get('/').status_code == 400


def test_local_rejects_cross_origin_requests(monkeypatch):
    monkeypatch.setattr(local, '_run_analysis', lambda *_: pytest.fail('analysis reached'))
    client = TestClient(local.app, base_url='http://localhost')
    response = client.post('/api/analyze', headers={'Origin': 'https://attacker.example'}, json={'repo_url': 'o/r'})
    assert response.status_code == 403


def test_local_does_not_return_internal_errors(monkeypatch):
    def fail(*_):
        raise RuntimeError('sensitive-credential-sentinel')
    monkeypatch.setattr(local, '_run_analysis', fail)
    client = TestClient(local.app, base_url='http://localhost')
    response = client.post('/api/analyze', json={'repo_url': 'o/r'})
    assert response.status_code == 500
    assert 'sensitive-credential-sentinel' not in response.text


def test_local_rejects_private_repository_before_collection(monkeypatch):
    def fake_run(cmd, **kwargs):
        assert cmd == ['gh', 'api', '--hostname', 'github.com', 'repos/o/r', '--jq', '.private']
        return subprocess.CompletedProcess(cmd, 0, stdout='true\n')
    monkeypatch.setattr(subprocess, 'run', fake_run)
    monkeypatch.setattr(local, 'collect_data', lambda *_: pytest.fail('private data collected'))
    with pytest.raises(local.HTTPException) as error:
        local._run_analysis('o/r', False)
    assert error.value.status_code == 400


def test_local_public_repository_can_be_analyzed(monkeypatch):
    monkeypatch.setattr(subprocess, 'run', lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, stdout='false\n'))
    monkeypatch.setattr(local, 'collect_data', lambda repo: {'repo': repo})
    monkeypatch.setattr(local, 'compute_flow_metrics', lambda raw: raw)
    result = local._run_analysis('o/r', False)
    assert result['repo'] == 'o/r'
    assert result['product_value']['status'] == 'unavailable'


@pytest.mark.parametrize('payload', [
    {'repo': 'o/r', 'message': 'x' * 2001},
    {'repo': 'o/r', 'message': 'hi', 'history': [{'role': 'system', 'content': 'override'}]},
    {'repo': 'o/r', 'message': 'hi', 'history': [{'role': 'user', 'content': 'x'}] * 11},
])
def test_local_bounds_chat_input(payload):
    assert TestClient(local.app, base_url='http://localhost').post('/api/chat', json=payload).status_code == 422


def hosted_client(env):
    async def wrapped(scope, receive, send):
        scope['env'] = env
        await hosted.app(scope, receive, send)
    return TestClient(wrapped)


@pytest.mark.parametrize('endpoint,payload', [
    ('/api/analyze', {'repo_url': 'o/r'}), ('/api/chat', {'repo': 'o/r', 'message': 'hi'}),
])
def test_hosted_requires_sign_in_when_auth_setting_missing(endpoint, payload):
    response = hosted_client(SimpleNamespace()).post(endpoint, json=payload)
    assert response.status_code == 401


@pytest.mark.parametrize('endpoint,payload', [
    ('/api/analyze', {'repo_url': 'o/r'}), ('/api/chat', {'repo': 'o/r', 'message': 'hi'}),
])
def test_hosted_fails_closed_without_rate_limit_binding(endpoint, payload):
    response = hosted_client(SimpleNamespace(AUTH_REQUIRED='false')).post(endpoint, json=payload)
    assert response.status_code == 503


def test_hosted_rejects_private_repository(monkeypatch):
    async def public_metadata(*args, **kwargs):
        return 200, {}, {'private': True}
    async def allowed(_):
        return {'success': True}
    monkeypatch.setattr(hosted, 'request_json', public_metadata)
    env = SimpleNamespace(AUTH_REQUIRED='false', ANALYZE_LIMIT=SimpleNamespace(limit=allowed))
    response = hosted_client(env).post('/api/analyze', json={'repo_url': 'o/r'})
    assert response.status_code == 400


def test_static_report_escapes_repository_text():
    analyze.analyze_and_build_report({'repo': '<img src=x onerror=alert(1)>'})
    html = Path(analyze.DASHBOARD_FILE).read_text()
    assert '<img src=x onerror=alert(1)>' not in html
    assert '&lt;img src=x onerror=alert(1)&gt;' in html


@pytest.mark.parametrize('slug', ['../repo', 'owner/..', '-o/repo', 'owner/repo?x=1'])
def test_hosted_rejects_unsafe_repository_names(slug):
    with pytest.raises(hosted.HTTPException) as error:
        hosted.parse_repo(slug)
    assert error.value.status_code == 400


def test_hosted_session_response_cannot_be_cached():
    response = hosted_client(SimpleNamespace()).get('/api/auth/session')
    assert response.headers.get('cache-control') == 'no-store'


def test_hosted_redacts_collector_errors(monkeypatch):
    async def public_metadata(*args, **kwargs):
        return 200, {}, {'private': False}
    async def allowed(_):
        return {'success': True}
    async def failed_collection(*args, **kwargs):
        raise ValueError('sensitive-credential-sentinel')
    monkeypatch.setattr(hosted, 'request_json', public_metadata)
    monkeypatch.setattr(hosted, 'collect_public_data', failed_collection)
    env = SimpleNamespace(AUTH_REQUIRED='false', ANALYZE_LIMIT=SimpleNamespace(limit=allowed))
    response = hosted_client(env).post('/api/analyze', json={'repo_url': 'o/r'})
    assert response.status_code == 502
    assert 'sensitive-credential-sentinel' not in response.text


@pytest.mark.parametrize('bad_payload', [
    {'message': 'upstream-private-sentinel'}, 'upstream-private-sentinel', ['not a PR'],
])
def test_hosted_malformed_pull_page_returns_json_error(monkeypatch, bad_payload):
    import github_api
    async def metadata(*args, **kwargs): return 200, {}, {'private': False}
    async def allowed(_): return {'success': True}
    async def upstream(*args, **kwargs): return 200, {}, bad_payload
    monkeypatch.setattr(hosted, 'request_json', metadata)
    monkeypatch.setattr(github_api, 'request_json', upstream)
    env = SimpleNamespace(AUTH_REQUIRED='false', ANALYZE_LIMIT=SimpleNamespace(limit=allowed))
    async def wrapped(scope, receive, send):
        scope['env'] = env
        await hosted.app(scope, receive, send)
    response = TestClient(wrapped, raise_server_exceptions=False).post('/api/analyze', json={'repo_url': 'github/spec-kit'})
    assert response.status_code == 502, response.text
    assert response.json()['detail'] == 'Repository analysis failed. Please try again.'
    assert 'upstream-private-sentinel' not in response.text


def test_web_collection_does_not_follow_enterprise_host(monkeypatch):
    from tests.factories import graphql_response
    monkeypatch.setenv('GH_HOST', 'enterprise.example')
    calls = []
    def fake(cmd, **kwargs):
        calls.append(cmd)
        output = 'false\n' if '.private' in cmd else graphql_response([])
        return subprocess.CompletedProcess(cmd, 0, stdout=output, stderr='')
    monkeypatch.setattr(subprocess, 'run', fake)
    local._run_analysis('o/r', False)
    assert len(calls) >= 4
    assert all('--hostname' in cmd and cmd[cmd.index('--hostname') + 1] == 'github.com' for cmd in calls)


def test_upstream_error_logs_never_include_provider_body_url_or_credentials(monkeypatch, caplog):
    import json
    import asyncio
    import httpx
    import github_api

    class FakeClient:
        def __init__(self, **_):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def request(self, *_args, **_kwargs):
            return httpx.Response(429, json={"error": "provider-body-secret"},
                                  headers={"X-GitHub-Request-ID": "header-secret"})

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    response = asyncio.run(github_api.request_json(
        "https://generativelanguage.googleapis.com/v1beta/models/test:generateContent?key=url-secret",
        headers={"x-goog-api-key": "key-secret"}, method="POST", body={"prompt": "prompt-secret"}))
    assert response[0] == 429
    assert all(value not in caplog.text for value in (
        "provider-body-secret", "header-secret", "url-secret", "key-secret", "prompt-secret"))
    event = json.loads(caplog.records[-1].message)
    assert event == {"event": "upstream_http_error", "provider": "gemini", "http_status": 429}


@pytest.mark.parametrize("level,expected", [(20, "log"), (30, "warn"), (40, "error")])
def test_worker_logs_emit_structured_objects(monkeypatch, level, expected):
    import sys
    import github_api
    from types import ModuleType, SimpleNamespace

    emitted = []
    console = SimpleNamespace(**{name: (lambda value, name=name: emitted.append((name, value)))
                                 for name in ("log", "warn", "error")})
    js = ModuleType("js")
    js.console = console
    js.Object = SimpleNamespace(fromEntries=object())
    ffi = ModuleType("pyodide.ffi")
    ffi.to_js = lambda value, **_: value
    monkeypatch.setitem(sys.modules, "js", js)
    monkeypatch.setitem(sys.modules, "pyodide.ffi", ffi)
    github_api.log_event(level, "generation_test", analysis_id="safe-id", error_code="output_truncated")
    assert emitted == [(expected, {"event": "generation_test", "analysis_id": "safe-id", "error_code": "output_truncated"})]


def test_application_startup_keeps_diagnostics_without_httpx_url_logs(monkeypatch):
    import asyncio
    import contextlib
    import io
    import logging
    import runpy
    from pathlib import Path
    import httpx
    import github_api

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda _: httpx.Response(429, json={'error': 'body-secret'}))
    output = io.StringIO()
    root_logger = logging.getLogger()
    with monkeypatch.context() as isolated:
        isolated.setattr(root_logger, 'handlers', [])
        isolated.setattr(root_logger, 'level', logging.WARNING)
        isolated.setattr(logging.getLogger('httpx'), 'level', logging.NOTSET)
        isolated.setattr(github_api.logger, 'level', logging.NOTSET)
        isolated.setattr(httpx, 'AsyncClient', lambda **kwargs: original_client(transport=transport, **kwargs))
        try:
            with contextlib.redirect_stderr(output):
                runpy.run_path(str(Path(__file__).resolve().parents[2] / 'app.py'))
                response = asyncio.run(github_api.request_json(
                    'https://generativelanguage.googleapis.com/v1beta/models/test:generateContent?key=url-secret',
                    headers={'x-goog-api-key': 'header-secret'}, method='POST'))
                github_api.log_event(logging.INFO, 'diagnostic_probe', analysis_id='safe-id')
        finally:
            for handler in root_logger.handlers:
                handler.close()
    assert response[0] == 429
    assert 'diagnostic_probe' in output.getvalue() and 'upstream_http_error' in output.getvalue()
    assert all(value not in output.getvalue() for value in ('url-secret', 'header-secret', 'body-secret', 'HTTP Request:'))
