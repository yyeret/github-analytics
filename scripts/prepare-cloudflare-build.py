"""Prepare Python dependencies before plain `npx wrangler deploy` bundles files.

Uses workspace-local tooling on macOS, Linux and Windows. No deployment, secrets,
account changes or shell-specific commands are needed by this build hook.
"""
import os
from pathlib import Path
import shutil
import subprocess
import venv

ROOT = Path(__file__).resolve().parents[1]
UV_VERSION = '0.13.5'


def main():
    tools = ROOT / '.cloudflare-build'
    bin_dir = tools / ('Scripts' if os.name == 'nt' else 'bin')
    python = bin_dir / ('python.exe' if os.name == 'nt' else 'python')
    uv = bin_dir / ('uv.exe' if os.name == 'nt' else 'uv')
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(tools)
    if uv.exists():
        installed = subprocess.run([str(uv), '--version'], check=True, capture_output=True, text=True).stdout.split()[1]
    else:
        installed = None
    if installed != UV_VERSION:
        subprocess.run([str(python), '-m', 'pip', 'install', f'uv=={UV_VERSION}'], check=True)
    env = {**os.environ, 'PATH': str(bin_dir) + os.pathsep + os.environ.get('PATH', '')}
    subprocess.run([str(uv), 'run', '--locked', '--no-extra', 'local', 'pywrangler', 'sync'],
                   cwd=ROOT, env=env, check=True)
    bundle = ROOT / '.wrangler' / 'worker'
    bundle.mkdir(parents=True, exist_ok=True)
    for name in ['cloudflare_worker.py', 'cloudflare_app.py', 'analyze.py', 'issues.py',
                 'github_api.py', 'google_auth.py', 'product_value.py', 'repo_research.py', 'value_classification.py']:
        shutil.copy2(ROOT / name, bundle / name)
    (ROOT / 'public').mkdir(exist_ok=True)
    for name in ['index.html', 'privacy.html']:
        shutil.copy2(ROOT / name, ROOT / 'public' / name)


if __name__ == '__main__':
    main()
