# GitHub Flow & Value Stream Analytics

A dashboard that analyzes pull request flow in public GitHub repositories and maps it to Theory of Constraints and Evidence-Based Management metrics.

## Try it

Enter a public repository such as `sveltejs/svelte` or `https://github.com/owner/repo`. Analysis runs on Cloudflare Workers; results are cached for six hours. Chat reads that cache on the server and sends Gemini a compact summary of cycle time, complete-week throughput, and aging work. Reanalyze the repository when the cache expires. Private repositories are rejected. Each visitor's IP is limited to six analyses and twelve AI questions per minute. Gemini chat is enabled when `GEMINI_API_KEY` is configured.

## Local development

The existing FastAPI app remains available locally:

```bash
uv sync --extra local
uv run uvicorn app:app --reload
```

Open `http://localhost:8000`. The local server uses the GitHub CLI and accepts only public repositories. Run `gh auth login` first, then optionally copy `.env.example` to `.env` for your Gemini key. Keep it bound to localhost; use the authenticated Worker entry point for hosting. See [Security](SECURITY.md).


## Testing

CI (`.github/workflows/ci.yml`) runs on every PR and push to `main`. To run the same checks locally:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
ruff check .
pytest --cov            # fails below the coverage floor in pyproject.toml
pytest -m unit          # or -m integration / -m e2e
```

Tests never touch the network, the `gh`/`bws` CLIs, or Gemini; `subprocess.run` is blocked by default and each test runs in a temp directory.


## Issue and PR content framing

The API, web dashboard, AI coach context, and generated static report classify
sampled issues and PRs from their own titles and bodies as **Input, Activity,
Output, Outcome, or Impact**. Empty content is **Unknown**. This describes stated
intent, not achieved customer or business value. Counts represent items, not effort.
The dashboard shows separate PR and issue distributions plus filterable items and
classification rationales. A stacked percentage chart compares framing across PRs
and issues; clicking a segment filters the item table. The hero **Outcome-oriented**
card shows `(Outcome + Impact) / classified items`, excluding Unknown and combining
the available issue and PR samples. It shows no percentage when nothing is classified.
Historical baseline items are excluded from this view.

`value_classification.py` vendors the rule core from
`jira-flow-coaching-dashboard/offline_analysis_value.py`, following the shared
`outcome-framing-coach` taxonomy (v1.1.0). Its source commit is recorded in the
module. There is no runtime dependency on either checkout and no model/API cost.
GitHub adapters add title/body input, explicit fix-title and quarterly-container
handling, provenance, defaults, and missing-body coverage. The rules retain the
Jira classifier's semantic limits: ambiguous items default to Activity, and keyword
matches can misread context. Review rationales before interpreting distributions.

Existing caches remain usable with title-only classification. Use `python analyze.py OWNER/REPO --refresh` to collect bodies.
`/api/analyze` exposes the additive `value_classification` field; AI contribution
classification remains in `classification`.
## Cloudflare deployment

This project uses Cloudflare Python Workers (FastAPI), Workers Static Assets, KV for cached public results, and native Worker rate-limit bindings.

1. Install the Cloudflare Worker dependencies with `uv sync` (Wrangler requires uv 0.12.3 or newer).
2. Forks must provision their own KV namespace and D1 database and replace the resource IDs, OAuth client ID, and custom domain in `wrangler.jsonc`. The checked-in identifiers belong to the maintainer; they are public identifiers, not credentials.
3. Run `bash scripts/deploy-cloudflare.sh` on macOS/Linux or `./scripts/deploy-cloudflare.ps1` in PowerShell. Each wrapper refreshes the Bitwarden session and calls the deployment script. It resets the development environment to the small Worker dependency set, then passes secrets to Wrangler in a temporary file that is deleted after deployment.
4. Wrangler deploys the app to `github-analytics.yuvalyeret.com` and its `workers.dev` address.

Google sign-in uses Cloudflare D1 for account records and hashed, short-lived sessions. The migration is applied by the deployment script. Configure the Google Auth Platform external app as **GitHub Flow Analytics**, with `https://github-analytics.yuvalyeret.com/` as its homepage, `https://github-analytics.yuvalyeret.com/privacy.html` as its privacy policy, and `yuvalyeret.com` as an authorized domain. Create a **Web application** OAuth client with the authorized JavaScript origin `https://github-analytics.yuvalyeret.com`. No redirect URI or Google API scopes are needed. Publish the app for external users; an app left in Testing only works for its listed test users. Then run:

```bash
python3 scripts/configure-google-auth.py --client-id YOUR_WEB_CLIENT_ID.apps.googleusercontent.com
bash scripts/deploy-cloudflare.sh
```

Cloudflare Builds can keep the deploy command `npx wrangler deploy`. The repository's
Wrangler build hook runs `scripts/prepare-cloudflare-build.py` first: it installs
workspace-local uv tooling, uses Pywrangler to package the locked Python
runtime dependencies and SDK, and stages only the runtime source files under
`.wrangler/worker`. Virtual environments and test files stay out of the upload.
Local and CI pip dependencies live in `requirements-local.txt` and
`requirements-dev.txt`; Pywrangler rejects a root `requirements.txt`.

The `public/` assets are synced from the root HTML files during deployment. Do not add credentials to `.dev.vars`, source files, or commits. The deployed API only accepts public repositories.

Check current Cloudflare plan limits and pricing before deployment; repository analysis and AI requests incur provider usage.


## License and public-release checks

[MIT](LICENSE). See [third-party notices](THIRD_PARTY_NOTICES.md) for adapted
code, browser assets, and fetched GitHub content. The license does not cover
third-party repository data.

Run `python scripts/security-check.py` for a redacted full-history secret scan,
tracked-file secret scan, and source security checks. Install the check tools
from `requirements-security.txt` into a separate virtual environment. The script
downloads checksum-verified Gitleaks 8.30.1 into the ignored project cache; CI uses
the same commands. The script never deploys,
rotates keys, rewrites history, or uploads source to a model. Dependency audits
query the advisory service with package names and versions.

Read [the release audit](docs/security/2026-10-15-public-release-audit.md) for
scope, evidence, and remaining release decisions.


Maintainers can inspect repository protections with
`python scripts/configure-repository-security.py --repo OWNER/REPO` and apply
private vulnerability reporting, Dependabot security alerts/fixes, and the checked-in
main-branch policy using `--apply --protect-main`. Applying requires repository
administration permission. The policy requires a PR, current CI and security
checks, and resolved conversations; it blocks force pushes and deletion, including
for admins. No additional approver is required, so a sole maintainer can merge.
