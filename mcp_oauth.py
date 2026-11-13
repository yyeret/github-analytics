"""OAuth authorization server for the GitHub Analytics MCP resource."""
import base64
import hashlib
import html
import json
import re
import secrets
import time
from urllib.parse import parse_qsl, urlencode, urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from github_auth import _provider, identify_token, token_hash

router = APIRouter()
SCOPE = 'analytics:read'
STATE_COOKIE = 'gha_mcp_state'
OAUTH_PATH = '/mcp/oauth'
GITHUB_CALLBACK = OAUTH_PATH + '/github/callback'
STATE_TTL = 600
CODE_TTL = 120
ACCESS_TTL = 3600
GRANT_TTL = 8 * 3600
CLIENT_TTL = 90 * 86400
PKCE = re.compile(r'[A-Za-z0-9._~-]{43,128}')
CHALLENGE = re.compile(r'[A-Za-z0-9_-]{43}')
TOKEN = re.compile(r'gha_mcp_[A-Za-z0-9_-]{43}')
CHATGPT_REDIRECT = 'https://chatgpt.com/connector_platform_oauth_redirect'
GRANT_TYPES = ['authorization_code', 'refresh_token']


def oauth_context(request: Request):
    env = request.scope.get('env')
    origin = getattr(env, 'MCP_ORIGIN', '')
    if not origin or not getattr(env, 'MCP_GITHUB_APP_CLIENT_ID', '') or not getattr(env, 'MCP_GITHUB_APP_CLIENT_SECRET', ''):
        raise HTTPException(503, 'The MCP connection is being configured.')
    if origin != f'https://{request.url.netloc}' or request.url.scheme != 'https':
        raise HTTPException(400, 'Use the configured HTTPS address for GitHub Analytics.')
    db = getattr(env, 'USERS_DB', None)
    if db is None:
        raise HTTPException(503, 'MCP sign-in storage is unavailable.')
    return env, origin, db


def bearer_challenge(origin, invalid=False):
    error = ', error="invalid_token"' if invalid else ''
    return f'Bearer resource_metadata="{origin}/.well-known/oauth-protected-resource/mcp", scope="{SCOPE}"{error}'


def pkce_challenge(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')


async def bounded_body(request, limit=65536):
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise HTTPException(413, 'Request body is too large.')
        body.extend(chunk)
    return bytes(body)


async def form_parameters(request):
    if request.headers.get('content-type', '').partition(';')[0].strip() != 'application/x-www-form-urlencoded':
        raise HTTPException(415, 'Use application/x-www-form-urlencoded.')
    try:
        pairs = parse_qsl((await bounded_body(request, 16384)).decode('utf-8'),
                          keep_blank_values=True, max_num_fields=20, errors='strict')
    except ValueError:
        raise HTTPException(400, 'Invalid OAuth request.') from None
    parameters = dict(pairs)
    if len(parameters) != len(pairs):
        raise HTTPException(400, 'Duplicate OAuth parameters.')
    return parameters


async def rate_limit(env, request):
    limiter = getattr(env, 'CHAT_LIMIT', None)
    if limiter is None:
        raise HTTPException(503, 'MCP sign-in rate limiting is unavailable.')
    key = request.headers.get('CF-Connecting-IP', 'unknown')
    if not (await limiter.limit({'key': 'mcp-oauth:' + key})).get('success'):
        raise HTTPException(429, 'Too many sign-in attempts. Try again shortly.')


def redirect_allowed(uri, env):
    return safe_redirect(uri, getattr(env, 'MCP_OAUTH_REDIRECT_URIS', ''),
                         getattr(env, 'MCP_OAUTH_ALLOW_LOOPBACK', '') == 'true')


def oauth_error(error):
    return JSONResponse({'error': error}, status_code=400)


def safe_redirect(uri, configured, allow_loopback=False):
    if not isinstance(uri, str) or len(uri) > 1000 or re.search(r'[\x00-\x20\x7f\\]', uri):
        return False
    try:
        parsed = urlsplit(uri)
        if parsed.username or parsed.password or parsed.fragment or not parsed.hostname or '#' in uri:
            return False
        if parsed.scheme == 'https':
            return uri == CHATGPT_REDIRECT or uri in configured.split()
        return (allow_loopback and parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', '::1', 'localhost')
                and parsed.port is not None and parsed.port > 0 and parsed.path.startswith('/')
                and not parsed.query)
    except ValueError:
        return False


@router.get('/.well-known/oauth-protected-resource')
@router.get('/.well-known/oauth-protected-resource/mcp')
async def protected_resource(request: Request):
    _, origin, _ = oauth_context(request)
    return {'resource': origin + '/mcp', 'authorization_servers': [origin], 'scopes_supported': [SCOPE],
            'bearer_methods_supported': ['header'], 'resource_name': 'GitHub Flow Analytics'}


@router.get('/.well-known/oauth-authorization-server')
async def authorization_metadata(request: Request):
    _, origin, _ = oauth_context(request)
    base = origin + OAUTH_PATH
    return {'issuer': origin, 'authorization_endpoint': base + '/authorize',
            'token_endpoint': base + '/token', 'registration_endpoint': base + '/register',
            'revocation_endpoint': base + '/revoke', 'response_types_supported': ['code'],
            'grant_types_supported': GRANT_TYPES, 'code_challenge_methods_supported': ['S256'],
            'token_endpoint_auth_methods_supported': ['none'], 'revocation_endpoint_auth_methods_supported': ['none'],
            'scopes_supported': [SCOPE], 'authorization_response_iss_parameter_supported': True}


@router.post(OAUTH_PATH + '/register')
async def register(request: Request):
    env, _, db = oauth_context(request)
    await rate_limit(env, request)
    if request.headers.get('content-type', '').partition(';')[0].strip() != 'application/json':
        return oauth_error('invalid_client_metadata')
    try:
        data = json.loads(await bounded_body(request, 16384))
    except ValueError:
        return oauth_error('invalid_client_metadata')
    if not isinstance(data, dict):
        return oauth_error('invalid_client_metadata')
    uris = data.get('redirect_uris')
    if (not isinstance(uris, list) or not 1 <= len(uris) <= 5
            or not all(redirect_allowed(uri, env) for uri in uris)):
        return oauth_error('invalid_redirect_uri')
    grants = data.get('grant_types', ['authorization_code'])
    if (data.get('token_endpoint_auth_method', 'none') != 'none' or data.get('response_types', ['code']) != ['code']
            or not isinstance(grants, list) or 'authorization_code' not in grants
            or any(grant not in GRANT_TYPES for grant in grants)):
        return oauth_error('invalid_client_metadata')
    uris = sorted(set(uris))
    encoded = json.dumps(uris)
    now = int(time.time())
    expiry = now + CLIENT_TTL if any(url.startswith('http:') for url in uris) else None
    row = await db.prepare(
        'INSERT INTO mcp_oauth_clients VALUES (?, ?, ?) '
        'ON CONFLICT(redirect_uris) DO UPDATE SET expires_at = excluded.expires_at RETURNING client_id'
    ).bind(secrets.token_urlsafe(24), encoded, expiry).first()
    client_id = row['client_id']
    return JSONResponse({'client_id': client_id, 'client_id_issued_at': now, 'redirect_uris': uris,
        'token_endpoint_auth_method': 'none', 'response_types': ['code'], 'grant_types': GRANT_TYPES,
        'scope': SCOPE}, status_code=201)


@router.get(OAUTH_PATH + '/authorize')
async def consent(request: Request):
    env, origin, db = oauth_context(request)
    await rate_limit(env, request)
    params = request.query_params
    client_id = params.get('client_id', '')
    redirect_uri = params.get('redirect_uri', '')
    client_state = params.get('state', '')
    challenge = params.get('code_challenge', '')
    if (any(len(params.getlist(key)) != 1 for key in params)
            or not 1 <= len(client_state) <= 1024 or not CHALLENGE.fullmatch(challenge)
            or params.get('response_type') != 'code' or params.get('code_challenge_method') != 'S256'
            or params.get('resource', origin + '/mcp') != origin + '/mcp'
            or params.get('scope', SCOPE) != SCOPE or len(client_id) > 100):
        raise HTTPException(400, 'Invalid MCP OAuth request.')
    now = int(time.time())
    client = await db.prepare('SELECT redirect_uris FROM mcp_oauth_clients WHERE client_id = ? AND (expires_at IS NULL OR expires_at > ?)').bind(
        client_id, now).first()
    if (not client or redirect_uri not in json.loads(client['redirect_uris'])
            or not redirect_allowed(redirect_uri, env)):
        raise HTTPException(400, 'Unrecognized MCP client or callback URL.')
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    await db.prepare('INSERT INTO mcp_oauth_states (state_hash, client_id, redirect_uri, client_state, challenge, '
                     'github_verifier, issuer, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)').bind(
        token_hash(state), client_id, redirect_uri, client_state, challenge, verifier, origin, now + STATE_TTL).run()
    slug = getattr(env, 'MCP_GITHUB_APP_SLUG', '')
    install_link = (
        f'<p><a href="https://github.com/apps/{slug}/installations/new">Install the GitHub App on selected repositories</a></p>'
        if re.fullmatch(r'[A-Za-z0-9-]{1,100}', slug) else ''
    )
    response = HTMLResponse(
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        '<title>Connect GitHub Flow Analytics</title>'
        '<style>body{font:1rem system-ui;max-width:38rem;margin:4rem auto;padding:1rem;color-scheme:light dark}'
        'button{font:inherit;padding:.6rem 1rem}code{overflow-wrap:anywhere}</style>'
        '<h1>Connect GitHub Flow Analytics</h1>'
        f'<p>Return analytics to the chat client at <code>{html.escape(redirect_uri)}</code>.</p>'
        '<p>GitHub determines which repositories you can read. Private analysis requires separate consent '
        'to share results with your chat client and send bounded repository context to Gemini and Jev.</p>'
        f'{install_link}'
        f'<form method="post" action="{OAUTH_PATH}/authorize">'
        '<p><label><input type="checkbox" name="private_consent" value="yes"> Allow private repository analysis '
        'in this chat connection, including the processing described above.</label></p>'
        f'<input type="hidden" name="state" value="{state}"><button>Continue with GitHub</button></form>'
        '<p><a href="/privacy.html">Privacy policy</a></p>',
        headers={'Referrer-Policy': 'same-origin'})
    response.set_cookie(STATE_COOKIE, state, max_age=STATE_TTL, secure=True, httponly=True, samesite='lax',
                        path=OAUTH_PATH)
    return response


def browser_state(request, state):
    cookie = request.cookies.get(STATE_COOKIE, '')
    if (not re.fullmatch(r'[A-Za-z0-9_-]{43}', state) or not cookie.isascii()
            or not secrets.compare_digest(state, cookie)):
        raise HTTPException(400, 'MCP sign-in could not be verified. Start again.')
    return token_hash(state)


@router.post(OAUTH_PATH + '/authorize')
async def start_github(request: Request):
    env, origin, db = oauth_context(request)
    await rate_limit(env, request)
    if request.headers.get('origin') != origin:
        raise HTTPException(403, 'Sign-in origin could not be verified.')
    data = await form_parameters(request)
    state = data.get('state', '')
    state_hash = browser_state(request, state)
    row = await db.prepare('UPDATE mcp_oauth_states SET private_consent = ? '
                           'WHERE state_hash = ? AND issuer = ? AND expires_at > ? AND private_consent IS NULL '
                           'RETURNING github_verifier').bind(
        int(data.get('private_consent') == 'yes'), state_hash, origin, int(time.time())).first()
    if not row:
        raise HTTPException(400, 'MCP sign-in expired. Start again.')
    return RedirectResponse('https://github.com/login/oauth/authorize?' + urlencode({
        'client_id': env.MCP_GITHUB_APP_CLIENT_ID, 'redirect_uri': origin + GITHUB_CALLBACK,
        'state': state, 'code_challenge': pkce_challenge(row['github_verifier']),
        'code_challenge_method': 'S256',
    }), status_code=303)


def return_to_client(row, **params):
    separator = '&' if urlsplit(row['redirect_uri']).query else '?'
    response = RedirectResponse(row['redirect_uri'] + separator + urlencode({
        'state': row['client_state'], 'iss': row['issuer'], **params,
    }), status_code=303)
    response.delete_cookie(STATE_COOKIE, path=OAUTH_PATH, secure=True, httponly=True, samesite='lax')
    return response


@router.get(GITHUB_CALLBACK)
async def github_callback(request: Request):
    env, origin, db = oauth_context(request)
    state_hash = browser_state(request, request.query_params.get('state', ''))
    now = int(time.time())
    row = await db.prepare('DELETE FROM mcp_oauth_states WHERE state_hash = ? AND issuer = ? RETURNING *').bind(
        state_hash, origin).first()
    if not row or row['expires_at'] <= now or row['private_consent'] is None:
        raise HTTPException(400, 'MCP sign-in expired or was already used. Start again.')
    if request.query_params.get('error') == 'access_denied':
        return return_to_client(row, error='access_denied')
    code = request.query_params.get('code', '')
    if not 1 <= len(code) <= 1024:
        raise HTTPException(400, 'GitHub did not complete sign-in.')
    status, _, data = await _provider('https://github.com/login/oauth/access_token', method='POST',
        headers={'Accept': 'application/json', 'User-Agent': 'github-flow-analytics'}, body={
            'client_id': env.MCP_GITHUB_APP_CLIENT_ID, 'client_secret': env.MCP_GITHUB_APP_CLIENT_SECRET,
            'code': code, 'code_verifier': row['github_verifier'], 'redirect_uri': origin + GITHUB_CALLBACK,
        })
    if status != 200 or not isinstance(data, dict) or not isinstance(data.get('access_token'), str):
        raise HTTPException(400, 'GitHub did not authorize sign-in. Start again.')
    github_token, lifetime = data['access_token'], data.get('expires_in')
    if not github_token.startswith('ghu_') or type(lifetime) is not int or lifetime <= 0:
        raise HTTPException(503, 'MCP sign-in requires an expiring GitHub App user token.')
    identity = await identify_token(github_token)
    authorization_code = secrets.token_urlsafe(32)
    await db.prepare(
        'INSERT INTO mcp_oauth_grants (grant_id, code_hash, client_id, redirect_uri, challenge, issuer, github_id, '
        'github_token, private_consent, expires_at, code_expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)').bind(
        secrets.token_urlsafe(24), token_hash(authorization_code), row['client_id'], row['redirect_uri'], row['challenge'], origin,
        str(identity.github_id), identity.token, row['private_consent'], now + min(lifetime, GRANT_TTL), now + CODE_TTL).run()
    return return_to_client(row, code=authorization_code)


@router.post(OAUTH_PATH + '/token')
async def exchange(request: Request):
    env, origin, db = oauth_context(request)
    await rate_limit(env, request)
    data = await form_parameters(request)
    grant = data.get('grant_type')
    if grant not in GRANT_TYPES:
        return oauth_error('unsupported_grant_type')
    if data.get('resource', origin + '/mcp') != origin + '/mcp':
        return oauth_error('invalid_target')
    if data.get('scope', SCOPE) != SCOPE:
        return oauth_error('invalid_scope')
    now = int(time.time())
    access = 'gha_mcp_' + secrets.token_urlsafe(32)
    refresh = 'gha_refresh_' + secrets.token_urlsafe(32)
    values = (token_hash(access), token_hash(refresh), now + ACCESS_TTL, data.get('client_id', ''), origin, now)
    update = ('UPDATE mcp_oauth_grants SET code_hash = NULL, access_hash = ?, refresh_hash = ?, '
              'access_expires_at = min(?, expires_at) WHERE client_id = ? AND issuer = ? AND expires_at > ? ')
    if grant == 'authorization_code':
        verifier = data.get('code_verifier', '')
        if not PKCE.fullmatch(verifier):
            return oauth_error('invalid_grant')
        update += 'AND code_hash = ? AND redirect_uri = ? AND challenge = ? AND code_expires_at > ? '
        values += (token_hash(data.get('code', '')), data.get('redirect_uri', ''), pkce_challenge(verifier), now)
    else:
        update += 'AND refresh_hash = ? '
        refresh_digest = token_hash(data.get('refresh_token', ''))
        values += (refresh_digest,)
    row = await db.prepare(update + 'RETURNING access_expires_at').bind(*values).first()
    if not row:
        if grant == 'refresh_token':
            await db.prepare(
                'DELETE FROM mcp_oauth_grants WHERE client_id = ? AND issuer = ? AND grant_id = '
                '(SELECT grant_id FROM mcp_oauth_spent_refresh WHERE refresh_hash = ?)'
            ).bind(data.get('client_id', ''), origin, refresh_digest).run()
        return oauth_error('invalid_grant')
    return {'access_token': access, 'refresh_token': refresh, 'token_type': 'Bearer',
            'expires_in': row['access_expires_at'] - now, 'scope': SCOPE}


@router.post(OAUTH_PATH + '/revoke')
async def revoke(request: Request):
    env, origin, db = oauth_context(request)
    await rate_limit(env, request)
    data = await form_parameters(request)
    digest = token_hash(data.get('token', ''))
    await db.prepare('DELETE FROM mcp_oauth_grants WHERE client_id = ? AND issuer = ? '
                     'AND (access_hash = ? OR refresh_hash = ?)').bind(
        data.get('client_id', ''), origin, digest, digest).run()
    return {}


async def access_grant(request):
    _, origin, db = oauth_context(request)
    scheme, _, token = request.headers.get('authorization', '').partition(' ')
    row = None
    if scheme.lower() == 'bearer' and TOKEN.fullmatch(token):
        now = int(time.time())
        row = await db.prepare('SELECT github_id, github_token, private_consent FROM mcp_oauth_grants '
                               'WHERE access_hash = ? AND issuer = ? AND access_expires_at > ? AND expires_at > ?').bind(
            token_hash(token), origin, now, now).first()
    if not row:
        raise HTTPException(401, 'Connect GitHub Flow Analytics to continue.',
            headers={'WWW-Authenticate': bearer_challenge(origin, bool(token))})
    return row, origin


async def mcp_storage_ready(database):
    return await database.prepare(
        "SELECT name FROM sqlite_schema WHERE type = 'table' AND name = 'mcp_oauth_grants'"
    ).first()


async def delete_mcp_grants(database, github_id):
    if await mcp_storage_ready(database):
        await database.prepare('DELETE FROM mcp_oauth_grants WHERE github_id = ?').bind(str(github_id)).run()


async def cleanup_mcp_oauth(database):
    if not await mcp_storage_ready(database):
        return
    now = int(time.time())
    await database.prepare('DELETE FROM mcp_oauth_grants WHERE expires_at <= ? '
                           'OR (code_hash IS NOT NULL AND code_expires_at <= ?)').bind(now, now).run()
    await database.prepare('DELETE FROM mcp_oauth_states WHERE expires_at <= ?').bind(now).run()
    await database.prepare('DELETE FROM mcp_oauth_clients WHERE expires_at <= ?').bind(now).run()
