import json
import subprocess

import pytest

import app


@pytest.mark.parametrize("raw,slug", [
    ("https://github.com/sveltejs/svelte", "sveltejs/svelte"),
    ("http://github.com/sveltejs/svelte", "sveltejs/svelte"),
    ("https://github.com/sveltejs/svelte/", "sveltejs/svelte"),
    ("https://github.com/sveltejs/svelte.git", "sveltejs/svelte"),
    ("https://github.com/sveltejs/svelte/tree/main/packages", "sveltejs/svelte"),
    ("sveltejs/svelte", "sveltejs/svelte"),
    ("  sveltejs/svelte  ", "sveltejs/svelte"),
])
def test_parse_repo_slug(raw, slug):
    assert app._parse_repo_slug(raw) == slug


@pytest.mark.parametrize("raw", ["svelte", "", "https://github.com/onlyowner"])
def test_parse_repo_slug_rejects_unparseable(raw):
    with pytest.raises(ValueError):
        app._parse_repo_slug(raw)


@pytest.mark.parametrize("corr,word", [
    (0.61, "strong positive"), (0.6, "moderate positive"), (0.31, "moderate positive"),
    (0.3, "weak positive"), (0.11, "weak positive"), (0.1, "negligible"),
    (0.0, "negligible"), (-0.09, "negligible"), (-0.1, "negative"), (-0.9, "negative"),
])
def test_interpret_corr_bands(corr, word):
    assert app._interpret_corr(corr).startswith(word)


def test_gemini_key_prefers_gemini_over_google(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "g")
    assert app._get_gemini_key() == "g"
    monkeypatch.setenv("GEMINI_API_KEY", "gem")
    assert app._get_gemini_key() == "gem"


def _bws(returncode=0, stdout="", exc=None):
    def run(cmd, **kwargs):
        if exc:
            raise exc
        assert cmd[:3] == ["bws", "secret", "get"]
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")
    return run


def test_gemini_key_falls_back_to_bws(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _bws(stdout=json.dumps({"value": "from-bws"})))
    assert app._get_gemini_key() == "from-bws"


@pytest.mark.parametrize("fake", [
    _bws(returncode=1),
    _bws(stdout="not json"),
    _bws(exc=FileNotFoundError("bws")),
    _bws(exc=subprocess.TimeoutExpired("bws", 5)),
])
def test_gemini_key_none_when_bws_unusable(monkeypatch, fake):
    monkeypatch.setattr(subprocess, "run", fake)
    assert app._get_gemini_key() is None


def _metrics(**over):
    base = {
        "repo": "o/r", "p50_sle": 48, "p85_sle": 96, "spearman_corr": 0.45,
        "recent_stats": {"total": 40, "avg_cycle": 72, "avg_size": 123.4, "flow_efficiency": 21.5,
                         "human_ratio": 0.5, "assisted_ratio": 0.25, "agentic_ratio": 0.25},
        "throughput_data": [{"total": 100}] * 2 + [{"total": 8}] * 8,
        "active_wip_points": [{"num": 1, "title": "t" * 100, "x": "2. Review Queue", "y": 9.0},
                              {"num": 2, "title": "young", "x": "1. Active Coding", "y": 1.0}],
        "stage_percentiles": {"coding": {"p50": 1, "p85": 2}, "review": {"p50": 3, "p85": 4}, "merge": {"p50": 5, "p85": 6}},
        "recent_prs_summary": [{"number": 7, "title": "Add x", "classification": "Human", "cycle_time_hours": 12.0, "pr_size": 30}],
    }
    base.update(over)
    return base


def test_system_prompt_contains_formatted_figures():
    p = app._build_system_prompt(_metrics())
    assert "Repository Being Analyzed: o/r" in p
    assert "**Total PRs analyzed**: 40" in p
    assert "3.0 days (72.0 hours)" in p
    assert "2.0 days" in p and "4.0 days" in p  # p50 / p85 SLE in days
    assert "+0.45" in p and "moderate positive" in p
    assert "Human: 50% | AI-Assisted: 25% | AI-Agentic: 25%" in p
    assert "Coding: 1.0d / 2.0d" in p
    assert "PR#7: Add x | Human | 12.0h | 30 lines" in p


def test_system_prompt_uses_only_last_eight_throughput_weeks():
    p = app._build_system_prompt(_metrics())
    assert "8.0 PRs/week" in p  # the two leading weeks of 100 are ignored


def test_system_prompt_lists_oldest_wip_first_and_truncates_titles():
    p = app._build_system_prompt(_metrics())
    assert p.index("PR#1:") < p.index("PR#2:")
    assert "t" * 60 in p and "t" * 61 not in p


def test_system_prompt_survives_empty_metrics():
    p = app._build_system_prompt({})
    assert "unknown" in p
    assert "No open PRs detected" in p
    assert "No recent PR data available" in p


def test_system_prompt_skips_prs_without_cycle_time():
    m = _metrics(recent_prs_summary=[{"number": 9, "title": "open", "classification": "Human", "cycle_time_hours": None, "pr_size": 1}])
    assert "PR#9" not in app._build_system_prompt(m)
