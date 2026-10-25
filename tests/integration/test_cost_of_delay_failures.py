"""Provider uncertainty and deadlines must not create fabricated zero scores."""
import asyncio

import pytest

import product_value
from tests.integration.test_product_value import DATA, FakeServices, jev_reply


def test_tied_probabilities_choose_lower_fibonacci_level():
    answer = jev_reply()["answers"]["value"]
    answer["probabilities"] = {str(i): .5 if i in (2, 3) else 0 for i in range(6)}
    assert product_value._dimension(answer, product_value.DIMENSIONS["value"])["score"] == 3


@pytest.mark.parametrize("probabilities", [
    {"0": float("nan"), "1": 0, "2": 0, "3": 0, "4": 0, "5": 1},
    {"0": -1, "1": 0, "2": 0, "3": 0, "4": 1, "5": 1},
    {str(i): .1 for i in range(6)},
])
def test_invalid_distribution_is_rejected(probabilities):
    answer = jev_reply()["answers"]["value"]
    answer["probabilities"] = probabilities
    with pytest.raises(ValueError):
        product_value._dimension(answer, product_value.DIMENSIONS["value"])


def test_jev_deadline_preserves_successful_sibling_assessments(monkeypatch):
    fake = FakeServices()

    async def request(url, **kwargs):
        if "typesafe.ai" in url and kwargs["body"]["state"]["pull_request"]["number"] == 2:
            await asyncio.sleep(10)
        return await fake(url, **kwargs)

    monkeypatch.setattr(product_value, "request_json", request)
    monkeypatch.setattr(product_value, "JEV_TIMEOUT", .01)
    result = asyncio.run(product_value.assess_product_value("o/r", DATA, "gemini", jev_key="jev"))
    assert result["assessed"] == 2 and result["failed"] == 1
    failed = next(item for item in result["items"] if item["number"] == 2)
    assert failed["cost_of_delay"] is None and failed["error_code"] == "timeout"


def test_overall_deadline_cancels_active_and_queued_assessments(monkeypatch):
    fake = FakeServices()
    started, cancelled = [], []
    data = {"recent_prs": [{"number": n, "title": f"Change {n}", "body": "Adds a useful capability."} for n in range(1, 9)]}

    async def request(url, **kwargs):
        if "typesafe.ai" in url:
            number = kwargs["body"]["state"]["pull_request"]["number"]
            started.append(number)
            if number != 1:
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    cancelled.append(number)
                    raise
        return await fake(url, **kwargs)

    monkeypatch.setattr(product_value, "request_json", request)
    monkeypatch.setattr(product_value, "CONCURRENCY", 2)
    monkeypatch.setattr(product_value, "ASSESSMENT_TIMEOUT", .05)
    result = asyncio.run(product_value.assess_product_value("o/r", data, "gemini", jev_key="jev"))
    assert result["selected"] == 8 and result["assessed"] == 1 and result["failed"] == 7
    assert [item["number"] for item in result["items"]] == list(range(1, 9))
    assert result["items"][0]["cost_of_delay"] == 29
    assert started == [1, 2, 3] and sorted(cancelled) == [2, 3]
    assert all(item["cost_of_delay"] is None and item["error_code"] == "assessment_deadline"
               for item in result["items"][1:])


def test_context_time_consumes_the_overall_assessment_budget(monkeypatch):
    fake = FakeServices()

    async def request(url, **kwargs):
        if "generativelanguage" in url or "typesafe.ai" in url:
            await asyncio.sleep(.06)
        return await fake(url, **kwargs)

    monkeypatch.setattr(product_value, "request_json", request)
    monkeypatch.setattr(product_value, "ASSESSMENT_TIMEOUT", .1)
    result = asyncio.run(product_value.assess_product_value("o/r", DATA, "gemini", jev_key="jev"))
    assert result["context"] is not None
    assert result["assessed"] == 0 and result["failed"] == 3
    assert all(item["error_code"] == "assessment_deadline" for item in result["items"])


def test_caller_cancellation_also_cleans_up_assessment_tasks(monkeypatch):
    fake = FakeServices()
    started = asyncio.Event()
    cancelled = []

    async def request(url, **kwargs):
        if "typesafe.ai" in url:
            started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.append(kwargs["body"]["state"]["pull_request"]["number"])
                raise
        return await fake(url, **kwargs)

    async def exercise():
        task = asyncio.create_task(product_value.assess_product_value("o/r", DATA, "gemini", jev_key="jev"))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]

    monkeypatch.setattr(product_value, "request_json", request)
    asyncio.run(exercise())
    assert sorted(cancelled) == [1, 2, 3]
