import asyncio
import base64
import json
from types import SimpleNamespace

import pytest

import product_value
from tests.integration.test_security_boundaries import hosted, hosted_client

CONTEXT = {"product_summary": "Flow analytics for GitHub repositories.",
           "value_proposition": "Coaches teams to deliver faster.",
           "lean_canvas": {"customer_segments": "Engineering leads"},
           "strategic_goals": ["Trustworthy flow metrics", "Hosted dashboard adoption"],
           "missing_context": "No pricing information."}
DATA = {"recent_prs": [
    {"number": 1, "title": "Users can export reports faster", "body": "Adds CSV export."},
    {"number": 2, "title": "Bump lint config", "body": None}],
    "open_prs": [{"number": 3, "title": "Fix cycle time for draft PRs", "body": "Drafts skewed p85."}]}
DIMS = ("value", "time_criticality", "opportunity_runway", "risk_reduction")


def jev_reply(scores=(3, 5, 8, 13)):
    scale = [1, 2, 3, 5, 8, 13]
    return {"model": "jev-1.14.0", "answers": {
        name: {"type": "score", "score": scale.index(score), "confidence": .8,
               "legend": {str(i): "rubric" for i in range(6)},
               "probabilities": {str(i): float(i == scale.index(score)) for i in range(6)}}
        for name, score in zip(DIMS, scores)}, "usage": {"input_tokens": 500, "output_tokens": 20}}


class FakeServices:
    def __init__(self, assessments=None, context_status=200, readme="# Flow analytics", context=CONTEXT, private=False):
        self.assessments = assessments or {}
        self.context_status, self.readme, self.context, self.private = context_status, readme, context, private
        self.prompts, self.jev_requests, self.urls = [], [], []
        self.readme_headers = None

    async def __call__(self, url, *, headers, method="GET", body=None, timeout=15):
        self.urls.append(url)
        if url.endswith("/readme"):
            self.readme_headers = headers
            if self.readme is None:
                return 403, {}, {}
            return 200, {}, {"encoding": "base64", "content": base64.b64encode(self.readme.encode()).decode()}
        if url.startswith("https://api.github.com/repos/"):
            return 200, {}, {"description": "Public flow analytics", "topics": ["dora"], "private": self.private}
        if "typesafe.ai" in url:
            self.jev_requests.append(body)
            number = body["state"]["pull_request"]["number"]
            answer = self.assessments.get(number, jev_reply())
            if isinstance(answer, int):
                return answer, {}, {"error": "provider-secret-sentinel"}
            return 200, {}, answer
        self.prompts.append(body["contents"][0]["parts"][0]["text"])
        return self.context_status, {}, {"candidates": [{"content": {"parts": [{"text": json.dumps(self.context)}]}}]}


@pytest.fixture
def services(monkeypatch):
    def install(**kwargs):
        fake = FakeServices(**kwargs)
        monkeypatch.setattr(product_value, "request_json", fake)
        return fake
    return install


def run(data=DATA, key="gemini", **kwargs):
    kwargs.setdefault("jev_key", "jev")
    return asyncio.run(product_value.assess_product_value("o/r", data, key, **kwargs))


def test_four_fibonacci_dimensions_are_classified_by_jev_and_summed_in_code(services):
    fake = services()
    result = run()
    assert result["status"] == "ok"
    assert result["scale"] == [1, 2, 3, 5, 8, 13]
    assert result["selected"] == result["assessed"] == 3
    assert len(fake.prompts) == 1 and len(fake.jev_requests) == 3
    for item in result["items"]:
        assert [item["dimensions"][name]["score"] for name in DIMS] == [3, 5, 8, 13]
        assert item["cost_of_delay"] == 29
        assert item["model"] == "jev-1.14.0"
    state = fake.jev_requests[0]["state"]
    assert state["repository"]["strategic_goals"] == CONTEXT["strategic_goals"]
    assert "opportunity_runway" in fake.jev_requests[0]["questions"]
    assert "realized" in result["disclaimer"]


def test_uses_most_probable_level_not_rounded_average_or_confidence(services):
    answer = jev_reply()
    answer["answers"]["value"].update(score=2.5, confidence=.2,
        probabilities={"0": .49, "1": 0, "2": 0, "3": 0, "4": 0, "5": .51})
    services(assessments={1: answer})
    item = run()["items"][0]
    assert item["dimensions"]["value"]["score"] == 13
    assert item["dimensions"]["value"]["confidence"] == .2
    assert item["dimensions"]["value"]["probabilities"]["13"] == .51


@pytest.mark.parametrize("failure", [429, {"answers": {}}, {"answers": {"value": {"type": "score"}}}])
def test_failed_or_malformed_answer_is_not_a_zero_score(services, failure):
    services(assessments={2: failure})
    result = run()
    bad = next(i for i in result["items"] if i["number"] == 2)
    assert bad["status"] == "failed" and bad["cost_of_delay"] is None
    assert "provider-secret-sentinel" not in json.dumps(result)
    assert result["failed"] == 1 and result["assessed"] == 2


def test_missing_key_sends_nothing(services):
    fake = services()
    assert run(jev_key=None)["status"] == "unavailable"
    assert run(key=None)["status"] == "unavailable"
    assert fake.urls == []


def test_context_failure_sends_nothing_to_jev(services):
    fake = services(context_status=429)
    assert run()["status"] == "failed"
    assert not fake.jev_requests


@pytest.mark.parametrize("context", [["bad"], {**CONTEXT, "strategic_goals": []},
                                     {**CONTEXT, "value_proposition": "draft " * 500}])
def test_unusable_context_does_not_become_strategic_evidence(services, context):
    fake = services(context=context)
    assert run()["status"] == "failed"
    assert not fake.jev_requests


def test_private_repository_sends_nothing_to_models(services):
    fake = services(private=True)
    assert run()["status"] == "failed"
    assert not fake.prompts and not fake.jev_requests


def test_readme_is_unauthenticated_and_can_fall_back_to_metadata(services):
    fake = services(readme=None)
    result = run(token="secret")
    assert "Authorization" not in fake.readme_headers
    assert result["status"] == "ok" and result["sources"]["readme"] is False


def test_provider_deadline_is_enforced(monkeypatch):
    async def hang(url, **_):
        if "generativelanguage" in url:
            await asyncio.sleep(10)
        return 200, {}, {"private": False}
    monkeypatch.setattr(product_value, "request_json", hang)
    monkeypatch.setattr(product_value, "GEMINI_TIMEOUT", .01)
    assert run()["status"] == "failed"


def test_selection_is_most_recent_and_pairs_each_cohort_with_its_own_record():
    data = {"recent_prs": [{"number": 1, "title": "Old", "mergedAt": "2026-01-01T00:00:00Z", "body": "merged"},
                           {"number": 2, "title": "New", "mergedAt": "2026-09-01T00:00:00Z"}],
            "open_prs": [{"number": 1, "title": "Old", "createdAt": "2026-10-01T00:00:00Z", "body": "open"}]}
    selected = product_value.select_prs(data, product_value.compute_value_classification(data))
    assert [(item["number"], item["cohort"]) for item, _ in selected] == [(2, "merged"), (1, "merged")]
    assert selected[1][1]["body"] == "merged"


def test_sample_is_capped_and_keeps_open_prs():
    data = {"recent_prs": [{"number": n, "title": f"PR {n}"} for n in range(1, 41)],
            "open_prs": [{"number": n, "title": f"PR {n}"} for n in range(100, 120)]}
    selected = product_value.select_prs(data, product_value.compute_value_classification(data), max_prs=30)
    cohorts = [item["cohort"] for item, _ in selected]
    assert len(selected) == 30 and cohorts.count("open") == 10


@pytest.mark.parametrize("value, ttl", [({"status": "ok", "items": []}, 21600),
                                        ({"status": "failed", "items": []}, 600),
                                        ({"status": "ok", "items": [], "failed": 2}, 600)])
def test_hosted_analysis_attaches_product_value_before_caching(monkeypatch, value, ttl):
    async def allowed(_):
        return {"success": True}

    async def public_metadata(*_, **__):
        return 200, {}, {"private": False}

    async def collected(repo, token):
        return {"repo": repo}

    seen = {}

    async def fake_assess(repo, raw, key, token, *, jev_key=None):
        seen["args"] = (repo, raw, key, jev_key)
        return value

    class Cache:
        stored = {}
        ttls = []

        async def get(self, _):
            return None

        async def put(self, key, value, expiration_ttl):
            self.stored[key] = json.loads(value)
            self.ttls.append(expiration_ttl)

    monkeypatch.setattr(hosted, "request_json", public_metadata)
    monkeypatch.setattr(hosted, "collect_public_data", collected)
    monkeypatch.setattr(hosted, "compute_flow_metrics", lambda raw: {"repo": raw["repo"], "recent_stats": {"total": 1}})
    monkeypatch.setattr(hosted, "assess_product_value", fake_assess)
    cache = Cache()
    env = SimpleNamespace(AUTH_REQUIRED="false", ANALYZE_LIMIT=SimpleNamespace(limit=allowed),
                          GEMINI_API_KEY="k", TYPESAFE_API_KEY="j", ANALYTICS_CACHE=cache)
    response = hosted_client(env).post("/api/analyze", json={"repo_url": "o/r"})
    assert response.status_code == 200
    assert response.json()["product_value"]["status"] == value["status"]
    assert seen["args"] == ("o/r", {"repo": "o/r"}, "k", "j")
    assert cache.stored[hosted.METRICS_CACHE_PREFIX + "o/r"]["product_value"]["status"] == value["status"]
    assert cache.ttls[-1] == ttl


@pytest.mark.parametrize("supplied", [True, False])
def test_caller_keys_override_server_defaults_and_bypass_shared_cache(monkeypatch, supplied):
    seen, cache_calls = [], []
    async def allowed(_): return {"success": True}
    async def public(*_, **__): return 200, {}, {"private": False}
    async def collect(repo, token): return {"repo": repo}
    async def assess(repo, raw, key, token, *, jev_key=None):
        seen.append((key, jev_key))
        return {"status": "ok", "items": []}
    class Cache:
        async def get(self, key):
            cache_calls.append("read")
            return json.dumps({"cached": True})
        async def put(self, *a, **kw): cache_calls.append("write")
    monkeypatch.setattr(hosted, "request_json", public)
    monkeypatch.setattr(hosted, "collect_public_data", collect)
    monkeypatch.setattr(hosted, "compute_flow_metrics", lambda raw: {"repo": "o/r", "recent_stats": {"total": 1}})
    monkeypatch.setattr(hosted, "assess_product_value", assess)
    env = SimpleNamespace(AUTH_REQUIRED="false", ANALYZE_LIMIT=SimpleNamespace(limit=allowed),
                          GEMINI_API_KEY="server-g", TYPESAFE_API_KEY="server-j", ANALYTICS_CACHE=Cache())
    headers = {"X-Gemini-Api-Key": "caller-g", "X-TypeSafe-Api-Key": "caller-j"} if supplied else {}
    response = hosted_client(env).post("/api/analyze", headers=headers, json={"repo_url": "o/r"})
    assert response.status_code == 200
    assert cache_calls == ([] if supplied else ["read"])
    assert seen == ([("caller-g", "caller-j")] if supplied else [])
    assert all(secret not in response.text for secret in ["caller-g", "caller-j", "server-g", "server-j"])


def test_local_api_accepts_ephemeral_provider_keys(monkeypatch):
    import subprocess
    from fastapi.testclient import TestClient
    import app as local
    seen = []
    async def assess(repo, raw, key, token, *, jev_key=None):
        seen.append((key, jev_key))
        return {"status": "ok", "items": []}
    monkeypatch.setenv("GEMINI_API_KEY", "server-g")
    monkeypatch.setenv("TYPESAFE_API_KEY", "server-j")
    monkeypatch.setattr(local.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0, stdout="false"))
    monkeypatch.setattr(local, "collect_data", lambda repo: {"repo": repo})
    monkeypatch.setattr(local, "compute_flow_metrics", lambda raw: raw)
    monkeypatch.setattr(local, "assess_product_value", assess)
    client = TestClient(local.app, base_url="http://localhost")
    r = client.post("/api/analyze", json={"repo_url": "o/r"},
                    headers={"X-Gemini-Api-Key": "caller-g", "X-TypeSafe-Api-Key": "caller-j"})
    assert r.status_code == 200 and seen == [("caller-g", "caller-j")]
    assert "caller-g" not in r.text and "caller-j" not in r.text
    assert local.os.environ["GEMINI_API_KEY"] == "server-g"
    assert local.os.environ["TYPESAFE_API_KEY"] == "server-j"
