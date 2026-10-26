import json
from types import SimpleNamespace

import pytest

import github_auth
from tests.integration.test_security_boundaries import hosted, hosted_client


def setup(monkeypatch):
    seen = []
    async def identity(request, optional=False):
        token = request.headers.get('authorization', '').removeprefix('Bearer ')
        return github_auth.GitHubIdentity(1 if token == 'caller-a' else 2, 'owner', token) if token else None
    async def metadata(url, **kwargs):
        seen.append(kwargs['headers'].get('Authorization'))
        return 200, {}, {'private': True}
    async def allowed(_): return {'success': True}
    monkeypatch.setattr(github_auth, 'authenticate', identity)
    monkeypatch.setattr(hosted, 'request_json', metadata)
    class Cache:
        def __init__(self): self.stored = {}
        async def get(self, key): return self.stored.get(key)
        async def put(self, key, value, **_): self.stored[key] = value
    cache = Cache()
    env = SimpleNamespace(AUTH_REQUIRED='false', GITHUB_TOKEN='deployment-token', GEMINI_API_KEY='g',
        TYPESAFE_API_KEY='j', ANALYTICS_CACHE=cache, ANALYZE_LIMIT=SimpleNamespace(limit=allowed),
        CHAT_LIMIT=SimpleNamespace(limit=allowed))
    return hosted_client(env), env, cache, seen


def test_private_issue_api_uses_caller_identity_without_pr_metrics(monkeypatch):
    client, _, cache, seen = setup(monkeypatch)
    async def collect(repo, token, **kwargs):
        assert token == 'caller-a'
        return {'items': [{'number': 7, 'title': 'Restore contact form', 'body': 'Preserve inquiries.', 'state': 'closed', 'item_type': 'issue'}],
                'total_count': 1, 'truncated': False}
    async def assess(repo, issues, gemini, token, **kwargs):
        assert kwargs['allow_private'] is True and token == 'caller-a'
        assert kwargs['owner_context'][0]['source_id'] == 'strategy'
        return {'status': 'ok', 'items': [{'number': 7, 'cost_of_delay': 16}], 'ranking': [{'number': 7, 'rank': 1}]}
    monkeypatch.setattr(hosted, 'collect_issue_candidates', collect, raising=False)
    monkeypatch.setattr(hosted, 'assess_issue_value', assess, raising=False)
    monkeypatch.setattr(hosted, 'collect_public_data', lambda *_: pytest.fail('PR pipeline used'))
    response = client.post('/api/issues/value', headers={'Authorization': 'Bearer caller-a'}, json={
        'repo_url': 'o/r', 'allow_private': True, 'owner_context': [{'source_id': 'strategy', 'text': 'Qualified inquiries.'}]})
    assert response.status_code == 200
    assert response.json()['product_value']['ranking'][0]['number'] == 7
    assert response.json()['repository_visibility'] == 'private'
    assert response.json()['selected_issues'][0]['state'] == 'closed'
    assert seen == ['Bearer caller-a'] and not cache.stored
    assert all(secret not in response.text for secret in ['caller-a', 'deployment-token'])


@pytest.mark.parametrize('endpoint', ['/api/analyze', '/api/issues/value'])
def test_private_analysis_requires_explicit_consent_and_caller(monkeypatch, endpoint):
    client, _, _, _ = setup(monkeypatch)
    monkeypatch.setattr(hosted, 'collect_public_data', lambda *_: pytest.fail('private data collected'))
    assert client.post(endpoint, headers={'Authorization': 'Bearer caller-a'}, json={'repo_url': 'o/r'}).status_code == 400
    assert client.post(endpoint, json={'repo_url': 'o/r', 'allow_private': True}).status_code == 401


def test_private_chat_prompt_preserves_visibility():
    prompt = hosted.chat_prompt({'repo': 'o/r', 'repository_visibility': 'private'})
    assert '"repository_visibility":"private"' in prompt
    assert 'public repository metrics' not in prompt


def test_private_pr_cache_is_identity_scoped_and_chat_rechecks_access(monkeypatch):
    client, env, cache, _ = setup(monkeypatch)
    async def collect(repo, token):
        assert token == 'caller-a'
        return {'repo': repo}
    async def assess(repo, raw, key, token, **kwargs):
        assert kwargs['allow_private'] is True
        return {'status': 'ok', 'items': []}
    monkeypatch.setattr(hosted, 'collect_public_data', collect)
    monkeypatch.setattr(hosted, 'compute_flow_metrics', lambda raw: {'repo': 'o/r', 'recent_stats': {'total': 1}})
    monkeypatch.setattr(hosted, 'assess_product_value', assess)
    response = client.post('/api/analyze', headers={'Authorization': 'Bearer caller-a'}, json={'repo_url': 'o/r', 'allow_private': True})
    assert response.status_code == 200
    result = response.json()
    assert result['analysis_id'] and result['repository_visibility'] == 'private'
    assert hosted.METRICS_CACHE_PREFIX + 'o/r' not in cache.stored
    assert all('caller-a' not in value and 'deployment-token' not in value for value in cache.stored.values())
    assert all(json.loads(value)['repository_visibility'] == 'private' for value in cache.stored.values())
    payload = {'repo': 'o/r', 'message': 'Next?', 'analysis_id': result['analysis_id']}
    other = hosted_client(env)
    assert other.post('/api/chat', headers={'Authorization': 'Bearer caller-b'}, json=payload).status_code == 409
    async def inaccessible(*_, **__): return 404, {}, {}
    monkeypatch.setattr(hosted, 'request_json', inaccessible)
    assert client.post('/api/chat', headers={'Authorization': 'Bearer caller-a'}, json=payload).status_code == 404


def test_github_caller_key_public_snapshot_remains_owned_by_that_identity(monkeypatch):
    client, _, cache, _ = setup(monkeypatch)
    async def public(*_, **__): return 200, {}, {'private': False}
    async def collect(repo, token): return {'repo': repo}
    async def assess(*_, **__): return {'status': 'ok', 'items': []}
    monkeypatch.setattr(hosted, 'request_json', public)
    monkeypatch.setattr(hosted, 'collect_public_data', collect)
    monkeypatch.setattr(hosted, 'compute_flow_metrics', lambda raw: {'repo': 'o/r', 'recent_stats': {'total': 1}})
    monkeypatch.setattr(hosted, 'assess_product_value', assess)
    response = client.post('/api/analyze', headers={'Authorization': 'Bearer caller-a', 'X-TypeSafe-Api-Key': 'caller-j'}, json={'repo_url': 'o/r'})
    assert response.status_code == 200
    analysis_id = response.json()['analysis_id']
    assert list(cache.stored) == [hosted.private_analysis_key('o/r', github_auth.GitHubIdentity(1, 'owner', 'caller-a'), analysis_id)]


@pytest.mark.parametrize('endpoint', ['/api/analyze', '/api/issues/value'])
def test_public_collection_cannot_read_private_content_after_visibility_change(monkeypatch, endpoint):
    client, _, cache, _ = setup(monkeypatch)
    async def public(*_, **__): return 200, {}, {'private': False}
    async def collect(repo, token, **kwargs):
        assert token is None, 'Public-mode content requests must not carry private access'
        raise ValueError('Repository became private')
    monkeypatch.setattr(hosted, 'request_json', public)
    monkeypatch.setattr(hosted, 'collect_public_data', collect)
    monkeypatch.setattr(hosted, 'collect_issue_candidates', collect)
    monkeypatch.setattr(hosted, 'assess_product_value', lambda *a, **k: pytest.fail('private text scored'))
    monkeypatch.setattr(hosted, 'assess_issue_value', lambda *a, **k: pytest.fail('private text scored'))
    response = client.post(endpoint, headers={'Authorization': 'Bearer caller-a'}, json={'repo_url': 'o/r'})
    assert response.status_code == 502
    assert not cache.stored


def test_public_analysis_allows_private_consent_without_github_identity(monkeypatch):
    client, _, _, _ = setup(monkeypatch)
    async def public(*_, **__): return 200, {}, {'private': False}
    async def collect(*_, **__): return {'items': [], 'total_count': 0, 'truncated': False}
    async def assess(*_, **__): return {'status': 'ok', 'items': []}
    monkeypatch.setattr(hosted, 'request_json', public)
    monkeypatch.setattr(hosted, 'collect_issue_candidates', collect)
    monkeypatch.setattr(hosted, 'assess_issue_value', assess)
    assert client.post('/api/issues/value', json={'repo_url': 'o/r', 'allow_private': True}).status_code == 200


def test_repository_lookup_has_an_outer_deadline(monkeypatch):
    import asyncio
    async def stalled(*_, **__):
        await asyncio.sleep(60)
    actual_wait = asyncio.wait_for
    async def bounded(awaitable, timeout):
        assert timeout == 10
        return await actual_wait(awaitable, .001)
    monkeypatch.setattr(hosted, 'request_json', stalled)
    monkeypatch.setattr(hosted.asyncio, 'wait_for', bounded)
    with pytest.raises(hosted.HTTPException) as error:
        asyncio.run(actual_wait(hosted.public_repo_exists('o/r', None), .05))
    assert error.value.status_code == 502


def test_delete_account_removes_both_connected_identities(monkeypatch):
    client, env, _, _ = setup(monkeypatch)
    deleted = []
    class Statement:
        def __init__(self, sql): self.sql = sql
        def bind(self, *args): return self
        async def run(self): deleted.append(self.sql)
    env.USERS_DB = SimpleNamespace(prepare=Statement)
    async def google(_): return {'google_sub': 'google-user'}
    monkeypatch.setattr(hosted, 'signed_in_user', google)
    client.cookies.set(hosted.SESSION_COOKIE, 'google-session')
    response = client.delete('/api/auth/account', headers={'Authorization': 'Bearer caller-a', 'Origin': 'http://testserver'})
    assert response.status_code == 200
    assert any('DELETE FROM users' in sql for sql in deleted)
    assert any('DELETE FROM github_sessions' in sql for sql in deleted)
