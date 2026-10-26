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


@pytest.mark.parametrize("corruption", ["stale_fingerprint", "duplicate_case", "unselected_case"])
def test_live_resume_rejects_invalid_completed_records_before_calls(tmp_path, corruption):
    cases = [case("one"), case("two")]
    contexts = {"org/repo": {"product_summary": "API"}}
    output = tmp_path / "checkpoint.json"

    async def assessor(*args):
        return scored()

    asyncio.run(run_cases(cases, contexts, "r1", output, "key", assessor, budget=1))
    checkpoint = json.loads(output.read_text())
    record = checkpoint["records"][0]
    if corruption == "stale_fingerprint":
        record["fingerprint"] = "an old description/context/rubric fingerprint"
    elif corruption == "duplicate_case":
        checkpoint["records"].append(dict(record))
        checkpoint["attempts"] = 2
    else:
        record["case_id"] = "not-selected"
    output.write_text(json.dumps(checkpoint))
    saved = output.read_bytes()

    async def unexpected_assessor(*args):
        pytest.fail("Invalid resumed records must fail before a provider call")

    with pytest.raises(ValueError, match="checkpoint|fingerprint"):
        asyncio.run(run_cases(cases, contexts, "r1", output, "key", unexpected_assessor,
                              budget=3, resume=True))
    assert output.read_bytes() == saved


def test_budget_is_hard_capped_and_missing_key_never_calls(tmp_path):
    async def assessor(*args):
        pytest.fail("Invalid configuration must fail before calling provider")

    with pytest.raises(ValueError, match="60"):
        asyncio.run(run_cases([case()], {"org/repo": {}}, "r", tmp_path / "out", "key", assessor, budget=61))
    with pytest.raises(ValueError, match="key"):
        asyncio.run(run_cases([case()], {"org/repo": {}}, "r", tmp_path / "out", "", assessor, budget=1))


@pytest.mark.parametrize("changed_input", ["body", "context"])
def test_offline_cli_rejects_stale_inputs_even_with_same_case_id(tmp_path, changed_input):
    from scripts.evaluate_value_intent import main

    cases = [case()]
    contexts = {"org/repo": {"product_summary": "Original product"}}
    output, dataset, overrides = (tmp_path / name for name in ("result.json", "dataset.json", "contexts.json"))

    async def assessor(*args):
        return scored()

    asyncio.run(run_cases(cases, contexts, "r1", output, "key", assessor, budget=1))
    dataset.write_text(json.dumps({"cases": cases, "controls": [], "contexts": contexts}))
    args = ["--dataset", str(dataset), "--results", str(output)]
    assert main(args) == 0
    if changed_input == "body":
        cases[0]["body"] = "A different change with the same ID"
        dataset.write_text(json.dumps({"cases": cases, "controls": [], "contexts": contexts}))
    else:
        overrides.write_text(json.dumps({"org/repo": {"product_summary": "A different product"}}))
        args += ["--contexts", str(overrides)]
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2
