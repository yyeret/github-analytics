import subprocess

import pytest


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """No network/CLI/real files: guard subprocess, clear keys, run in a temp cwd."""
    def _blocked(cmd, *args, **kwargs):
        raise AssertionError(f"test attempted a real subprocess call: {cmd}")

    monkeypatch.setattr(subprocess, "run", _blocked)
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GITHUB_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def pytest_collection_modifyitems(items):
    """Mark tests by directory so layers are selectable with -m."""
    for item in items:
        for layer in ("unit", "integration", "e2e"):
            if f"/tests/{layer}/" in str(item.fspath).replace("\\", "/"):
                item.add_marker(getattr(pytest.mark, layer))
