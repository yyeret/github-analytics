"""Preview setup must never mutate production or write resources without --apply."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('cloudflare_previews', ROOT / 'scripts/configure-cloudflare-previews.py')
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


def test_default_only_describes_the_plan(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['configure-cloudflare-previews.py'])
    monkeypatch.setattr(script, 'api', lambda *args, **kwargs: pytest.fail('Plan must not call Cloudflare'))
    assert script.main() == 0


def test_apply_is_resumable_and_never_recreates_existing_resources(monkeypatch, tmp_path):
    config_path = tmp_path / 'wrangler.jsonc'
    config_path.write_text((ROOT / 'wrangler.jsonc').read_text())
    monkeypatch.setattr(script, 'CONFIG', config_path)
    monkeypatch.setenv('CLOUDFLARE_API_TOKEN', 'test-token')
    monkeypatch.setattr(sys, 'argv', ['configure-cloudflare-previews.py', '--apply'])
    calls = []

    def fake(account, path, method='GET', payload=None):
        calls.append((path, method, payload))
        if method == 'GET':
            return [{'id': 'preview-kv', 'title': 'github-flow-analytics-preview-cache'}] if 'storage/kv' in path else [
                {'uuid': 'preview-d1', 'name': 'github-flow-analytics-preview-users'}]
        assert path == 'd1/database/preview-d1/query'
        assert 'CREATE TABLE IF NOT EXISTS users' in payload['sql']
        return [{'success': True}]

    monkeypatch.setattr(script, 'api', fake)
    assert script.main() == 0
    assert script.main() == 0
    assert [method for _, method, _ in calls].count('POST') == 2  # schema only, on the preview database
    preview = json.loads(config_path.read_text())['previews']
    assert preview['vars']['AUTH_REQUIRED'] == 'true'
    assert preview['kv_namespaces'][0]['id'] == 'preview-kv'
    assert preview['d1_databases'][0]['database_id'] == 'preview-d1'


@pytest.mark.parametrize('kind', ['kv', 'd1'])
def test_refuses_production_resources_before_any_write(monkeypatch, tmp_path, kind):
    config_path = tmp_path / 'wrangler.jsonc'
    config = json.loads((ROOT / 'wrangler.jsonc').read_text())
    production_id = config['d1_databases'][0]['database_id'] if kind == 'd1' else config['kv_namespaces'][0]['id']
    config_path.write_text(json.dumps(config))
    monkeypatch.setattr(script, 'CONFIG', config_path)
    monkeypatch.setenv('CLOUDFLARE_API_TOKEN', 'test-token')
    monkeypatch.setattr(sys, 'argv', ['configure-cloudflare-previews.py', '--apply'])

    def fake(account, path, method='GET', payload=None):
        assert method == 'GET', 'Production isolation must be checked before any write'
        if 'storage/kv' in path:
            return [{'id': production_id, 'title': 'github-flow-analytics-preview-cache'}] if kind == 'kv' else []
        return [{'uuid': production_id, 'name': 'github-flow-analytics-preview-users'}] if kind == 'd1' else []

    monkeypatch.setattr(script, 'api', fake)
    with pytest.raises(ValueError, match='production'):
        script.main()


def test_apply_creates_missing_resources_then_initializes_only_the_new_database(monkeypatch, tmp_path):
    config_path = tmp_path / 'wrangler.jsonc'
    original = json.loads((ROOT / 'wrangler.jsonc').read_text())
    config_path.write_text(json.dumps(original))
    monkeypatch.setattr(script, 'CONFIG', config_path)
    monkeypatch.setenv('CLOUDFLARE_API_TOKEN', 'test-token')
    monkeypatch.setattr(sys, 'argv', ['configure-cloudflare-previews.py', '--apply'])
    writes = []

    def fake(account, path, method='GET', payload=None):
        if method == 'GET':
            return []
        writes.append((path, payload))
        if path == 'storage/kv/namespaces':
            return {'id': 'new-preview-kv'}
        if path == 'd1/database':
            return {'uuid': 'new-preview-d1'}
        assert path == 'd1/database/new-preview-d1/query'
        return [{'success': True}]

    monkeypatch.setattr(script, 'api', fake)
    assert script.main() == 0
    assert [path for path, _ in writes] == ['storage/kv/namespaces', 'd1/database', 'd1/database/new-preview-d1/query']
    updated = json.loads(config_path.read_text())
    assert {k: v for k, v in updated.items() if k != 'previews'} == {k: v for k, v in original.items() if k != 'previews'}
