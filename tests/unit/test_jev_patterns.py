"""Unit tests for Jev insight pattern catalog and outcome-framing upgrade."""
import asyncio

import jev_patterns


def _metrics():
    return {
        "recent_stats": {"total": 4, "p50_days": 2.0, "p85_days": 6.0},
        "stage_percentiles": {"coding": {"p50": 1.0, "p85": 2.0}, "review": {"p50": 3.0, "p85": 5.0}},
        "open_wip": [{"num": 10, "x": "In Review", "y": 9.0}],
        "issues": {"open_count": 2, "linked_pr_count": 1},
        "value_classification": {
            "levels": ["Input", "Activity", "Output", "Outcome", "Impact", "Unknown"],
            "method": "rules",
            "prs": {
                "total": 2,
                "body_available": 2,
                "defaulted": 0,
                "counts": {"Input": 0, "Activity": 2, "Output": 0, "Outcome": 0, "Impact": 0, "Unknown": 0},
                "items": [
                    {"number": 1, "title": "FY26Q4 cleanup", "cohort": "merged", "level": "Activity", "rationale": "Quarterly bucket"},
                    {"number": 2, "title": "Speed up checkout", "cohort": "open", "level": "Activity", "rationale": "Rule"},
                ],
            },
            "issues": None,
        },
    }


def _data():
    return {
        "recent_prs": [{"number": 1, "title": "FY26Q4 cleanup", "body": "Refactor module"}],
        "open_prs": [{"number": 2, "title": "Speed up checkout", "body": "Cuts checkout latency by 40%."}],
        "issues": {"open": [{"number": 9, "title": "Fix login bug", "body": "Users cannot sign in."}], "closed": []},
    }


def test_patterns_unavailable_without_jev_key():
    metrics = _metrics()
    result = asyncio.run(jev_patterns.analyze_patterns_and_framing("o/r", _data(), metrics, jev_key=None))
    assert result["status"] == "unavailable"
    assert len(result["flow_patterns"]) == len(jev_patterns.FLOW_PATTERNS)
    assert len(result["item_patterns"]) == len(jev_patterns.ITEM_PATTERNS)
    assert metrics["value_classification"]["method"] == "rules"


def test_patterns_and_framing_upgrade_with_jev(monkeypatch):
    async def fake_request(url, timeout, **kwargs):
        body = kwargs["body"]
        if "flow_metrics" in body["state"]:
            return 200, {}, {"answers": {
                "review_queue_bottleneck": {"type": "noul", "noul": 0.88},
                "wip_aging_tail_risk": {"type": "noul", "noul": 0.4},
                "output_without_outcome_bias": {"type": "noul", "noul": 0.72},
                "unlinked_work_stream": {"type": "noul", "noul": 0.2},
            }}
        num = body["state"]["work_item"]["number"]
        return 200, {}, {"answers": {
            "activity_without_outcome": {"type": "noul", "noul": 0.9 if num == 1 else 0.1},
            "vague_scope_or_ac": {"type": "noul", "noul": 0.2},
            "multi_concern_batch": {"type": "noul", "noul": 0.1},
            "preventive_risk_or_debt": {"type": "noul", "noul": 0.75 if num == 1 else 0.1},
            "customer_friction_fix": {"type": "noul", "noul": 0.85 if num in (2, 9) else 0.1},
            "outcome_framing": {"type": "score", "probabilities": {"0": 0, "1": 0, "2": 0.1, "3": 0.85, "4": 0.05}},
        }}

    monkeypatch.setattr(jev_patterns, "request_json", fake_request)
    metrics = _metrics()
    result = asyncio.run(jev_patterns.analyze_patterns_and_framing("o/r", _data(), metrics, jev_key="jev"))
    assert result["status"] == "ok" and result["assessed_items"] == 3
    matched_flow = {p["id"] for p in result["flow_patterns"] if p["matched"]}
    assert matched_flow == {"review_queue_bottleneck", "output_without_outcome_bias"}
    friction = next(p for p in result["item_patterns"] if p["id"] == "customer_friction_fix")
    assert friction["matched_count"] == 2
    assert metrics["value_classification"]["method"] == "jev"
    items = {i["number"]: i for i in metrics["value_classification"]["prs"]["items"]}
    assert items[1]["level"] == "Activity"  # QUARTERLY_BUCKET_RE guardrail preserved
    assert items[2]["level"] == "Outcome"


def test_patterns_provider_error_degrades_gracefully(monkeypatch):
    async def fail_request(url, timeout, **kwargs):
        return 503, {}, {"error": "down"}

    monkeypatch.setattr(jev_patterns, "request_json", fail_request)
    metrics = _metrics()
    result = asyncio.run(jev_patterns.analyze_patterns_and_framing("o/r", _data(), metrics, jev_key="jev"))
    assert result["status"] == "ok" and result["assessed_items"] == 0
    assert metrics["value_classification"]["method"] == "rules"


def test_app_pattern_prompt_block_formats_matched_patterns():
    import app
    assert app._build_pattern_block(None) == ""
    assert app._build_pattern_block({"status": "ok", "flow_patterns": [], "item_patterns": []}) == ""
    block = app._build_pattern_block({
        "status": "ok",
        "flow_patterns": [{"label": "Review queue bottleneck", "matched": True, "probability": 0.88, "coaching_hint": "Lower WIP"}],
        "item_patterns": [{"label": "Activity without outcome", "matched_count": 1, "sampled_count": 2,
                           "matched_items": [{"number": 1}], "coaching_hint": "Reframe"}],
    })
    assert "Review queue bottleneck" in block and "#1" in block
