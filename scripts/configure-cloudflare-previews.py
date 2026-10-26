"""Plan isolated Preview resources; --apply provisions them and updates Wrangler.

Requires CLOUDFLARE_API_TOKEN with Workers KV Storage Write and D1 Write when
applying. Never deploys, copies production data, or changes Google OAuth settings.
Python standard library only; works on macOS, Linux and Windows.
"""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'wrangler.jsonc'
MANIFEST = ROOT / '.cloudflare' / 'preview-resources.json'


def api(account, path, method='GET', payload=None):
    request = urllib.request.Request(
        f'https://api.cloudflare.com/client/v4/accounts/{account}/{path}',
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Authorization': f'Bearer {os.environ["CLOUDFLARE_API_TOKEN"]}',
                 'Content-Type': 'application/json'}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # nosec B310: fixed HTTPS origin
            body = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'Cloudflare {method} failed (HTTP {exc.code}); response suppressed.') from None
    if not body.get('success'):
        raise RuntimeError(f'Cloudflare {method} failed; response suppressed.')
    result = body['result']
    if isinstance(result, list) and any(item.get('success') is False for item in result):
        raise RuntimeError('Cloudflare query failed; response suppressed.')
    return result


def find_resource(account, path, field, name):
    matches = []
    page = 1
    while True:
        items = api(account, f'{path}?page={page}&per_page=100')
        matches.extend(item for item in items if item.get(field) == name)
        if len(items) < 100:
            break
        page += 1
    if len(matches) > 1:
        raise ValueError(f'Multiple resources named {name}; refusing to choose one.')
    return matches[0] if matches else None


def assert_isolated(config, kv, database):
    if kv and kv['id'] in {item.get('id') for item in config.get('kv_namespaces', [])}:
        raise ValueError('Preview KV must not use a production namespace.')
    if database and database['uuid'] in {item.get('database_id') for item in config.get('d1_databases', [])}:
        raise ValueError('Preview D1 must not use a production database.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Create missing Preview resources, initialize schema, and write config')
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    config = json.loads(CONFIG.read_text(encoding='utf-8'))
    if config['name'] != manifest['worker'] or not re.fullmatch(r'[a-f0-9]{32}', manifest['account_id']):
        raise ValueError('Preview manifest must identify this Worker and a valid account ID.')
    if any(not manifest[key].startswith(config['name'] + '-preview-') for key in ['cache_namespace', 'users_database']):
        raise ValueError('Preview resources must use this Worker\'s preview prefix.')
    production_limits = {item['namespace_id'] for item in config['ratelimits']}
    preview_limits = list(manifest['rate_limit_namespaces'].values())
    if len(set(preview_limits)) != len(preview_limits) or production_limits.intersection(preview_limits):
        raise ValueError('Preview rate-limit namespaces must be distinct from each other and production.')
    print(json.dumps(manifest, indent=2))
    if not args.apply:
        print('Plan only. --apply reuses or creates the named KV/D1 resources, initializes the Preview schema, '
              'and writes Preview bindings. No deployment, production data copy, or OAuth change.')
        return 0
    if not os.environ.get('CLOUDFLARE_API_TOKEN'):
        raise RuntimeError('Export CLOUDFLARE_API_TOKEN from Bitwarden before --apply.')
    account = manifest['account_id']
    kv = find_resource(account, 'storage/kv/namespaces', 'title', manifest['cache_namespace'])
    database = find_resource(account, 'd1/database', 'name', manifest['users_database'])
    assert_isolated(config, kv, database)  # Check both before creating or mutating anything.
    if kv is None:
        kv = api(account, 'storage/kv/namespaces', 'POST', {'title': manifest['cache_namespace']})
    if database is None:
        database = api(account, 'd1/database', 'POST', {'name': manifest['users_database']})
    assert_isolated(config, kv, database)
    # These schemas are idempotent (IF NOT EXISTS), contain no user records,
    # and runs only against the independently resolved Preview database.
    api(account, f'd1/database/{database["uuid"]}/query', 'POST',
        {'sql': '\n'.join(path.read_text(encoding='utf-8') for path in sorted((ROOT / 'migrations').glob('*.sql')))})
    preview = config.setdefault('previews', {})
    preview['vars'] = {**preview.get('vars', {}), 'AUTH_REQUIRED': 'true',
                       'GOOGLE_OAUTH_CLIENT_ID': config['vars']['GOOGLE_OAUTH_CLIENT_ID']}
    preview['kv_namespaces'] = [{'binding': 'ANALYTICS_CACHE', 'id': kv['id']}]
    preview['d1_databases'] = [{'binding': 'USERS_DB', 'database_name': manifest['users_database'],
                              'database_id': database['uuid']}]
    preview['ratelimits'] = deepcopy(config['ratelimits'])
    for item in preview['ratelimits']:
        item['namespace_id'] = manifest['rate_limit_namespaces'][item['name']]
    CONFIG.write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
    print('Isolated Preview resources and schema are ready; Wrangler config updated. Nothing deployed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
