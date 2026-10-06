"""Guards the workflow file against a malformed edit; the real proof is the workflow running."""
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


@pytest.fixture(scope="module")
def wf():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_triggers_on_pull_request_and_push_to_main(wf):
    on = wf.get("on", wf.get(True))  # YAML 1.1 parses a bare `on` key as True
    assert "pull_request" in on
    assert on["push"]["branches"] == ["main"]


def test_least_privilege_permissions(wf):
    assert wf["permissions"] == {"contents": "read"}


def test_runs_lint_and_tests_on_a_python_matrix(wf):
    job = wf["jobs"]["test"]
    assert len(job["strategy"]["matrix"]["python-version"]) >= 2
    steps = " ".join(str(s.get("run", "")) for s in job["steps"])
    assert "ruff check" in steps
    assert "pytest" in steps
    assert "requirements-dev.txt" in steps


def test_cancels_superseded_runs(wf):
    assert wf["concurrency"]["cancel-in-progress"] is True


def test_no_step_can_be_silently_skipped_or_ignored(wf):
    for step in wf["jobs"]["test"]["steps"]:
        assert "continue-on-error" not in step, step
    job = wf["jobs"]["test"]
    assert "continue-on-error" not in job
    for step in job["steps"]:
        if "pytest" in str(step.get("run", "")) or "ruff" in str(step.get("run", "")):
            assert "if" not in step, step


def test_coverage_floor_is_enforced_by_config():
    import tomllib
    cfg = tomllib.loads((WORKFLOW.parents[2] / "pyproject.toml").read_text(encoding="utf-8"))
    assert cfg["tool"]["coverage"]["report"]["fail_under"] >= 90
    assert "--cov" in str(WORKFLOW.read_text(encoding="utf-8"))
