from fastapi.testclient import TestClient

import app
from tests.factories import small_raw_data


def cacheable_raw(repo):
    data = small_raw_data(repo)
    data["issues"] = {"open": [], "closed": [], "pre_ai": []}
    return data


def test_api_reuses_repository_after_memory_reset(monkeypatch):
    calls = []

    def collect(repo, **kwargs):
        calls.append(repo)
        return cacheable_raw(repo)

    monkeypatch.setattr(app, "collect_data", collect)
    app._metrics_cache.clear()
    client = TestClient(app.app)

    first = client.post("/api/analyze", json={"repo_url": "https://github.com/Owner/Alpha"})
    assert first.status_code == 200
    assert first.json()["repo"] == "owner/alpha"
    assert first.json()["cache_hit"] is False
    app._metrics_cache.clear()

    second = client.post("/api/analyze", json={"repo_url": "owner/alpha"})
    assert second.status_code == 200
    assert second.json()["cache_hit"] is True
    assert calls == ["owner/alpha"]
    assert app._metrics_cache["owner/alpha"]["repo"] == "owner/alpha"
    no_scheme = client.post("/api/analyze", json={"repo_url": "github.com/OWNER/ALPHA"})
    assert no_scheme.json()["cache_hit"] is True
    assert calls == ["owner/alpha"]


def test_api_keeps_repositories_separate_and_refreshes_one(monkeypatch):
    calls = []

    def collect(repo, **kwargs):
        calls.append(repo)
        data = cacheable_raw(repo)
        data["captured_at"] = f"capture-{len(calls)}"
        return data

    monkeypatch.setattr(app, "collect_data", collect)
    client = TestClient(app.app)
    for repo in ("owner/alpha", "owner/beta"):
        assert client.post("/api/analyze", json={"repo_url": repo}).status_code == 200

    refreshed = client.post("/api/analyze", json={"repo_url": "owner/alpha", "force_refresh": True})
    assert refreshed.status_code == 200
    assert refreshed.json()["captured_at"] == "capture-3"
    assert refreshed.json()["cache_hit"] is False
    beta = client.post("/api/analyze", json={"repo_url": "owner/beta"})
    assert beta.json()["captured_at"] == "capture-2"
    assert beta.json()["cache_hit"] is True
    assert calls == ["owner/alpha", "owner/beta", "owner/alpha"]


def test_failed_refresh_does_not_replace_cache(monkeypatch):
    def collect(repo, **kwargs):
        return cacheable_raw(repo)

    monkeypatch.setattr(app, "collect_data", collect)
    client = TestClient(app.app)
    assert client.post("/api/analyze", json={"repo_url": "owner/alpha"}).status_code == 200

    def fail(repo, **kwargs):
        raise RuntimeError("GitHub unavailable")

    monkeypatch.setattr(app, "collect_data", fail)
    failed = client.post("/api/analyze", json={"repo_url": "owner/alpha", "force_refresh": True})
    assert failed.status_code == 500
    cached = client.post("/api/analyze", json={"repo_url": "owner/alpha"})
    assert cached.status_code == 200
    assert cached.json()["cache_hit"] is True


def test_api_rejects_unsafe_repository_slug():
    client = TestClient(app.app)
    response = client.post("/api/analyze", json={"repo_url": "owner/../other"})
    assert response.status_code == 400


def test_issue_collection_failure_keeps_prior_cache(monkeypatch):
    monkeypatch.setattr(app, "collect_data", lambda repo, **kwargs: cacheable_raw(repo))
    client = TestClient(app.app)
    assert client.post("/api/analyze", json={"repo_url": "owner/alpha"}).status_code == 200

    monkeypatch.setattr(app, "collect_data", lambda repo, **kwargs: small_raw_data(repo))
    partial = client.post("/api/analyze", json={"repo_url": "owner/alpha", "force_refresh": True})
    assert partial.status_code == 200
    assert partial.json()["cache_hit"] is False
    cached = client.post("/api/analyze", json={"repo_url": "owner/alpha"})
    assert cached.status_code == 200
    assert cached.json()["cache_hit"] is True
    assert cached.json()["issues"] is not None
