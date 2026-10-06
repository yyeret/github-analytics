import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def blocked_calls():
    """Commands that reached the subprocess guard; a test that triggers it on purpose clears this."""
    return []


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch, blocked_calls):
    """No network/CLI/real files: guard subprocess, clear keys, run in a temp cwd."""
    def _blocked(cmd, *args, **kwargs):
        blocked_calls.append(cmd)
        raise AssertionError(f"test attempted a real subprocess call: {cmd}")

    monkeypatch.setattr(subprocess, "run", _blocked)
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GITHUB_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    yield tmp_path
    # Production code catches broad exceptions, so a blocked call can be swallowed; fail anyway.
    if subprocess.run is _blocked:
        assert not blocked_calls, f"unmocked subprocess call(s) swallowed by production code: {blocked_calls}"


def pytest_collection_modifyitems(items):
    """Mark tests by directory so layers are selectable with -m."""
    for item in items:
        for layer in ("unit", "integration", "e2e"):
            if f"/tests/{layer}/" in str(item.fspath).replace("\\", "/"):
                item.add_marker(getattr(pytest.mark, layer))
