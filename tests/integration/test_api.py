import sys

import pytest
from fastapi.testclient import TestClient

import app as app_module
from tests.factories import FakeGenai

SAMPLE_METRICS = {"repo": "o/r", "recent_stats": {"total": 3}, "p50_sle": 24, "p85_sle": 48}


@pytest.fixture
def client():
    app_module._metrics_cache.clear()
    yield TestClient(app_module.app)
    app_module._metrics_cache.clear()


@pytest.fixture
def fake_genai(monkeypatch):
    import google

    def install(**kwargs):
        fake = FakeGenai(**kwargs)
        monkeypatch.setitem(sys.modules, "google.generativeai", fake)
        monkeypatch.setattr(google, "generativeai", fake, raising=False)
        return fake
    return install


@pytest.fixture
def analyzed(client, monkeypatch):
    seen = {}

    def fake_run(repo, force):
        seen["args"] = (repo, force)
        return dict(SAMPLE_METRICS, repo=repo)

    monkeypatch.setattr(app_module, "_run_analysis", fake_run)
    return seen


# --- /api/analyze -----------------------------------------------------------

def test_analyze_returns_metrics_and_caches_by_slug(client, analyzed):
    r = client.post("/api/analyze", json={"repo_url": "https://github.com/o/r.git"})
    assert r.status_code == 200
    assert r.json()["repo"] == "o/r"
    assert app_module._metrics_cache["o/r"]["recent_stats"]["total"] == 3
    assert analyzed["args"] == ("o/r", False)


def test_analyze_passes_force_refresh(client, analyzed):
    client.post("/api/analyze", json={"repo_url": "o/r", "force_refresh": True})
    assert analyzed["args"] == ("o/r", True)


def test_analyze_bad_url_is_400(client, analyzed):
    r = client.post("/api/analyze", json={"repo_url": "nonsense"})
    assert r.status_code == 400
    assert "Cannot parse repo slug" in r.json()["detail"]
    assert "args" not in analyzed


def test_analyze_failure_is_500_and_not_cached(client, monkeypatch):
    def boom(repo, force):
        raise RuntimeError("gh exploded")
    monkeypatch.setattr(app_module, "_run_analysis", boom)
    r = client.post("/api/analyze", json={"repo_url": "o/r"})
    assert r.status_code == 500
    assert "Analysis failed: gh exploded" in r.json()["detail"]
    assert app_module._metrics_cache == {}


def test_analyze_missing_field_is_422(client):
    assert client.post("/api/analyze", json={}).status_code == 422


# --- /api/chat --------------------------------------------------------------

def test_chat_before_analysis_is_404(client):
    r = client.post("/api/chat", json={"repo": "o/r", "message": "hi"})
    assert r.status_code == 404
    assert "analyze the repository first" in r.json()["detail"]


def test_chat_without_api_key_is_503(client, monkeypatch):
    app_module._metrics_cache["o/r"] = SAMPLE_METRICS
    monkeypatch.setattr(app_module, "_get_gemini_key", lambda: None)
    r = client.post("/api/chat", json={"repo": "o/r", "message": "hi"})
    assert r.status_code == 503
    assert "GEMINI_API_KEY" in r.json()["detail"]


def test_chat_success_maps_history_and_injects_system_prompt(client, monkeypatch, fake_genai):
    app_module._metrics_cache["o/r"] = SAMPLE_METRICS
    monkeypatch.setenv("GEMINI_API_KEY", "k-123")
    fake = fake_genai(reply="Cut WIP.")
    r = client.post("/api/chat", json={
        "repo": "o/r", "message": "what next?",
        "history": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi there"}],
    })
    assert r.status_code == 200
    assert r.json() == {"reply": "Cut WIP."}
    assert fake.api_key == "k-123"
    assert fake.sent == "what next?"
    assert fake.history == [{"role": "user", "parts": ["hello"]}, {"role": "model", "parts": ["hi there"]}]
    assert "Repository Being Analyzed: o/r" in fake.model_kwargs["system_instruction"]


def test_chat_model_error_is_500(client, monkeypatch, fake_genai):
    app_module._metrics_cache["o/r"] = SAMPLE_METRICS
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    fake_genai(error=RuntimeError("quota"))
    r = client.post("/api/chat", json={"repo": "o/r", "message": "x"})
    assert r.status_code == 500
    assert "AI error: quota" in r.json()["detail"]


def test_chat_validation_is_422(client):
    assert client.post("/api/chat", json={"repo": "o/r"}).status_code == 422


# --- / ----------------------------------------------------------------------

def test_root_serves_index_html(client, tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<h1>hello spa</h1>", encoding="utf-8")
    monkeypatch.setattr(app_module, "STATIC_DIR", tmp_path)
    r = client.get("/")
    assert r.status_code == 200
    assert "hello spa" in r.text


def test_root_is_404_when_index_missing(client, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "STATIC_DIR", tmp_path)
    r = client.get("/")
    assert r.status_code == 404
    assert "index.html not found" in r.text


def test_real_index_html_is_served(client):
    # The repo ships index.html next to app.py; the default STATIC_DIR must find it.
    r = client.get("/")
    assert r.status_code == 200
    assert "<html" in r.text.lower()
