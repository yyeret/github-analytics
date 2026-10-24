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
            {"number": 1, "title": "Users can export reports faster", "body": "Adds CSV export.", "additions": 40, "deletions": 2},
            {"number": 2, "title": "Bump lint config", "body": None, "additions": 1, "deletions": 1}],
        "open_prs": [{"number": 3, "title": "Fix cycle time for draft PRs", "body": "Drafts skewed p85."}]}


def gemini_reply(answer):
    return 200, {}, {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(answer)}]}}]}


class FakeServices:
    """Answers GitHub and Gemini requests; records each Gemini prompt."""

    def __init__(self, assessments=None, context_status=200, readme="# Flow analytics\nFor teams.",
                 context=CONTEXT, private=False):
        self.assessments = assessments or {}
        self.context_status = context_status
        self.readme = readme
        self.context = context
        self.private = private
        self.prompts = []
        self.urls = []
        self.readme_headers = None

    async def __call__(self, url, *, headers, method="GET", body=None, timeout=15):
        self.urls.append(url)
        if url.endswith("/readme"):
            self.readme_headers = headers
            if self.readme is None:
                return 403, {}, {"message": "API rate limit exceeded"}
            return 200, {}, {"encoding": "base64", "content": base64.b64encode(self.readme.encode()).decode()}
        if url.startswith("https://api.github.com/repos/"):
            return 200, {}, {"description": "Public flow analytics", "topics": ["dora"], "homepage": "",
                             "private": self.private}
        prompt = body["contents"][0]["parts"][0]["text"]
        self.prompts.append(prompt)
        if "## Pull request #" not in prompt:
            return (self.context_status, {}, {}) if self.context_status != 200 else gemini_reply(self.context)
        number = int(prompt.split("## Pull request #")[1].split(" ")[0])
        answer = self.assessments.get(number)
        if answer is None:
            return 500, {}, {"message": "boom"}
        return gemini_reply(answer)


def assessment(value, **extra):
    return {"expected_value": value, "value_summary": "Faster exports.", "beneficiaries": "Engineering leads",
            "why_it_matters": "Supports adoption.", "strategic_goal": "Hosted dashboard adoption",
            "confidence": "Medium", "missing_context": "Usage data.", **extra}


@pytest.fixture
def services(monkeypatch):
    def install(**kwargs):
        fake = FakeServices(**kwargs)
        monkeypatch.setattr(product_value, "request_json", fake)
        return fake
    return install


def run(data=DATA, key="k", **kwargs):
    return asyncio.run(product_value.assess_product_value("o/r", data, key, **kwargs))


def test_assesses_each_sampled_pr_against_generated_product_context(services):
    fake = services(assessments={1: assessment("High"), 2: assessment("Low"), 3: assessment("Medium")})
    result = run()
    assert result["status"] == "ok"
    assert result["context"]["strategic_goals"][0] == "Trustworthy flow metrics"
    assert result["counts"] == {"High": 1, "Medium": 1, "Low": 1, "Unclear": 0}
    assert result["selected"] == 3 and result["total_prs"] == 3
    assert [(i["number"], i["cohort"], i["expected_value"]) for i in result["items"]] == [
        (1, "merged", "High"), (2, "merged", "Low"), (3, "open", "Medium")]
    assert "realized" in result["disclaimer"]
    # One context call built from README and description, then one call per PR that sees that context.
    assert "# Flow analytics" in fake.prompts[0] and "Public flow analytics" in fake.prompts[0]
    assert len(fake.prompts) == 4
    assert all("Trustworthy flow metrics" in prompt for prompt in fake.prompts[1:])


def test_framing_is_evidence_not_value(services):
    fake = services(assessments={1: assessment("Low"), 2: assessment("Low"), 3: assessment("Low")})
    result = run()
    pr1 = next(i for i in result["items"] if i["number"] == 1)
    assert pr1["framing"] == "Outcome" and pr1["expected_value"] == "Low"
    assert "framing, not value" in fake.prompts[1]


def test_one_failed_pr_does_not_lose_the_others(services):
    services(assessments={1: assessment("High"), 3: assessment("Banana", confidence="Total")})
    result = run()
    by_number = {i["number"]: i for i in result["items"]}
    assert by_number[2]["status"] == "failed" and "expected_value" not in by_number[2]
    assert by_number[3]["expected_value"] == "Unclear" and by_number[3]["confidence"] == "Low"
    assert result["assessed"] == 2 and result["failed"] == 1


def test_without_key_nothing_is_called(services):
    fake = services()
    result = run(key=None)
    assert result["status"] == "unavailable" and result["items"] == []
    assert fake.prompts == []


def test_context_failure_skips_pr_assessment(services):
    fake = services(context_status=503)
    result = run()
    assert result["status"] == "failed" and "context" in result["reason"]
    assert len(fake.prompts) == 1


@pytest.mark.parametrize("context", [["not", "an", "object"], {**CONTEXT, "lean_canvas": "text"}])
def test_malformed_context_fails_soft_or_is_normalized(services, context):
    services(context=context, assessments={n: assessment("Low") for n in (1, 2, 3)})
    result = run()
    if isinstance(context, list):
        assert result["status"] == "failed" and "context" in result["reason"]
    else:
        assert result["status"] == "ok" and result["context"]["lean_canvas"] == {}


def test_private_repository_sends_nothing_to_gemini(services):
    fake = services(private=True)
    result = run()
    assert result["status"] == "failed"
    assert fake.prompts == [] and not any(url.endswith("/readme") for url in fake.urls)


def test_readme_is_fetched_without_the_token_and_skipped_when_refused(services):
    fake = services(readme=None)
    result = run(token="secret")
    assert "Authorization" not in fake.readme_headers
    assert result["status"] == "ok" and result["sources"]["readme"] is False
    assert "(no README)" in fake.prompts[0]


def test_unresponsive_gemini_times_out_as_failed(monkeypatch):
    async def hang(url, **_):
        if "generativelanguage" in url:
            await asyncio.sleep(10)
        return 200, {}, {"private": False}

    monkeypatch.setattr(product_value, "request_json", hang)
    monkeypatch.setattr(product_value, "GEMINI_TIMEOUT", 0.01)
    assert run()["status"] == "failed"


def test_empty_reasoning_is_downgraded_to_unclear(services):
    services(assessments={n: assessment("High", value_summary="", confidence="High") for n in (1, 2, 3)})
    item = run()["items"][0]
    assert (item["expected_value"], item["confidence"]) == ("Unclear", "Low")
    assert item["missing_context"]


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

    async def fake_assess(repo, raw, key, token):
        seen["args"] = (repo, raw, key)
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
                          GEMINI_API_KEY="k", ANALYTICS_CACHE=cache)
    response = hosted_client(env).post("/api/analyze", json={"repo_url": "o/r"})
    assert response.status_code == 200
    assert response.json()["product_value"]["status"] == value["status"]
    assert seen["args"] == ("o/r", {"repo": "o/r"}, "k")
    assert cache.stored[hosted.METRICS_CACHE_PREFIX + "o/r"]["product_value"]["status"] == value["status"]
    assert cache.ttls[-1] == ttl


def _events(caplog):
    return [json.loads(r.message) for r in caplog.records if r.name in ("product_value", "github_api")]


def test_truncated_context_retries_once_then_assesses_prs(monkeypatch, caplog):
    fake = FakeServices(assessments={n: assessment("High") for n in (1, 2, 3)})
    budgets = []

    async def upstream(url, **kwargs):
        body = kwargs.get("body")
        if body and "## Pull request #" not in body["contents"][0]["parts"][0]["text"]:
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
        if kwargs.get("body"):
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
        if kwargs.get("body"):
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
        if kwargs.get("body"):
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
    services(assessments={1: assessment("High"), 3: assessment("Low")})
    with caplog.at_level("INFO"):
        result = run()
    events = _events(caplog)
    failure = next(e for e in events if e.get("stage") == "pr_assessment" and e.get("error_code"))
    assert failure["pr_number"] == 2 and failure["cohort"] == "merged"
    assert result["assessed"] == 2 and result["failed"] == 1
    assert result["items"][1]["error_code"] == "provider_http_error"
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
        if kwargs.get('body'):
            raise httpx.ReadTimeout('exception-secret-sentinel')
        return await fake(url, **kwargs)

    monkeypatch.setattr(product_value, 'request_json', upstream)
    result = run()
    assert result['error_code'] == 'timeout'
    assert 'exception-secret-sentinel' not in json.dumps(result) + caplog.text


def test_retry_cannot_reset_the_shared_context_deadline(monkeypatch, services):
    fake = services()
    calls = []
    clock = [0.0]

    async def upstream(url, **kwargs):
        if kwargs.get('body'):
            calls.append(url)
            clock[0] += 6
            for _ in range(3):
                await asyncio.sleep(0)
            if len(calls) == 1:
                return 200, {}, {'candidates': [{'finishReason': 'MAX_TOKENS'}]}
        return await fake(url, **kwargs)

    async def scenario():
        loop = asyncio.get_running_loop()
        clock[0] = loop.time()
        with monkeypatch.context() as controlled:
            controlled.setattr(loop, 'time', lambda: clock[0])
            return await product_value.assess_product_value('o/r', DATA, 'k')

    monkeypatch.setattr(product_value, 'request_json', upstream)
    monkeypatch.setattr(product_value, 'CONTEXT_TIMEOUT', 10)
    result = asyncio.run(scenario())
    assert result['error_code'] == 'timeout' and len(calls) == 2
