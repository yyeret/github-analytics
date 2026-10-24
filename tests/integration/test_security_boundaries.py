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
