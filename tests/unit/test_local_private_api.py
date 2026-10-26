import asyncio

from fastapi.testclient import TestClient

from local_private_api import LocalLimit, create_application


def test_local_api_requires_a_verified_identity():
    client = TestClient(create_application(), base_url='http://localhost')
    assert client.get('/api/health').json() == {'ok': True}
    response = client.post('/api/issues/value', json={'repo_url': 'o/r', 'allow_private': True})
    assert response.status_code == 401


def test_local_limit_bounds_the_same_client():
    async def exercise():
        limit = LocalLimit(2)
        return [await limit.limit({'key': 'client'}) for _ in range(3)]
    assert [result['success'] for result in asyncio.run(exercise())] == [True, True, False]


def test_study_cli_preserves_interrupted_evidence(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    from types import SimpleNamespace
    import pytest
    spec = importlib.util.spec_from_file_location('issue_study_cli', Path(__file__).resolve().parents[2] / 'scripts/analyze_issue_value.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = tmp_path / 'interrupted'
    output.mkdir()
    receipt = output / 'provider-calls.json'
    receipt.write_text('original receipt')
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout='caller-token'))
    args = SimpleNamespace(output=output, repo='o/r', limit=1, owner_context=None, owner_profile=None, issue_sample=None)
    with pytest.raises(ValueError, match='fresh output directory'):
        asyncio.run(module.analyze(args))
    assert receipt.read_text() == 'original receipt'
    assert not (output / 'request.json').exists()
