"""Evaluator mechanics, not model accuracy: no live calls or model-score assertions."""
import asyncio
import json

import pytest

from scripts.evaluate_value_intent import evaluate, fingerprint, run_cases


def case(name="example", assessable=True, partition="development"):
    return {"id": name, "repository": "org/repo", "number": 1, "title": name,
            "body": "Add two-factor authentication.", "partition": partition,
            "expected": {"assessable": assessable,
                         "dimensions": {key: [3, 5] for key in
                                        ("value", "time_criticality", "opportunity_runway", "risk_reduction")}}}


def scored(value=3):
    return {"status": "assessed", "cost_of_delay": value * 4,
            "dimensions": {key: {"score": value} for key in
                           ("value", "time_criticality", "opportunity_runway", "risk_reduction")}}


def test_metrics_distinguish_provider_failure_abstention_and_missing():
    cases = [case("ok"), case("far", partition="held_out"), case("blank", False),
             case("network"), case("missing")]
    records = [{"case_id": "ok", "result": scored()},
               {"case_id": "far", "result": scored(13)},
               {"case_id": "blank", "result": {"status": "failed", "error_code": "insufficient_context"}},
               {"case_id": "network", "result": {"status": "failed", "error_code": "provider_http_error"}}]
    result = evaluate(cases, records)
    assert result["overall"]["missing"] == 1
    assert result["overall"]["provider_failures"] == 1
    assert result["overall"]["correct_abstentions"] == 1
    assert result["overall"]["within_allowed_dimensions"] == 4
    assert result["overall"]["coarse_disagreement_dimensions"] == 4
    assert result["overall"]["compared_dimensions"] == 8
    assert result["partitions"]["held_out"]["coarse_disagreement_dimensions"] == 4


def test_fingerprint_covers_context_body_and_rubric_but_not_reference_labels():
    original = case()
    context = {"product_summary": "API framework"}
    initial = fingerprint(original, context, "rubric1")
    original["expected"]["assessable"] = False
    assert fingerprint(original, context, "rubric1") == initial
    assert fingerprint(original, context, "rubric2") != initial
    assert fingerprint(original, {"product_summary": "CLI"}, "rubric1") != initial
    original["body"] += " with recovery codes"
    assert fingerprint(original, context, "rubric1") != initial


def test_live_budget_resume_and_stale_input_are_checked_before_calls(tmp_path):
    calls = []

    async def assessor(key, context, item, pr):
        calls.append((item["number"], pr["body"]))
        return scored()

    cases = [case("one"), case("two")]
    contexts = {"org/repo": {"product_summary": "API"}}
    output = tmp_path / "checkpoint.json"
    asyncio.run(run_cases(cases, contexts, "r1", output, "not-a-real-key", assessor, budget=1))
    assert len(calls) == 1
    saved = json.loads(output.read_text())
    assert saved["attempts"] == 1
    assert len(saved["records"]) == 1
    asyncio.run(run_cases(cases, contexts, "r1", output, "not-a-real-key", assessor, budget=2, resume=True))
    assert len(calls) == 2
    cases[0]["body"] = "Changed input"
    with pytest.raises(ValueError, match="fingerprint"):
        asyncio.run(run_cases(cases, contexts, "r1", output, "not-a-real-key", assessor, budget=3, resume=True))
    assert len(calls) == 2
    assert "not-a-real-key" not in output.read_text()


def test_truncation_metadata_does_not_invent_missing_evidence(tmp_path):
    item = case()
    item.update(body="a" * 1500, body_truncated=True, original_body_chars=5000)
    bodies = []

    async def assessor(key, context, metadata, pr):
        bodies.append(pr["body"])
        return scored()

    asyncio.run(run_cases([item], {"org/repo": {}}, "r", tmp_path / "out.json", "key", assessor, budget=1))
    assert bodies == ["a" * 1500 + " "]


def test_budget_is_hard_capped_and_missing_key_never_calls(tmp_path):
    async def assessor(*args):
        pytest.fail("Invalid configuration must fail before calling provider")

    with pytest.raises(ValueError, match="60"):
        asyncio.run(run_cases([case()], {"org/repo": {}}, "r", tmp_path / "out", "key", assessor, budget=61))
    with pytest.raises(ValueError, match="key"):
        asyncio.run(run_cases([case()], {"org/repo": {}}, "r", tmp_path / "out", "", assessor, budget=1))
