import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("installed,refresh", [("0.12.23", True), ("0.13.5", False)])
def test_cached_build_tool_is_refreshed_when_its_pin_changes(tmp_path, monkeypatch, installed, refresh):
    spec = importlib.util.spec_from_file_location("prepare_build", Path(__file__).parents[2] / "scripts/prepare-cloudflare-build.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    monkeypatch.setattr(build, "ROOT", tmp_path)
    bin_dir = tmp_path / ".cloudflare-build" / ("Scripts" if build.os.name == "nt" else "bin")
    bin_dir.mkdir(parents=True)
    for executable in ("python.exe", "uv.exe") if build.os.name == "nt" else ("python", "uv"):
        (bin_dir / executable).touch()
    calls, copied = [], []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout=f"uv {installed}\n")

    monkeypatch.setattr(build.subprocess, "run", run)
    monkeypatch.setattr(build.shutil, "copy2", lambda source, target: copied.append((source, target)))
    build.main()
    installs = [command for command in calls if "pip" in command]
    assert bool(installs) is refresh
    assert any(command[1:4] == ["run", "--locked", "--no-extra"] for command in calls)
    assert any(source.name == "repo_research.py" for source, _ in copied)
