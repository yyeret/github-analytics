"""Exercise the MCP and OAuth routes with local D1 and simulated providers."""
import base64
import hashlib
import runpy
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

import cloudflare_app as hosted
import mcp_analytics
import mcp_oauth
import github_auth
from tests.unit.test_github_auth import Database


ORIGIN = 'https://analytics.test'
RESOURCE = ORIGIN + '/mcp'
REDIRECT = 'https://chatgpt.com/connector_platform_oauth_redirect'
VERIFIER = 'v' * 64
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip('=')
MCP_HEADERS = {'Accept': 'application/json, text/event-stream'}


@pytest.fixture
def client(monkeypatch):
    db = Database(check_same_thread=False)
    db.connection.execute('PRAGMA foreign_keys=ON')
    db.connection.executescript((Path(__file__).parents[2] / 'migrations/0003_mcp_oauth.sql').read_text())
    async def allowed(_):
        return {'success': True}

    async def allow_analysis(entry):
        return {'success': entry['key'] == 'mcp:github:12'}
    async def github(url, **kwargs):
        if url.endswith('/access_token'):
            return 200, {}, {'access_token': 'ghu_provider-secret', 'expires_in': 600}
        assert kwargs['headers']['Authorization'] == 'Bearer ghu_provider-secret'
        return 200, {}, {'id': 12, 'login': 'owner'}
    monkeypatch.setattr(github_auth, 'request_json', github)
    class Assets:
        async def fetch(self, url):
            async def read():
                builder = Path(__file__).parents[2] / 'scripts' / 'build-mcp-dashboard.py'
                return runpy.run_path(str(builder))['build']().encode('utf-8')

            return SimpleNamespace(status=200 if url.endswith('/mcp-dashboard') else 404, bytes=read)

    class Cache:
        def __init__(self):
            self.data = {}

        async def put(self, key, value, **_):
            self.data[key] = value

        async def get(self, key):
            return self.data.get(key)

    env = SimpleNamespace(USERS_DB=db, ASSETS=Assets(), ANALYTICS_CACHE=Cache(), ANALYZE_LIMIT=SimpleNamespace(limit=allow_analysis),
        CHAT_LIMIT=SimpleNamespace(limit=allowed), MCP_ORIGIN=ORIGIN,
        MCP_GITHUB_APP_CLIENT_ID='Iv23app', MCP_GITHUB_APP_CLIENT_SECRET='app-secret')

    async def wrapped(scope, receive, send):
        scope['env'] = env
        await hosted.app(scope, receive, send)
    with TestClient(wrapped, base_url=ORIGIN, follow_redirects=False) as test_client:
        yield test_client
    db.connection.close()


def rpc(client, method, params=None, token=None):
    headers = {**MCP_HEADERS, **({'Authorization': f'Bearer {token}'} if token else {})}
    return client.post('/mcp', headers=headers, json={'jsonrpc': '2.0', 'id': 1, 'method': method,
        'params': params or {}})


def begin(client, *, private=False):
    result = client.post('/mcp/oauth/register', json={'redirect_uris': [REDIRECT]})
    assert result.status_code == 201
    client_id = result.json()['client_id']
    params = dict(client_id=client_id, redirect_uri=REDIRECT, response_type='code',
        resource=RESOURCE, state='client-state', scope='analytics:read',
        code_challenge=CHALLENGE, code_challenge_method='S256')
    consent = client.get('/mcp/oauth/authorize', params=params)
    assert consent.status_code == 200
    assert consent.headers['referrer-policy'] == 'same-origin'
    state = client.cookies['gha_mcp_state']
    start = client.post('/mcp/oauth/authorize', data={'state': state, **({'private_consent': 'yes'} if private else {})}, headers={'Origin': ORIGIN})
    assert start.status_code == 303
    github_params = parse_qs(urlsplit(start.headers['Location']).query)
    assert github_params['client_id'] == ['Iv23app']
    assert github_params['code_challenge_method'] == ['S256']
    assert 'scope' not in github_params
    assert client.get('/mcp/oauth/github/callback', params={'state': 'wrong', 'code': 'provider-code'}).status_code == 400
    callback = client.get('/mcp/oauth/github/callback', params={'state': state, 'code': 'provider-code'})
    assert callback.status_code == 303
    redirect = urlsplit(callback.headers['Location'])
    assert redirect.netloc == 'chatgpt.com' and redirect.path == '/connector_platform_oauth_redirect'
    returned = parse_qs(redirect.query)
    assert returned['state'] == ['client-state'] and returned['iss'] == [ORIGIN]
    assert 'provider-secret' not in str(callback.headers)
    assert client.get('/mcp/oauth/github/callback', params={'state': state, 'code': 'provider-code'}).status_code == 400
    return dict(grant_type='authorization_code', client_id=client_id, code=returned['code'][0],
                redirect_uri=REDIRECT, code_verifier=VERIFIER, resource=RESOURCE)


def test_oauth_discovery_pkce_replay_audience_and_revoke(client):
    challenge = rpc(client, 'tools/list')
    assert challenge.status_code == 401
    assert f'{ORIGIN}/.well-known/oauth-protected-resource/mcp' in challenge.headers['www-authenticate']
    assert client.get('/.well-known/oauth-protected-resource/mcp').json()['resource'] == RESOURCE
    assert client.get('/.well-known/oauth-authorization-server').json()['code_challenge_methods_supported'] == ['S256']

    data = begin(client)
    for invalid in ({'resource': ORIGIN + '/api'}, {'code_verifier': 'x' * 64},
                    {'client_id': 'another-client'}, {'redirect_uri': 'https://evil.test/callback'}):
        denied = client.post('/mcp/oauth/token', data={**data, **invalid})
        assert denied.status_code == 400
        assert 'access_token' not in denied.json()
    result = client.post('/mcp/oauth/token', data=data)
    assert result.status_code == 200
    assert result.headers['cache-control'] == 'no-store'
    token = result.json()['access_token']
    assert token != 'ghu_provider-secret'
    assert client.post('/mcp/oauth/token', data=data).status_code == 400
    assert rpc(client, 'tools/list', token=token).status_code == 200
    assert rpc(client, 'tools/list', token='ghu_provider-secret').status_code == 401
    assert client.post('/mcp/oauth/revoke', data={'token': token, 'client_id': data['client_id']}).status_code == 200
    assert rpc(client, 'tools/list', token=token).status_code == 401


def connect(client, *, private=False):
    data = begin(client, private=private)
    return client.post('/mcp/oauth/token', data=data).json()['access_token']


def tool(client, name, args, token):
    return rpc(client, 'tools/call', {'name': name, 'arguments': args}, token).json()['result']


def test_refresh_rotates_and_caps_credentials_at_github_expiry(client, monkeypatch):
    monkeypatch.setattr(mcp_oauth.time, 'time', lambda: 1000)
    data = begin(client)
    first = client.post('/mcp/oauth/token', data=data).json()
    refresh = {'grant_type': 'refresh_token', 'refresh_token': first['refresh_token'],
               'resource': RESOURCE, 'client_id': data['client_id']}
    monkeypatch.setattr(mcp_oauth.time, 'time', lambda: 1500)
    second = client.post('/mcp/oauth/token', data=refresh).json()
    assert second['expires_in'] == 100
    assert rpc(client, 'tools/list', token=first['access_token']).status_code == 401
    assert rpc(client, 'tools/list', token=second['access_token']).status_code == 200
    assert client.post('/mcp/oauth/token', data=refresh).status_code == 400
    assert rpc(client, 'tools/list', token=second['access_token']).status_code == 401
    monkeypatch.setattr(mcp_oauth.time, 'time', lambda: 1600)
    assert rpc(client, 'tools/list', token=second['access_token']).status_code == 401


@pytest.mark.parametrize('uri', ['https://evil.test/callback', 'https://chatgpt.com@evil.test/connector/oauth/x',
                                'https://chatgpt.com/connector_platform_oauth_redirect#fragment'])
def test_registration_rejects_untrusted_callbacks(client, uri):
    assert client.post('/mcp/oauth/register', json={'redirect_uris': [uri]}).status_code == 400


def test_resource_binds_the_interactive_dashboard_to_the_tool(client):
    token = connect(client)
    tools = rpc(client, 'tools/list', token=token).json()['result']['tools']
    definition = next(tool for tool in tools if tool['name'] == 'show_github_dashboard')
    assert definition['securitySchemes'] == [{'type': 'oauth2', 'scopes': ['analytics:read']}]
    uri = definition['_meta']['ui']['resourceUri']
    resource = rpc(client, 'resources/read', {'uri': uri}, token).json()['result']['contents'][0]
    assert resource['mimeType'] == 'text/html;profile=mcp-app'
    assert resource['_meta']['ui']['csp']['connectDomains'] == []
    assert 'ui/notifications/tool-result' in resource['text']
    assert 'function renderDashboard(' in resource['text']
    assert 'configureAuthentication' not in resource['text']
    assert 'ghu_provider-secret' not in resource['text']


def test_private_dashboard_requires_browser_consent_and_current_github_access(client, monkeypatch):
    calls = []
    private = True

    async def metadata(url, **kwargs):
        calls.append(kwargs['headers']['Authorization'])
        return 200, {}, {'private': private, 'id': 42}

    async def graph(url, **kwargs):
        assert url == 'https://api.github.com/graphql'
        assert kwargs['headers']['Authorization'] == 'Bearer ghu_provider-secret'
        search = kwargs['body']['variables']['searchQuery']
        node = {'number': 7, 'title': 'Sensitive issue', 'body': 'Internal context',
                'createdAt': '2026-01-01T00:00:00Z', 'repository': {'nameWithOwner': 'o/r', 'isPrivate': True, 'fullDatabaseId': '42'}}
        return 200, {}, {'data': {'search': {'nodes': [node] if 'is:issue is:open' in search else [],
                    'pageInfo': {'hasNextPage': False}}}}

    monkeypatch.setattr(hosted, 'request_json', metadata)
    monkeypatch.setattr(mcp_analytics, 'request_json', graph)
    public_token = connect(client)
    private_token = connect(client, private=True)
    args = {'repo': 'o/r', 'allow_private': True, 'view': 'issues'}
    assert tool(client, 'show_github_dashboard', args, public_token)['isError']
    assert not calls
    assert tool(client, 'show_github_dashboard', {'repo': 'o/r'}, private_token)['isError']
    result = tool(client, 'show_github_dashboard', args, private_token)
    assert result['structuredContent']['open_issues_in_sample'] == 1
    assert result['structuredContent']['merged_pr_sample'] == 0
    assert result['_meta']['metrics']['issues']['stale']['items'][0]['title'] == 'Sensitive issue'
    assert 'Sensitive issue' not in str(result['structuredContent'])
    assert 'ghu_provider-secret' not in str(result)
    assert calls == ['Bearer ghu_provider-secret', 'Bearer ghu_provider-secret']
    assert tool(client, 'ask_flow_coach', {'repo': 'o/r', 'message': 'Summarize',
        'analysis_id': result['structuredContent']['analysis_id'], 'allow_private': True}, public_token)['isError']
    private = False
    response = tool(client, 'show_github_dashboard', args, private_token)
    assert response['isError'] and 'Sensitive issue' not in str(response)


def test_modern_mcp_checks_routing_headers(client):
    token = connect(client)
    version = '2026-07-28'
    uri = 'ui://github-analytics/dashboard-v1.html'
    headers = {**MCP_HEADERS, 'Authorization': f'Bearer {token}', 'MCP-Protocol-Version': version,
               'Mcp-Method': 'resources/read', 'Mcp-Name': uri}
    body = {'jsonrpc': '2.0', 'id': 1, 'method': 'resources/read', 'params': {'uri': uri,
            '_meta': {'io.modelcontextprotocol/protocolVersion': version}}}
    assert client.post('/mcp', headers={**headers, 'Mcp-Name': 'ui://other/chart'}, json=body).json()['error']['code'] == -32020
    result = client.post('/mcp', headers=headers, json=body).json()['result']
    assert result['resultType'] == 'complete'
    assert result['ttlMs'] == 0
    assert result['cacheScope'] == 'private'
    assert result['contents'][0]['uri'] == uri
    assert result['_meta']['io.modelcontextprotocol/serverInfo']['name'] == 'github-flow-analytics'


def test_account_deletion_revokes_mcp_connection(client):
    token = connect(client, private=True)
    assert client.delete('/api/auth/account', headers={'Authorization': 'Bearer ghu_provider-secret',
        'Origin': ORIGIN}).status_code == 200
    assert rpc(client, 'tools/list', token=token).status_code == 401


def test_public_coach_keeps_the_displayed_snapshot_after_refresh(client, monkeypatch):
    from tests.factories import make_pr
    import json

    async def metadata(*_, **__):
        return 200, {}, {'private': False, 'id': 12}

    sampled_title = 'Original'
    async def graph(*_, **kwargs):
        terms = kwargs['body']['variables']['searchQuery']
        node = {**make_pr(created='2026-01-10T00:00:00Z', merged='2026-01-11T00:00:00Z', title=sampled_title),
                'repository': {'nameWithOwner': 'o/r', 'isPrivate': False, 'fullDatabaseId': '12'}}
        return 200, {}, {'data': {'search': {'nodes': [node] if 'is:pr is:merged' in terms and 'merged:' not in terms else [],
            'pageInfo': {'hasNextPage': False}}}}

    async def no_value(*_, **__):
        return {'status': 'unavailable'}

    class CoachProvider:
        def __init__(self, **_): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, *, headers, json):
            prompt = json['system_instruction']['parts'][0]['text']
            assert 'Original' in prompt and 'After refresh' not in prompt
            return SimpleNamespace(is_success=True, json=lambda: {'candidates': [
                {'content': {'parts': [{'text': 'The displayed snapshot.'}]}}]})

    monkeypatch.setattr(hosted, 'request_json', metadata)
    monkeypatch.setattr(mcp_analytics, 'request_json', graph)
    monkeypatch.setattr(hosted, 'assess_product_value', no_value)
    # Configure the same environment that reached the running route.
    async def limited(request, kind, limit, **_):
        request.scope['env'].GEMINI_API_KEY = 'local-fixture'
    monkeypatch.setattr(hosted, 'enforce_limit', limited)
    monkeypatch.setattr(hosted.httpx, 'AsyncClient', CoachProvider)
    token = connect(client)
    first = tool(client, 'show_github_dashboard', {'repo': 'o/r'}, token)
    sampled_title = 'After refresh'
    refreshed = tool(client, 'refresh_github_dashboard', {'repo': 'o/r', 'force_refresh': True}, token)
    analysis_id = first['structuredContent']['analysis_id']
    assert refreshed['structuredContent']['analysis_id'] != analysis_id
    assert 'After refresh' in json.dumps(refreshed['_meta'])
    result = tool(client, 'ask_flow_coach', {'repo': 'o/r', 'message': 'Summarize',
                                           'analysis_id': analysis_id}, token)
    assert result['structuredContent']['reply'] == 'The displayed snapshot.'


@pytest.mark.parametrize('tool_name', ['show_github_dashboard', 'show_issue_value'])
@pytest.mark.parametrize('became_private', [False, True])
def test_public_mcp_value_uses_visibility_checked_github_reads(client, monkeypatch, tool_name, became_private):
    from tests.factories import make_pr
    from tests.integration.test_product_value import FakeServices, jev_reply
    import issue_candidates
    import product_value

    identity = {'nameWithOwner': 'o/r', 'isPrivate': False, 'fullDatabaseId': '12'}
    changed = {**identity, 'isPrivate': became_private}
    issue = {'number': 16, 'state': 'OPEN', 'title': 'Improve reporting', 'body': 'Add CSV export.',
             'url': 'https://github.com/o/r/issues/16', 'repository': changed}

    async def metadata(url, **kwargs):
        assert kwargs['headers']['Authorization'] == 'Bearer ghu_provider-secret'
        return 200, {}, {'private': False, 'id': 12, 'full_name': 'o/r'}

    async def graph(url, **kwargs):
        assert url == 'https://api.github.com/graphql'
        assert kwargs['headers']['Authorization'] == 'Bearer ghu_provider-secret'
        body = kwargs['body']
        if 'RepositoryValueSources' in body['query']:
            assert body['variables']['owner'] == 'o'
            return 200, {}, {'data': {'repository': {**changed, 'root': {'entries': [{'name': 'README.md', 'type': 'blob'}]}}}}
        if 'RepositoryReadme' in body['query']:
            assert body['variables']['path'] == 'HEAD:README.md'
            return 200, {}, {'data': {'repository': {**changed, 'readme': {'text': 'A flow dashboard.'}}}}
        if 'ValueIssues' in body['query']:
            return 200, {}, {'data': {'repository': {**changed, 'issue0': issue}}}
        terms = body['variables']['searchQuery']
        node = {**make_pr(title='Add export'), 'body': 'Add a CSV export.', 'repository': identity}
        return 200, {}, {'data': {'search': {'nodes': [node] if 'is:pr is:merged' in terms and 'merged:' not in terms else [],
                                           'pageInfo': {'hasNextPage': False}}}}

    class Providers(FakeServices):
        async def __call__(self, url, **kwargs):
            if url.startswith('https://api.github.com/repos/'):
                assert 'Authorization' not in kwargs['headers']
                return 403, {}, {}
            assert not became_private, 'A repository that changed visibility reached an AI provider'
            if 'typesafe.ai' in url:
                self.jev_requests.append(kwargs['body'])
                return 200, {}, jev_reply()
            return await super().__call__(url, **kwargs)

    async def configure(request, kind, limit, **_):
        request.scope['env'].GEMINI_API_KEY = 'gemini-fixture'
        request.scope['env'].TYPESAFE_API_KEY = 'jev-fixture'

    async def anonymous_issue(*args, **kwargs):
        return 403, {}, {}

    provider = Providers()
    monkeypatch.setattr(hosted, 'request_json', metadata)
    monkeypatch.setattr(hosted, 'enforce_limit', configure)
    monkeypatch.setattr(mcp_analytics, 'request_json', graph)
    monkeypatch.setattr(issue_candidates, 'request_json', anonymous_issue)
    monkeypatch.setattr(product_value, 'request_json', provider)
    args = {'repo': 'previous/r', **({'issue_numbers': [16]} if tool_name == 'show_issue_value' else {'view': 'value'})}
    result = tool(client, tool_name, args, connect(client))
    if became_private and tool_name == 'show_issue_value':
        assert result['isError']
        assert 'Improve reporting' not in str(result)
        return
    assert result['structuredContent']['repo'] == 'o/r'
    record = result['_meta']['issueValue' if tool_name == 'show_issue_value' else 'metrics']['product_value']
    if became_private:
        assert record['status'] == 'failed'
        assert not record['items']
        assert not provider.prompts and not provider.jev_requests
    else:
        assert record['status'] == 'ok'
        assert record['assessed'] == 1
        assert record['items'][0]['cost_of_delay'] == 29
        assert record['sources']['readme']
