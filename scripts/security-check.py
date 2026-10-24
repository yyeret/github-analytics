"""Repeatable release checks; source and secret values stay on this machine.

Installs a checksum-verified Gitleaks binary into the ignored project cache when
not present. Requires requirements-security.txt. Works on macOS/Linux/Windows.
"""
import hashlib
import io
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache' / 'security-tools'
VERSION = '8.30.1'


def gitleaks():
    system = {'Darwin': 'darwin', 'Linux': 'linux', 'Windows': 'windows'}[platform.system()]
    arch = {'arm64': 'arm64', 'aarch64': 'arm64', 'x86_64': 'x64', 'AMD64': 'x64'}[platform.machine()]
    binary = CACHE / f'gitleaks-{VERSION}' / ('gitleaks.exe' if os.name == 'nt' else 'gitleaks')
    if binary.exists():
        return str(binary)
    suffix = 'zip' if system == 'windows' else 'tar.gz'
    name = f'gitleaks_{VERSION}_{system}_{arch}.{suffix}'
    base = f'https://github.com/gitleaks/gitleaks/releases/download/v{VERSION}/'
    # The download origin is fixed HTTPS; no user-controlled URL or extraction path.
    with urllib.request.urlopen(base + name, timeout=60) as response:  # nosec B310
        data = response.read()
    with urllib.request.urlopen(base + f'gitleaks_{VERSION}_checksums.txt', timeout=30) as response:  # nosec B310
        checks = response.read().decode()
    expected = next(line.split()[0] for line in checks.splitlines() if line.split()[-1] == name)
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('Gitleaks release checksum mismatch')
    if suffix == 'zip':
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            payload = archive.read('gitleaks.exe')
    else:
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            payload = archive.extractfile('gitleaks').read()
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(payload)
    binary.chmod(0o700)
    return str(binary)


def run(label, command):
    print(label, flush=True)
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    # Never echo scanner output: even diagnostics may contain source or secrets.
    print(f'  {"PASS" if result.returncode == 0 else "FAIL"} (exit {result.returncode})', flush=True)
    return result.returncode == 0


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    binary = gitleaks()
    common = ['--redact=100', '--no-banner', '--report-format=json']
    checks = [run('Full available Git history', [binary, 'git', str(ROOT), '--log-opts=--all --full-history',
                                               *common, f'--report-path={CACHE / "history.json"}'])]
    # Scan only publishable files: never sweep ignored environments and caches.
    paths = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=ROOT).split(b'\0')
    with tempfile.TemporaryDirectory(dir=CACHE, prefix='source-') as temporary:
        for encoded in set(paths) - {b''}:
            relative = Path(os.fsdecode(encoded))
            source = ROOT / relative
            if not source.is_file() or source.is_symlink():
                continue
            target = Path(temporary) / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        checks.append(run('Publishable source files', [binary, 'dir', temporary, *common,
                                                     f'--report-path={CACHE / "source.json"}']))
    checks.append(run('Source security (medium/high severity)', [sys.executable, '-m', 'bandit', '-r',
                      'app.py', 'cloudflare_app.py', 'google_auth.py', 'github_api.py', 'analyze.py',
                      'issues.py', 'product_value.py', 'value_classification.py', 'scripts', '-ll', '-f', 'json', '-o', str(CACHE / 'bandit.json')]))
    lock_export = CACHE / 'runtime-requirements.txt'
    exported = run('Export locked runtime dependencies', [sys.executable, '-m', 'uv', 'export', '--locked',
                   '--no-dev', '--extra', 'local', '--no-hashes', '--no-emit-project', '--output-file', str(lock_export)])
    checks.append(exported)
    if exported:
        checks.append(run('Locked runtime advisories', [sys.executable, '-m', 'pip_audit', '-r', str(lock_export),
                          '--format=json', '--output', str(CACHE / 'locked-dependencies.json')]))
    checks.append(run('Fresh local dependency resolution advisories', [sys.executable, '-m', 'pip_audit', '-r',
                      'requirements-local.txt', '--format=json', '--output', str(CACHE / 'dependencies.json')]))
    print(f'Redacted evidence: {CACHE}')
    return 0 if all(checks) else 1


if __name__ == '__main__':
    raise SystemExit(main())
