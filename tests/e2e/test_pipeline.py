"""Whole pipeline with only the outermost boundaries faked: the `gh` CLI and Gemini."""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from freezegun import freeze_time

import analyze
import app as app_module
from tests.factories import FakeGenai, FakeGh, graphql_response, small_raw_data

REPO_ROOT = Path(__file__).resolve().parents[2]
WATCHED = ("raw_data.json", "dashboard.html", "index.html", "analyze.py", "app.py")


def _embedded(name, html):
    """Parse the JSON literal assigned to `const <name> = ...;` in the generated dashboard."""
    m = re.search(rf"const {name} = (.*?);\n", html)
    assert m, f"{name} not found in dashboard"
    return json.loads(m.group(1))


def _digest():
    out = {}
    for name in WATCHED:
        p = REPO_ROOT / name
        out[name] = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
    return out


@freeze_time("2024-03-01T00:00:00")
def test_collect_analyze_chat_and_dashboard(monkeypatch):
    raw = small_raw_data("acme/widgets")
    monkeypatch.setattr(subprocess, "run", FakeGh([
        graphql_response(raw["pre_ai_prs"]),
        graphql_response(raw["recent_prs"]),
        graphql_response(raw["open_prs"]),
    ]))
    fake = FakeGenai(reply="Reduce WIP.")
    import google
    monkeypatch.setitem(sys.modules, "google.generativeai", fake)
    monkeypatch.setattr(google, "generativeai", fake, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    app_module._metrics_cache.clear()
    before = _digest()

    client = TestClient(app_module.app)

    # 1. analyze: real collect_data + compute_flow_metrics
    r = client.post("/api/analyze", json={"repo_url": "https://github.com/acme/widgets"})
    assert r.status_code == 200
    m = r.json()
    assert m["repo"] == "acme/widgets"
    assert m["recent_stats"]["total"] == 4
    assert m["pre_stats"]["total"] == 1
    stats = m["recent_stats"]
    assert stats["human_ratio"] == pytest.approx(0.5)      # 101 and 104
    assert stats["assisted_ratio"] == pytest.approx(0.25)  # 102: three commits < 120s apart
    assert stats["agentic_ratio"] == pytest.approx(0.25)   # 103: dependabot
    assert {p["num"] for p in m["active_wip_points"]} == {201, 202}
    assert len(json.load(open(analyze.RAW_DATA_FILE))["recent_prs"]) == 4

    # 2. chat uses what analyze cached
    r = client.post("/api/chat", json={"repo": "acme/widgets", "message": "Where is my bottleneck?"})
    assert r.json() == {"reply": "Reduce WIP."}
    assert "acme/widgets" in fake.model_kwargs["system_instruction"]
    assert "**Total PRs analyzed**: 4" in fake.model_kwargs["system_instruction"]

    # 3. dashboard from the cache file written in step 1
    analyze.analyze_and_build_report(analyze.load_or_collect("acme/widgets"))
    html = open(analyze.DASHBOARD_FILE, encoding="utf-8").read()
    for canvas in ("cfdChart", "scatterChart", "activeWipAgeChart", "sizeCorrelationChart", "throughputChart", "prCometChartCanvas"):
        assert f'id="{canvas}"' in html
    assert "acme/widgets" in html
    # the embedded data blobs carry the analysed PRs, not empty placeholders
    assert len(_embedded("recentPRs", html)) == 4
    assert len(_embedded("cometRawData", html)) == 4
    assert {p["num"] for p in _embedded("activeWipData", html)} == {201, 202}
    assert len(_embedded("throughputData", html)) == 9
    assert len(_embedded("cfdMerged", html)) == 10

    # 4. nothing leaked into the repository checkout
    assert _digest() == before
    app_module._metrics_cache.clear()
