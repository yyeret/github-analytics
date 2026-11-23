"""Unit tests for deterministic operational review insights, slices, and Gemini synthesis."""
import asyncio
import json

import operational_review as review_mod


def _rich_metrics():
    return {
        "repo": "acme/platform",
        "repository_visibility": "public",
        "analysis_id": "snap-1",
        "p50_sle": 12.0,
        "p85_sle": 60.0,
        "spearman_corr": 0.58,
        "recent_stats": {"total": 12, "avg_cycle": 28.0},
        "pre_stats": {"total": 10, "avg_cycle": 48.0},
        "throughput_data": [
            {"week": "2026-01-01", "total": 2},
            {"week": "2026-01-08", "total": 8},
            {"week": "2026-01-15", "total": 9},
            {"week": "2026-01-22", "total": 8},
            {"week": "2026-01-29", "total": 7},
            {"week": "2026-02-05", "total": 4},
            {"week": "2026-02-12", "total": 3},
            {"week": "2026-02-19", "total": 4},
            {"week": "2026-02-26", "total": 3},
            {"week": "2026-03-05", "total": 1},
        ],
        "active_wip_points": [
            {"x": "2. Review Queue", "y": 14.5, "num": 101, "title": "Secret aging PR", "classification": "AI-Agentic", "size": 650},
            {"x": "2. Review Queue", "y": 5.2, "num": 102, "title": "Waiting PR", "classification": "Human", "size": 120},
            {"x": "1. Active Coding", "y": 1.1, "num": 103, "title": "Fresh draft", "classification": "Human", "size": 40},
        ],
        "pr_size_scatter_points": [
            {"x": 30, "y": 6.0, "classification": "Human", "num": 1, "title": "Small fix"},
            {"x": 80, "y": 10.0, "classification": "Human", "num": 2, "title": "Auth tweak"},
            {"x": 150, "y": 12.0, "classification": "Human", "num": 3, "title": "UI polish"},
            {"x": 280, "y": 24.0, "classification": "AI-Assisted", "num": 4, "title": "API helper"},
            {"x": 620, "y": 54.0, "classification": "AI-Agentic", "num": 5, "title": "Large refactor"},
            {"x": 840, "y": 72.0, "classification": "AI-Agentic", "num": 6, "title": "Big migration"},
        ],
        "recent_scatter_points": [
            {"x": 1700000000000, "y": 6.0, "classification": "Human", "num": 1},
            {"x": 1700000000001, "y": 72.0, "classification": "AI-Agentic", "num": 6},
        ],
        "recent_prs_summary": [
            {"number": 6, "title": "Big migration", "cycle_time_hours": 72.0},
            {"number": 5, "title": "Large refactor", "cycle_time_hours": 54.0},
        ],
        "issues": {
            "open_now": 20,
            "open_available": True,
            "closed_available": True,
            "open_truncated": False,
            "net_8w": 12,
            "stale": {
                "count": 8,
                "threshold_days": 90,
                "items": [{"number": 77, "title": "Old secret issue", "days_since_activity": 140}],
            },
            "lifecycle_cfd": {
                "available": True,
                "series": [{"week": "2026-03-01", "stages": {"Triage": 12, "In Progress": 4}}],
            },
        },
        "value_classification": {
            "prs": {
                "total": 6,
                "counts": {"Input": 1, "Activity": 3, "Output": 1, "Outcome": 0, "Impact": 0, "Unknown": 1},
                "items": [{"number": 1, "title": "Small fix", "level": "Activity"}],
            },
            "issues": {"total": 2, "counts": {"Activity": 2}, "items": []},
        },
        "product_value": {
            "status": "ok",
            "assessed": 2,
            "items": [
                {"number": 6, "title": "Big migration", "status": "assessed", "cost_of_delay": 32},
                {"number": 5, "title": "Large refactor", "status": "assessed", "cost_of_delay": 18},
            ],
        },
    }


def test_build_operational_review_ranks_signals_and_featured_slices():
    review = review_mod.build_operational_review(_rich_metrics())
    assert review["synthesis_source"] == "deterministic"
    assert "acme/platform" in review["executive_summary"]
    assert 3 <= len(review["insights"]) <= 5
    assert len(review["featured_slices"]) == 4
    assert len(review["review_questions"]) >= 3

    # Verify all 6 slices are available on rich data and top 4 are marked featured.
    available = [s for s in review["slices"].values() if s["available"]]
    assert len(available) == 6
    assert sum(1 for s in available if s["featured"]) == 4

    # Verify specific signal detections.
    assert "exceed" in review["slices"]["wip_aging"]["headline_metric"]
    assert "500+" in review["slices"]["batch_size"]["insight"]
    assert "AI-Agentic" in review["slices"]["contributor_mode"]["insight"]
    assert review["slices"]["backlog_health"]["rows"][-1]["label"] == "Largest lifecycle stage"
    assert any("Highest expected CoD" in r["label"] for r in review["slices"]["value_framing"]["rows"])

    # Available filters reflect present dimensions.
    assert set(review["available_filters"]["classifications"]) == {"Human", "AI-Assisted", "AI-Agentic"}
    assert review["available_filters"]["size_buckets"] == ["XS (<50)", "S (50-199)", "M (200-499)", "L (500+)"]


def test_build_operational_review_handles_empty_and_rest_collector_without_sizes():
    empty = review_mod.build_operational_review({"repo": "acme/empty"})
    assert empty["featured_slices"] == []
    assert empty["insights"][0]["id"] == "sparse_repository_signal"
    assert not empty["slices"]["batch_size"]["available"]

    # REST collector: PRs present, but pr_size == 0 (unmeasured).
    rest_metrics = {
        "repo": "acme/rest",
        "p50_sle": 20.0,
        "p85_sle": 30.0,
        "recent_stats": {"total": 4},
        "pr_size_scatter_points": [
            {"x": 0, "y": 15.0, "classification": "Human", "num": 1, "title": "A"},
            {"x": 0, "y": 20.0, "classification": "Human", "num": 2, "title": "B"},
            {"x": 0, "y": 25.0, "classification": "Human", "num": 3, "title": "C"},
            {"x": 0, "y": 30.0, "classification": "Human", "num": 4, "title": "D"},
        ],
        "active_wip_points": [
            {"x": "1. Active Coding", "y": 2.0, "num": 9, "title": "Draft", "classification": "Human", "size": 0},
        ],
        "issues": {
            "open_now": 5,
            "open_available": True,
            "closed_available": True,
            "net_8w": -2,
            "stale": {"count": 2, "threshold_days": 90, "items": []},
        },
        "value_classification": {
            "prs": {"total": 2, "counts": {"Outcome": 1, "Output": 1}, "items": [{"number": 1, "title": "A", "level": "Outcome"}]},
        },
    }
    review = review_mod.build_operational_review(rest_metrics)
    assert review["slices"]["batch_size"]["available"] is False
    assert review["available_filters"]["size_buckets"] == []
    assert review["slices"]["flow_predictability"]["available"] is True
    assert review["slices"]["backlog_health"]["available"] is True


def test_public_projection_and_slice_detail_redact_private_titles():
    metrics = _rich_metrics()
    metrics["repository_visibility"] = "private"
    review = review_mod.build_operational_review(metrics)
    metrics["operational_review"] = review

    projected = review_mod.public_operational_review(review, private=True)
    assert projected["private_titles_redacted"] is True
    assert "Secret aging PR" not in json.dumps(projected)
    assert "Old secret issue" not in json.dumps(projected)

    detail_private = review_mod.get_slice_detail(metrics, "wip_aging", private=True)
    assert detail_private["slice"]["id"] == "wip_aging"
    assert detail_private["slice"]["evidence_items"][0]["number"] == 101
    assert "title" not in detail_private["slice"]["evidence_items"][0]

    detail_public = review_mod.get_slice_detail(metrics, "wip_aging", private=False)
    assert detail_public["slice"]["evidence_items"][0]["title"] == "Secret aging PR"

    assert review_mod.get_slice_detail(metrics, "unknown_slice") is None
    assert review_mod.public_operational_review(None) is None


def test_synthesize_operational_review_merges_llm_and_falls_back_cleanly(monkeypatch):
    metrics = _rich_metrics()
    metrics["repository_visibility"] = "private"
    captured_prompts = []

    async def ok_gemini(url, *, method, headers, body, timeout):
        prompt = body["contents"][0]["parts"][0]["text"]
        captured_prompts.append(prompt)
        return 200, {}, {
            "candidates": [{
                "content": {
                    "parts": [{
                        "text": json.dumps({
                            "executive_summary": "AI-sharpened operational brief for acme/platform.",
                            "insights": [{
                                "id": "wip_aging_signal",
                                "title": "Review queue is holding 2 PRs past SLE",
                                "summary": "Two open PRs exceed the 2.5d SLE.",
                                "review_question": "Who owns unblocking #101 and #102 today?",
                            }],
                            "slice_insights": [{
                                "id": "wip_aging",
                                "insight": "Aging is concentrated in review.",
                                "review_question": "Can we cap review queue WIP?",
                            }],
                        })
                    }]
                }
            }]
        }

    monkeypatch.setattr(review_mod, "request_json", ok_gemini)
    synthesized = asyncio.run(review_mod.synthesize_operational_review(metrics, gemini_key="fake-key", allow_llm=True))
    assert synthesized["synthesis_source"] == "llm"
    assert synthesized["executive_summary"] == "AI-sharpened operational brief for acme/platform."
    assert any(i["title"] == "Review queue is holding 2 PRs past SLE" for i in synthesized["insights"])
    assert synthesized["slices"]["wip_aging"]["insight"] == "Aging is concentrated in review."
    # Privacy check: raw private titles must NOT be sent to Gemini synthesis prompt.
    assert "Secret aging PR" not in captured_prompts[0]
    assert "Old secret issue" not in captured_prompts[0]

    # No key or allow_llm=False -> deterministic.
    det = asyncio.run(review_mod.synthesize_operational_review(metrics, gemini_key=None))
    assert det["synthesis_source"] == "deterministic"
    det2 = asyncio.run(review_mod.synthesize_operational_review(metrics, gemini_key="fake-key", allow_llm=False))
    assert det2["synthesis_source"] == "deterministic"

    # Provider error -> graceful fallback to deterministic.
    async def failing_gemini(*args, **kwargs):
        return 502, {}, {}

    monkeypatch.setattr(review_mod, "request_json", failing_gemini)
    fallback = asyncio.run(review_mod.synthesize_operational_review(metrics, gemini_key="fake-key"))
    assert fallback["synthesis_source"] == "deterministic"

    # Exception -> graceful fallback.
    async def raising_gemini(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(review_mod, "request_json", raising_gemini)
    fallback2 = asyncio.run(review_mod.synthesize_operational_review(metrics, gemini_key="fake-key"))
    assert fallback2["synthesis_source"] == "deterministic"
