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
    {"number": 2, "title": "Bump lint config", "body": "Routine lint update."}],
    "open_prs": [{"number": 3, "title": "Fix cycle time for draft PRs", "body": "Drafts skewed p85."}]}
DIMS = ("value", "time_criticality", "opportunity_runway", "risk_reduction")


def jev_reply(scores=(3, 5, 8, 13)):
    scale = [1, 2, 3, 5, 8, 13]
    return {"model": "jev-1.14.0", "answers": {"intent_assessable": {"type": "noul", "noul": .95}, **{
        name: {"type": "score", "score": scale.index(score), "confidence": .8,
               "legend": {str(i): "rubric" for i in range(6)},
               "probabilities": {str(i): float(i == scale.index(score)) for i in range(6)}}
        for name, score in zip(DIMS, scores)}}, "usage": {"input_tokens": 500, "output_tokens": 20}}


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
        return self.context_status, {}, {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(self.context)}]}}]}


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


@pytest.mark.parametrize("probability", [.05, .5, .69])
def test_indeterminate_nonempty_description_has_no_numeric_contribution(services, probability):
    answer = jev_reply()
    answer["answers"]["intent_assessable"]["noul"] = probability
    services(assessments={1: answer})
    result = run(data={"recent_prs": [{"number": 1, "body": "Various improvements and fixes."}]})
    item = result["items"][0]
    assert item["status"] == "failed" and item["error_code"] == "insufficient_context"
    assert item["cost_of_delay"] is None and "dimensions" not in item
    assert "empty" not in item["reason"]


@pytest.mark.parametrize("description", ["Add 2FA.", "Run chaos experiments against recovery paths.", "Parallelize CI tests."])
def test_inferable_intent_needs_no_explicit_business_case(services, description):
    fake = services()
    item = run(data={"recent_prs": [{"number": 1, "body": description}]})["items"][0]
    assert item["status"] == "assessed" and item["cost_of_delay"] == 29
    assert item["intent_assessability"] == .95
    assert "intent_assessable" in fake.jev_requests[0]["questions"]
    assert item["body_truncated"] is False


@pytest.mark.parametrize("answer", [None, {"type": "score", "noul": .99}, {"type": "noul", "noul": True}, {"type": "noul", "noul": float("nan")}])
def test_malformed_assessability_does_not_turn_into_a_score(services, answer):
    reply = jev_reply()
    reply["answers"]["intent_assessable"] = answer
    services(assessments={1: reply})
    item = run()["items"][0]
    assert item["status"] == "failed" and item["error_code"] == "invalid_response"
    assert item["cost_of_delay"] is None


@pytest.mark.parametrize("failure", [429, {"answers": {}}, {"answers": {"value": {"type": "score"}}}])
def test_failed_or_malformed_answer_is_not_a_zero_score(services, failure):
    services(assessments={2: failure})
    result = run()
    bad = next(i for i in result["items"] if i["number"] == 2)
    assert bad["status"] == "failed" and bad["cost_of_delay"] is None
    assert "provider-secret-sentinel" not in json.dumps(result)
    assert result["failed"] == 1 and result["assessed"] == 2


@pytest.mark.parametrize("probabilities", [
    {**jev_reply()["answers"]["value"]["probabilities"], "6": .1},
    {**jev_reply()["answers"]["value"]["probabilities"], "provider-secret-sentinel": 0},
    {str(i): float(i == 2) for i in range(5)},
    [0, 0, 1, 0, 0, 0],
], ids=["extra-level", "extra-metadata", "missing-level", "not-an-object"])
def test_distribution_requires_exactly_the_supported_buckets(services, probabilities):
    answer = jev_reply()
    answer["answers"]["value"]["probabilities"] = probabilities
    services(assessments={2: answer})
    result = run()
    bad = next(item for item in result["items"] if item["number"] == 2)
    assert bad["status"] == "failed" and bad["cost_of_delay"] is None
    assert bad["error_code"] == "invalid_response"
    assert "dimensions" not in bad
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


def test_jev_uses_only_the_description_prefix_and_product_context(services):
    fake = services()
    description = 'Useful change. ' * 150
    data = {'recent_prs': [{'number': 7, 'title': 'title-secret-sentinel',
                           'body': description + 'tail-secret-sentinel',
                           'additions': 9000, 'deletions': 8000}]}
    result = run(data=data)
    assert result['assessed'] == 1
    state = fake.jev_requests[0]['state']
    assert state['pull_request']['body'] == description[:1500]
    assert state['pull_request']['body_truncated'] is True
    assert state['repository']['strategic_goals'] == CONTEXT['strategic_goals']
    assert all(value not in json.dumps(state) for value in ('title-secret-sentinel', 'tail-secret-sentinel', '9000', '8000'))
    assert result['items'][0]['title'] == 'title-secret-sentinel'


@pytest.mark.parametrize('description', [None, '', '   '])
def test_empty_description_is_unassessed_without_a_provider_call(services, description):
    fake = services()
    result = run(data={'recent_prs': [{'number': 7, 'title': 'Urgent critical security fix', 'body': description}]})
    assert not fake.jev_requests
    item = result['items'][0]
    assert item['status'] == 'failed' and item['error_code'] == 'insufficient_context'
    assert item['cost_of_delay'] is None and item['reason']


def _events(caplog):
    return [json.loads(r.message) for r in caplog.records if r.name in ("product_value", "github_api")]


def test_truncated_context_retries_once_then_assesses_prs(monkeypatch, caplog):
    fake = FakeServices()
    budgets = []

    async def upstream(url, **kwargs):
        body = kwargs.get("body")
        if "generativelanguage" in url:
            budgets.append(body["generationConfig"]["maxOutputTokens"])
            if len(budgets) == 1:
                return 200, {}, {"candidates": [{"finishReason": "MAX_TOKENS", "content": {
                    "parts": [{"text": '{"product_summary":"private-body-sentinel'}]}}],
                    "usageMetadata": {"promptTokenCount": 3302, "candidatesTokenCount": 4096,
                                      "untrusted": "secret-sentinel"}}
        return await fake(url, **kwargs)

    monkeypatch.setattr(product_value, "request_json", upstream)
    with caplog.at_level("INFO"):
        result = run(key="key-sentinel")
    assert result["status"] == "ok" and result["assessed"] == 3
    assert budgets == [4096, 8192]
    events = _events(caplog)
    failed = next(e for e in events if e.get("error_code") == "output_truncated")
    assert failed["finish_reason"] == "MAX_TOKENS" and failed["http_status"] == 200
    assert failed["usage"]["candidatesTokenCount"] == 4096
    assert failed["stage"] == "product_context" and failed["attempt"] == 1
    assert all(e["analysis_id"] == result["analysis_id"] for e in events)
    assert all("elapsed_ms" in e for e in events)
    assert all(secret not in caplog.text for secret in ("key-sentinel", "private-body-sentinel", "secret-sentinel"))


@pytest.mark.parametrize("text", ['{"product_summary":', json.dumps(CONTEXT)])
def test_repeated_truncation_is_explicit_and_bounded(monkeypatch, services, text, caplog):
    fake = services()
    context_calls = []

    async def upstream(url, **kwargs):
        if "generativelanguage" in url:
            context_calls.append(kwargs["body"])
            return 200, {}, {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": text}]}}]}
        return await fake(url, **kwargs)

    monkeypatch.setattr(product_value, "request_json", upstream)
    result = run()
    assert result["status"] == "failed" and result["error_code"] == "output_truncated"
    assert "truncated" in result["reason"].lower()
    assert result["items"] == [] and len(context_calls) == 2
    assert len([e for e in _events(caplog) if e.get("event") == "product_value_failed"]) == 1


@pytest.mark.parametrize("reply,code", [
    ((429, {}, {"error": "provider-secret-sentinel"}), "provider_http_error"),
    ((200, {}, {"promptFeedback": {"blockReason": "SAFETY"}}), "blocked_response"),
    ((200, {}, {"candidates": [{"finishReason": "SAFETY", "content": {"parts": [{"text": json.dumps(CONTEXT)}]}}]}), "blocked_response"),
    ((200, {}, {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "provider-secret-sentinel"}]}}]}), "invalid_response"),
    ((200, {}, {"candidates": "provider-secret-sentinel"}), "invalid_response"),
])
def test_non_truncation_errors_are_specific_safe_and_not_retried(monkeypatch, services, caplog, reply, code):
    fake = services()
    calls = []

    async def upstream(url, **kwargs):
        if "generativelanguage" in url:
            calls.append(url)
            return reply
        return await fake(url, **kwargs)

    monkeypatch.setattr(product_value, "request_json", upstream)
    result = run()
    assert result["status"] == "failed" and result["error_code"] == code
    assert len(calls) == 1
    assert "provider-secret-sentinel" not in json.dumps(result) + caplog.text
    event = next(e for e in _events(caplog) if e.get("event") == "gemini_generation")
    assert event["error_code"] == code and event["stage"] == "product_context"


def test_context_retry_shares_one_deadline(monkeypatch, services, caplog):
    fake = services()
    calls = []

    async def upstream(url, **kwargs):
        if "generativelanguage" in url:
            calls.append(url)
            if len(calls) == 1:
                await asyncio.sleep(.02)
                return 200, {}, {"candidates": [{"finishReason": "MAX_TOKENS"}]}
            await asyncio.sleep(10)
        return await fake(url, **kwargs)

    monkeypatch.setattr(product_value, "request_json", upstream)
    monkeypatch.setattr(product_value, "CONTEXT_TIMEOUT", .04)
    result = run()
    assert result["error_code"] == "timeout" and len(calls) == 2
    assert any(e.get("error_code") == "timeout" and e["stage"] == "product_context" for e in _events(caplog))


def test_pr_failure_logs_identify_the_pr_without_its_contents(services, caplog):
    services(assessments={2: 429})
    with caplog.at_level("INFO"):
        result = run()
    events = _events(caplog)
    failure = next(e for e in events if e.get("stage") == "pr_assessment" and e.get("error_code"))
    assert failure["pr_number"] == 2 and failure["cohort"] == "merged"
    assert result["assessed"] == 2 and result["failed"] == 1
    assert result["items"][1]["error_code"] == "rate_limited"
    assert not any(key in failure for key in ("prompt", "body", "headers", "response", "title"))


def test_source_failure_is_distinguished_from_model_failure(monkeypatch, caplog):
    async def failing(*_, **__):
        raise ValueError("source-secret-sentinel")

    monkeypatch.setattr(product_value, "fetch_product_sources", failing)
    result = run()
    assert result["error_code"] == "source_unavailable"
    event = next(e for e in _events(caplog) if e.get("event") == "product_value_failed")
    assert event["stage"] == "product_sources"
    assert "source-secret-sentinel" not in json.dumps(result) + caplog.text


def test_local_transport_timeout_is_reported_as_timeout(monkeypatch, services, caplog):
    import httpx
    fake = services()

    async def upstream(url, **kwargs):
        if "generativelanguage" in url:
            raise httpx.ReadTimeout('exception-secret-sentinel')
        return await fake(url, **kwargs)

    monkeypatch.setattr(product_value, 'request_json', upstream)
    result = run()
    assert result['error_code'] == 'timeout'
    assert 'exception-secret-sentinel' not in json.dumps(result) + caplog.text


def test_retry_cannot_reset_the_shared_context_deadline(monkeypatch, services):
    import selectors
    fake = services()
    calls = []
    clock = [0.0]

    class ClockSelector(selectors.SelectSelector):
        def select(self, timeout=None):
            assert timeout is not None, "the context operation must always have a deadline"
            clock[0] += timeout
            return super().select(0)

    async def upstream(url, **kwargs):
        if "generativelanguage" in url:
            calls.append(url)
            await asyncio.sleep(6)
            if len(calls) == 1:
                return 200, {}, {'candidates': [{'finishReason': 'MAX_TOKENS'}]}
        return await fake(url, **kwargs)

    monkeypatch.setattr(product_value, 'request_json', upstream)
    monkeypatch.setattr(product_value, 'CONTEXT_TIMEOUT', 10)
    loop = asyncio.SelectorEventLoop(selector=ClockSelector())
    try:
        with monkeypatch.context() as controlled:
            controlled.setattr(loop, 'time', lambda: clock[0])
            result = loop.run_until_complete(product_value.assess_product_value('o/r', DATA, 'k', jev_key='j'))
    finally:
        loop.close()
    assert result['error_code'] == 'timeout' and len(calls) == 2


def test_unconfigured_result_exposes_the_logged_error_code(services):
    services()
    result = run(key=None)
    assert result['status'] == 'unavailable' and result['error_code'] == 'not_configured'
    assert result['reason']


def test_failed_pr_has_a_safe_explanation(services):
    services(assessments={n: 503 for n in (1, 2, 3)})
    result = run()
    assert all(item['reason'] and item['error_code'] == 'provider_http_error' for item in result['items'])


@pytest.mark.parametrize('waiting_for', ['headers', 'body'])
@pytest.mark.parametrize('deadline', ['request', 'context'])
def test_worker_deadlines_abort_fetch_and_body_reads(monkeypatch, waiting_for, deadline):
    import sys
    from types import ModuleType

    controller = SimpleNamespace(signal=object(), aborted=False)

    def abort():
        controller.aborted = True

    controller.abort = abort
    seen = []

    async def stalled():
        await asyncio.Event().wait()

    async def body():
        if waiting_for == 'body':
            await stalled()
        return '{}'

    async def fetch(url, options):
        seen.append(options)
        if waiting_for == 'headers':
            await stalled()
        return SimpleNamespace(status=200, headers=SimpleNamespace(get=lambda *args: ''), text=body)

    js = ModuleType('js')
    js.Object = SimpleNamespace(fromEntries=object())
    js.AbortController = SimpleNamespace(new=lambda: controller)
    js.fetch = fetch
    ffi = ModuleType('pyodide.ffi')
    ffi.to_js = lambda value, **_: value
    monkeypatch.setitem(sys.modules, 'js', js)
    monkeypatch.setitem(sys.modules, 'pyodide.ffi', ffi)
    monkeypatch.setattr(product_value, 'GEMINI_TIMEOUT', .01 if deadline == 'request' else 1)
    monkeypatch.setattr(product_value, 'CONTEXT_TIMEOUT', .01 if deadline == 'context' else 1)
    expected = product_value.GenerationError if deadline == 'request' else TimeoutError
    with pytest.raises(expected) as failure:
        asyncio.run(product_value.generate_context('key', 'prompt', {'stage': 'product_context'}))
    if deadline == 'request':
        assert failure.value.code == 'timeout'
    assert controller.aborted and len(seen) == 1
    assert seen[0]['signal'] is controller.signal
