import os
import subprocess

import pytest

from tests.factories import make_pr


def test_real_subprocess_is_blocked():
    with pytest.raises(AssertionError, match="real subprocess"):
        subprocess.run(["gh", "auth", "status"])


def test_api_keys_cleared_and_cwd_is_temp(tmp_path):
    assert "GEMINI_API_KEY" not in os.environ
    assert os.getcwd() == str(tmp_path.resolve()) or os.getcwd() == str(tmp_path)


def test_factory_has_fields_process_pr_metrics_reads():
    pr = make_pr()
    for key in ("number", "title", "createdAt", "mergedAt", "additions", "deletions", "author",
                "closingIssuesReferences", "commits", "reviews", "timelineItems"):
        assert key in pr
