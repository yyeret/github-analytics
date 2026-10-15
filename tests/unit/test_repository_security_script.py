"""The settings helper must operate on the caller's fork and remain read-only by default."""
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

spec = importlib.util.spec_from_file_location('repository_security_script', Path(__file__).resolve().parents[2] / 'scripts/configure-repository-security.py')
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


@pytest.mark.parametrize('origin', ['https://github.com/example/fork.git', 'git@github.com:example/fork.git'])
def test_read_only_check_uses_the_fork_remote(monkeypatch, origin):
    calls = []
    def fake(cmd, **kwargs):
        calls.append(cmd)
        output = origin if cmd[0] == 'git' else '{"enabled": false}'
        return subprocess.CompletedProcess(cmd, 0, stdout=output)
    monkeypatch.setattr(subprocess, 'run', fake)
    monkeypatch.setattr(sys, 'argv', ['configure-repository-security.py'])
    assert script.main() == 0
    assert calls[1] == ['gh', 'api', '--hostname', 'github.com', '--method', 'GET',
                       'repos/example/fork/private-vulnerability-reporting']
    assert len(calls) == 2  # no mutation without --apply


def test_explicit_apply_uses_only_the_requested_repository(monkeypatch):
    calls = []
    def fake(cmd, **kwargs):
        calls.append(cmd)
        output = '{"enabled": false}' if 'GET' in cmd else ''
        return subprocess.CompletedProcess(cmd, 0, stdout=output)
    monkeypatch.setattr(subprocess, 'run', fake)
    monkeypatch.setattr(sys, 'argv', ['configure-repository-security.py', '--repo', 'example/fork', '--apply'])
    assert script.main() == 0
    assert all(cmd[-1].startswith('repos/example/fork/') for cmd in calls)
    assert [cmd[-1].rsplit('/', 1)[-1] for cmd in calls if 'PUT' in cmd] == [
        'private-vulnerability-reporting', 'vulnerability-alerts', 'automated-security-fixes']


def test_main_protection_uses_the_checked_in_policy(monkeypatch):
    calls = []
    def fake(cmd, **kwargs):
        calls.append(cmd)
        output = '{"enabled": true}' if 'GET' in cmd else '{}'
        return subprocess.CompletedProcess(cmd, 0, stdout=output)
    monkeypatch.setattr(subprocess, 'run', fake)
    monkeypatch.setattr(sys, 'argv', ['configure-repository-security.py', '--repo', 'example/fork', '--apply', '--protect-main'])
    assert script.main() == 0
    command = next(cmd for cmd in calls if 'repos/example/fork/branches/main/protection' in cmd)
    assert 'PUT' in command
    assert Path(command[command.index('--input') + 1]) == script.ROOT / '.github/main-branch-protection.json'
