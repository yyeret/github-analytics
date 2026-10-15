import asyncio
import json
from types import SimpleNamespace

from starlette.requests import Request

import cloudflare_app


class FakeKv:
    def __init__(self):
        self.values = {}
        self.writes = []

    async def get(self, key):
        return self.values.get(key)

    async def put(self, key, value, **options):
        self.values[key] = value
        self.writes.append((key, options))


def request_with_cache(cache):
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/analyze",
        "scheme": "https",
        "server": ("example.test", 443),
        "headers": [],
        "query_string": b"",
        "env": SimpleNamespace(ANALYTICS_CACHE=cache),
    })


def test_hosted_repository_cache_survives_revisit_and_refresh(monkeypatch):
    cache = FakeKv()
    collected = []
    checked = []

    async def public_repo_exists(repo, token):
        checked.append(repo)

    async def collect_public_data(repo, token):
        collected.append(repo)
        return {"repo": repo, "captured_at": f"capture-{len(collected)}"}

    def compute_flow_metrics(raw):
        return {**raw, "recent_stats": {"total": 1}}

    monkeypatch.setattr(cloudflare_app, "public_repo_exists", public_repo_exists)
    monkeypatch.setattr(cloudflare_app, "collect_public_data", collect_public_data)
    monkeypatch.setattr(cloudflare_app, "compute_flow_metrics", compute_flow_metrics)

    async def scenario():
        request = request_with_cache(cache)
        first = await cloudflare_app.analyze_repo(
            cloudflare_app.AnalyzeRequest(repo_url="owner/alpha"), request
        )
        reopened = await cloudflare_app.analyze_repo(
            cloudflare_app.AnalyzeRequest(repo_url="owner/alpha"), request
        )
        beta = await cloudflare_app.analyze_repo(
            cloudflare_app.AnalyzeRequest(repo_url="owner/beta"), request
        )
        refreshed = await cloudflare_app.analyze_repo(
            cloudflare_app.AnalyzeRequest(repo_url="owner/alpha", force_refresh=True), request
        )
        after_refresh = await cloudflare_app.analyze_repo(
            cloudflare_app.AnalyzeRequest(repo_url="owner/alpha"), request
        )
        return first, reopened, beta, refreshed, after_refresh

    first, reopened, beta, refreshed, after_refresh = asyncio.run(scenario())
    assert first["cache_hit"] is False
    assert reopened["cache_hit"] is True
    assert reopened["captured_at"] == first["captured_at"]
    assert beta["cache_hit"] is False
    assert refreshed["cache_hit"] is False
    assert after_refresh["cache_hit"] is True
    assert after_refresh["captured_at"] == "capture-3"
    assert collected == ["owner/alpha", "owner/beta", "owner/alpha"]
    assert checked == ["owner/alpha", "owner/alpha", "owner/beta", "owner/alpha", "owner/alpha"]
    assert all(options == {} for _, options in cache.writes)
    assert json.loads(cache.values["metrics:v2:owner/alpha"])["captured_at"] == "capture-3"
