"""Publish the Worker using keys from Bitwarden without logging or persisting them."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def validate_auth_config() -> None:
    config = json.loads((ROOT / "wrangler.jsonc").read_text(encoding="utf-8"))
    vars_config = config.get("vars", {})
    if vars_config.get("AUTH_REQUIRED") == "true":
        client_id = vars_config.get("GOOGLE_OAUTH_CLIENT_ID", "")
        if not client_id.endswith(".apps.googleusercontent.com"):
            raise SystemExit("Google sign-in is required, but no valid Web OAuth client ID is configured.")


def find_uv() -> str | None:
    candidates = [shutil.which("uv")]
    current = candidates[0]
    if current:
        checked = subprocess.run([current, "--version"], capture_output=True, text=True)
        match = re.search(r"uv (\d+)\.(\d+)\.(\d+)", checked.stdout)
        if match and tuple(map(int, match.groups())) >= (0, 12, 3):
            return current
        cache = subprocess.run([current, "cache", "dir"], capture_output=True, text=True)
        if cache.returncode == 0:
            root = Path(cache.stdout.strip()) / "archive-v0"
            candidates.extend(str(path) for path in root.glob("*/uv-*.data/scripts/uv"))
            candidates.extend(str(path) for path in root.glob("*/uv-*.data/scripts/uv.exe"))
    for candidate in candidates[1:]:
        checked = subprocess.run([candidate, "--version"], capture_output=True, text=True)
        match = re.search(r"uv (\d+)\.(\d+)\.(\d+)", checked.stdout)
        if match and tuple(map(int, match.groups())) >= (0, 12, 3):
            return candidate
    return None


def main() -> int:
    validate_auth_config()
    try:
        result = subprocess.run(
            ["bws", "secret", "list", "-o", "json"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("Could not read Bitwarden secrets. Load the bws environment and retry.", file=sys.stderr)
        return 1

    records = json.loads(result.stdout)
    keys = {record.get("key"): record.get("value") for record in records}
    cloudflare_token = os.environ.get("CLOUDFLARE_API_TOKEN") or keys.get("CLOUDFLARE_API_TOKEN")
    if not cloudflare_token:
        print("CLOUDFLARE_API_TOKEN is missing from the environment and Bitwarden.", file=sys.stderr)
        return 1
    uv = find_uv()
    if not uv:
        print("uv 0.12.3 or newer is required by Cloudflare's Python Worker tooling.", file=sys.stderr)
        return 1

    secrets = {}
    github_token = os.environ.get("GITHUB_TOKEN") or keys.get("GITHUB_TOKEN") or keys.get("GH_TOKEN")
    gemini_key = os.environ.get("GEMINI_API_KEY") or keys.get("GEMINI_DEVELOPER_API_KEY")
    if github_token:
        secrets["GITHUB_TOKEN"] = github_token
    if gemini_key:
        secrets["GEMINI_API_KEY"] = gemini_key

    env = os.environ.copy()
    env["CLOUDFLARE_API_TOKEN"] = cloudflare_token
    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID") or keys.get("CLOUDFLARE_ACCOUNT_ID")
    if account_id:
        env["CLOUDFLARE_ACCOUNT_ID"] = account_id

    with tempfile.TemporaryDirectory(prefix="github-analytics-deploy-") as temp_dir:
        secret_file = Path(temp_dir) / "worker-secrets.json"
        secret_file.write_text(json.dumps(secrets), encoding="utf-8")
        secret_file.chmod(0o600)
        try:
            synced = subprocess.run(
                [uv, "sync", "--no-extra", "local"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
            )
            if synced.returncode:
                print("Could not prepare the Cloudflare Worker dependencies. See uv sync output.", file=sys.stderr)
                return synced.returncode
            (ROOT / "public").mkdir(exist_ok=True)
            shutil.copy2(ROOT / "index.html", ROOT / "public" / "index.html")
            shutil.copy2(ROOT / "privacy.html", ROOT / "public" / "privacy.html")
            migrated = subprocess.run(
                [uv, "run", "pywrangler", "d1", "migrations", "apply", "github-flow-analytics-users", "--remote"],
                cwd=ROOT,
                env={**env, "PATH": f"{Path(uv).parent}{os.pathsep}{env.get('PATH', '')}"},
                capture_output=True,
                text=True,
            )
            if migrated.returncode:
                print("Could not apply the remote D1 migrations. See Wrangler's migration output.", file=sys.stderr)
                return migrated.returncode
            deployed = subprocess.run(
                [uv, "run", "pywrangler", "deploy", "--secrets-file", str(secret_file)],
                cwd=ROOT,
                env={**env, "PATH": f"{Path(uv).parent}{os.pathsep}{env.get('PATH', '')}"},
                capture_output=True,
                text=True,
            )
        finally:
            secret_file.unlink(missing_ok=True)

    if deployed.returncode:
        print("Cloudflare deployment failed. Wrangler output was suppressed to protect credentials.", file=sys.stderr)
        return deployed.returncode

    match = re.search(r"https://[a-z0-9-]+\.[a-z0-9-]+\.workers\.dev", deployed.stdout)
    print("Deployment complete." + (f" Open {match.group(0)}" if match else " Check Wrangler's deployment output for the workers.dev URL."))
    if not github_token:
        print("No GitHub token was found; anonymous GitHub API limits apply.")
    if not gemini_key:
        print("No Gemini key was found; AI chat will remain unavailable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
