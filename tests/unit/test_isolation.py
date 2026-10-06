import os
import subprocess

import pytest

from tests.factories import make_pr


def test_real_subprocess_is_blocked(blocked_calls):
    with pytest.raises(AssertionError, match="real subprocess"):
        subprocess.run(["gh", "auth", "status"])
    assert blocked_calls == [["gh", "auth", "status"]]
    blocked_calls.clear()  # deliberate trigger; otherwise teardown fails the test


def test_swallowed_blocked_call_is_reported_at_teardown(blocked_calls):
    try:
        subprocess.run(["bws", "secret", "get", "X"])
    except Exception:
        pass  # what production code does
    assert blocked_calls  # teardown would fail this test if we did not clear it
    blocked_calls.clear()


def test_api_keys_cleared_and_cwd_is_temp(tmp_path):
    assert "GEMINI_API_KEY" not in os.environ
    assert os.getcwd() == str(tmp_path.resolve()) or os.getcwd() == str(tmp_path)


def test_factory_has_fields_process_pr_metrics_reads():
    pr = make_pr()
    for key in ("number", "title", "createdAt", "mergedAt", "additions", "deletions", "author",
                "closingIssuesReferences", "commits", "reviews", "timelineItems"):
        assert key in pr
