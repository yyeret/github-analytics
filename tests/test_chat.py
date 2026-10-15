import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

from cloudflare_app import ChatRequest, chat, chat_prompt


METRICS = {
    "repo": "github/spec-kit",
    "recent_stats": {"total": 400, "avg_cycle": 48, "avg_size": 80, "flow_efficiency": 12},
    "p50_sle": 24, "p85_sle": 96, "spearman_corr": 0.3,
    "stage_percentiles": {"review": {"p85": 3.5}},
    "upstream_breakdown": {"review_wait": 72, "coding": 20},
    "throughput_data": [
        {"week": f"2026-09-{day:02d}", "total": count}
        for day, count in enumerate(range(1, 15), start=1)
    ],
    "active_wip_points": [{"x": "2. Review Queue", "y": 15, "num": 42}],
    "recent_prs_summary": [{"number": 42, "title": "Fix flow", "cycle_time_hours": 48}],
    "large_chart_data": "x" * 200_000,
}


class FakeCache:
    def __init__(self, metrics):
        self.metrics = metrics
        self.keys = []

    async def get(self, key):
        self.keys.append(key)
        return json.dumps(self.metrics) if self.metrics is not None else None


class FakeAIResponse:
    is_success = True

    def json(self):
        return {"candidates": [{"content": {"parts": [{"text": "Throughput rose."}]}}]}


class FakeAIClient:
    def __init__(self):
        self.body = None
        self.url = None
        self.headers = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def post(self, url, **kwargs):
        self.url = url
        self.headers = kwargs["headers"]
        self.body = kwargs["json"]
        return FakeAIResponse()


class ChatTests(unittest.IsolatedAsyncioTestCase):
    def test_prompt_uses_compact_relevant_metrics(self):
        prompt = chat_prompt(METRICS)
        self.assertIn("throughput", prompt.lower())
        self.assertIn("review", prompt.lower())
        self.assertIn("github/spec-kit", prompt)
        self.assertNotIn("large_chart_data", prompt)
        self.assertLess(len(prompt), 20_000)

    def test_throughput_omits_partial_edge_weeks(self):
        metrics = {**METRICS, "throughput_data": [
            {"week": "first-partial", "total": 1},
            {"week": "complete-1", "total": 8},
            {"week": "complete-2", "total": 10},
            {"week": "current-partial", "total": 2},
        ]}
        prompt = chat_prompt(metrics)
        self.assertIn("complete-1", prompt)
        self.assertIn("complete-2", prompt)
        self.assertNotIn('"week":"first-partial"', prompt)
        self.assertNotIn('"week":"current-partial"', prompt)

    async def test_chat_reads_cached_metrics_without_client_payload(self):
        req = ChatRequest(repo="github/spec-kit", message="How is throughput trending?")
        cache = FakeCache(METRICS)
        env = SimpleNamespace(AUTH_REQUIRED="false", ANALYTICS_CACHE=cache, GEMINI_API_KEY="test",
                              CHAT_LIMIT=SimpleNamespace(limit=AsyncMock(return_value={"success": True})))
        fake_request = SimpleNamespace(scope={"env": env}, headers={})
        client = FakeAIClient()
        with patch("cloudflare_app.httpx.AsyncClient", return_value=client):
            result = await chat(req, fake_request)
        self.assertEqual(result["reply"], "Throughput rose.")
        self.assertEqual(cache.keys, ["metrics:v2:github/spec-kit"])
        self.assertIn("throughput", client.body["system_instruction"]["parts"][0]["text"].lower())
        self.assertIn("gemini-3.5-flash-lite", client.url)
        self.assertEqual(client.headers["x-goog-api-key"], "test")


if __name__ == "__main__":
    unittest.main()
