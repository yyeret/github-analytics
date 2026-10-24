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
    assert failed["cost_of_delay"] is None and failed["reason"] == "assessment_unavailable"
