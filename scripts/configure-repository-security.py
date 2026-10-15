"""Inspect GitHub security settings; --apply explicitly enables protective settings.

Requires gh authentication for this repository. Never changes visibility, rewrites
history, sends messages, deploys code, or requests broader credential scopes.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def api(repository, path, method='GET', input_file=None):
    command = ['gh', 'api', '--hostname', 'github.com', '--method', method, f'repos/{repository}/{path}']
    if input_file is not None:
        command.extend(['--input', str(input_file)])
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f'GitHub {method} {path} failed (exit {result.returncode}); no token scopes changed.')
    return json.loads(result.stdout) if result.stdout.strip() else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Enable private reporting and Dependabot protections')
    parser.add_argument('--protect-main', action='store_true', help='Also apply .github/main-branch-protection.json')
    parser.add_argument('--repo', help='Explicit github.com owner/name; defaults to this checkout')
    args = parser.parse_args()
    repository = args.repo
    if repository is None:
        result = subprocess.run(['git', 'remote', 'get-url', 'origin'], cwd=ROOT,
                                capture_output=True, text=True, check=True)
        remote = result.stdout.strip().removesuffix('.git')
        match = re.fullmatch(r'(?:https://github\.com/|git@github\.com:)([^/]+/[^/]+)', remote)
        if not match:
            raise ValueError('Origin must be on github.com; pass --repo owner/name explicitly.')
        repository = match.group(1)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*', repository):
        raise ValueError('Expected a github.com owner/name repository')
    print(f'Target repository: github.com/{repository}')
    private_reporting = api(repository, 'private-vulnerability-reporting')
    print(f'Private vulnerability reporting: {private_reporting["enabled"]}')
    if not args.apply:
        print('Read-only check. --apply enables private reporting, vulnerability alerts, and automated security fixes.')
        return 0
    if not private_reporting['enabled']:
        api(repository, 'private-vulnerability-reporting', 'PUT')
    api(repository, 'vulnerability-alerts', 'PUT')
    api(repository, 'automated-security-fixes', 'PUT')
    print('Private reporting, vulnerability alerts, and automated security fixes enabled.')
    if args.protect_main:
        api(repository, 'branches/main/protection', 'PUT', ROOT / '.github/main-branch-protection.json')
        print('Main branch requires pull requests, passing checks, and resolved conversations; admins are included.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
