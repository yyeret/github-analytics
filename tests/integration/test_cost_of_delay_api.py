"""Caller-funded analysis keeps its coach snapshot isolated by authenticated session."""
from types import SimpleNamespace

import pytest

from tests.integration.test_security_boundaries import hosted, hosted_client


@pytest.mark.parametrize('header,expected', [
    ('X-TypeSafe-Api-Key', ('server-g', 'caller-key')),
    ('X-Gemini-Api-Key', ('caller-key', 'server-j')),
])
@pytest.mark.parametrize('assessment_status,expected_ttl', [('ok', 21600), ('unavailable', 600)])
def test_caller_analysis_can_chat_without_sharing_its_result(monkeypatch, header, expected, assessment_status, expected_ttl):
    seen = []
    async def allowed(_): return {'success': True}
    async def public(*_, **__): return 200, {}, {'private': False}
    async def collect(repo, token): return {'repo': repo}
    async def assess(repo, raw, gemini, token, *, jev_key=None):
        seen.append((gemini, jev_key))
        return {'status': assessment_status, 'items': []}
    class Cache:
        stored = {}
        ttls = []
        async def get(self, key): return self.stored.get(key)
        async def put(self, key, value, expiration_ttl):
            self.stored[key] = value
            self.ttls.append(expiration_ttl)
    class AI:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def post(self, *_, **__):
            return SimpleNamespace(is_success=True, json=lambda: {'candidates': [{'content': {'parts': [{'text': 'Coach works.'}]}}]})
    monkeypatch.setattr(hosted, 'request_json', public)
    monkeypatch.setattr(hosted, 'collect_public_data', collect)
    monkeypatch.setattr(hosted, 'compute_flow_metrics', lambda raw: {'repo': 'o/r', 'recent_stats': {'total': 1}})
    monkeypatch.setattr(hosted, 'assess_product_value', assess)
    monkeypatch.setattr(hosted.httpx, 'AsyncClient', lambda **_: AI())
    cache = Cache()
    env = SimpleNamespace(AUTH_REQUIRED='false', GEMINI_API_KEY='server-g', TYPESAFE_API_KEY='server-j',
                          ANALYTICS_CACHE=cache, ANALYZE_LIMIT=SimpleNamespace(limit=allowed),
                          CHAT_LIMIT=SimpleNamespace(limit=allowed))
    owner = hosted_client(env)
    owner.cookies.set('gha_session', 'session-a')
    result = owner.post('/api/analyze', json={'repo_url': 'o/r'}, headers={header: 'caller-key'})
    assert result.status_code == 200 and seen == [expected]
    analysis_id = result.json()['analysis_id']
    assert hosted.METRICS_CACHE_PREFIX + 'o/r' not in cache.stored
    assert all('session-a' not in key for key in cache.stored)
    assert all('caller-key' not in value for value in cache.stored.values())
    assert cache.ttls[-1] == expected_ttl
    payload = {'repo': 'o/r', 'message': 'What next?', 'analysis_id': analysis_id}
    reply = owner.post('/api/chat', json=payload)
    assert reply.status_code == 200 and reply.json()['reply'] == 'Coach works.'
    other = hosted_client(env)
    other.cookies.set('gha_session', 'session-b')
    assert other.post('/api/chat', json=payload).status_code == 409
    owner.cookies.clear()
    assert owner.post('/api/chat', json=payload).status_code == 401
    # A later default-key analysis returns to the normal shared path.
    result = other.post('/api/analyze', json={'repo_url': 'o/r'})
    assert result.status_code == 200 and 'analysis_id' not in result.json()
    assert other.post('/api/chat', json={'repo': 'o/r', 'message': 'What next?'}).status_code == 200
    assert cache.ttls[-1] == expected_ttl
    # Auth-disabled development must not silently select the default-key snapshot.
    anonymous = hosted_client(env)
    result = anonymous.post('/api/analyze', json={'repo_url': 'o/r'}, headers={header: 'caller-key'})
    assert result.status_code == 200
    assert anonymous.post('/api/chat', json={'repo': 'o/r', 'message': 'What next?',
                                           'analysis_id': result.json()['analysis_id']}).status_code == 401
