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

### Cost-of-delay intent analysis

Each sampled PR receives four independent Jev classifications, each mapped to
`1, 2, 3, 5, 8, 13` Fibonacci points:

| Element | Meaning |
| --- | --- |
| Value | Significance of intended benefit to users or contributors, including developer productivity |
| Time criticality | Stated or reasonably implied time sensitivity of the intended contribution |
| Opportunity runway | Future capabilities intended to be enabled along the repository's inferred direction |
| Risk reduction | Significance of existing or potential product risk the change intends to reduce, including preventive controls |

**Cost of delay = value + time criticality + opportunity runway + risk reduction.**
The total ranges from 4 to 52 for assessable items. This is a relative intent index,
not money, measured impact or WSJF; no job-size divisor is used. Risk reduction does
not assess implementation difficulty or regression risk. Preventive work needs no
prior incident or proof of realized benefit. No discernible timing intent is distinct
from establishing that delay has no consequence. Missing answers remain failures.

Gemini researches the repository once per analysis. A planning call selects up to
five documents from a catalog capped at 120 ranked Markdown/RST/text paths. A
synthesis call investigates primary users, critical jobs, strategic options, adoption
friction, contributor workflows and preventive product risks. Inputs include metadata,
README (12,000 characters), selected documents (4,000 each), up to three recent release
notes and five open issue signals, under a shared 36,000-character source budget.
The API exposes `product_value.sources.research` with source IDs/URLs and truncation,
and collection limitations. Missing optional sources are reported as unknown.
`context.repository_research` contains source-linked claims marked `stated` or `inferred`
for primary users, critical workflows, value drivers and product risks. Source links
establish provenance, not independently verified strategic truth; issue signals are a
small comment-ranked sample. The LLM selects only supplied repository paths; external
sites and arbitrary URLs are not fetched. Optional planning failure preserves other
evidence and is disclosed.

Jev receives that compact researched profile plus only the first 1,500 characters
of each PR description. Titles, code size and framing labels are not sent. A bundled
Noul asks whether the description identifies a change whose intent can be assessed.
Code requires probability at least 0.7; this default is not a calibrated accuracy claim.
Terse identifiable changes can have inferred intent without an explicit business case.
Vague/template-only descriptions return `insufficient_context` and no numeric total;
empty descriptions also skip the provider call. `intent_assessability` and
`body_truncated` make that decision and input limit inspectable. Four Score questions
share the same request. Code chooses the most probable rubric level (lower wins ties),
maps its index to Fibonacci points, and sums the four points. The API retains each
rubric and probability distribution for review, rather than treating model
confidence as proof of correctness. There is no diff inspection or measured-impact
input. The most recent 30 sampled PRs are assessed, including up to 10 open PRs.

For local use, export `GEMINI_API_KEY` and `TYPESAFE_API_KEY`; `GOOGLE_API_KEY` and
`JEV_API_KEY` are also accepted locally. Credentials may be injected from Bitwarden;
do not commit populated environment files. The hosted deployment accepts configured
`GEMINI_API_KEY` and `TYPESAFE_API_KEY` runtime secrets. Alternatively, supply keys
for one analysis in the optional dashboard fields or these request headers:

```http
POST /api/analyze
Content-Type: application/json
X-TypeSafe-Api-Key: <your Jev key>
X-Gemini-Api-Key: <your Gemini key>

{"repo_url":"github/spec-kit","force_refresh":true}
```

Omit either header to use its configured default. Keys stay server-side when set
as runtime secrets; caller-supplied keys travel over HTTPS, are not persisted in
browser storage or analysis results. Hosted caller-key requests bypass the shared analysis cache. The
hosted API still requires a Google session; provider keys do not grant app access.
Caller-key hosted responses include an `analysis_id` tied to the signed-in session.
Pass it to `/api/chat` to coach against that exact analysis; these snapshots expire
after the normal analysis TTL and cannot be read from another session. Chat still
uses the deployment Gemini key. Coaching caller-key hosted results requires a
signed-in session, including when authentication is disabled for development;
without one, chat returns 401 rather than selecting a shared result.
The local development server is a single-user localhost tool: its process-wide
coaching cache is shared by clients of that process. Use the authenticated hosted
entry point for multiple users.
Model or configuration failures leave other repository metrics available. Default-key
hosted results cache for six hours, shortened to ten minutes after assessment
failures or unavailable provider configuration. Force-refresh after changing deployment keys.
Each caller-key analysis starts fresh provider requests; avoid concurrent retries.

Only public repositories are assessed. README and research content are fetched
without a GitHub token so a visibility transition cannot expose private content.
A refused or rate-limited source is omitted with a collection limitation. Review inferred context and scores
before using them to prioritize work. Assessments have a 90-second overall budget;
completed PR scores are retained and unfinished items are marked failed on expiry.

Planning uses a 768-token output budget. Synthesis starts with 4,096 tokens and
asks for under 700 words. A `MAX_TOKENS` synthesis finish triggers one fresh attempt
with 8,192 tokens. Planning, optional source collection and synthesis share a
45-second deadline; each GitHub research request is limited to 8 seconds and
Gemini requests to 25 seconds. Other
Gemini errors, blocked responses, and invalid JSON are not retried. Incomplete
responses are never used to score PRs. Each PR call remains independent, so one
failed assessment does not discard successful ones. When a deadline cancels a
Worker request, its abort signal cancels the outgoing fetch, including a pending
response-body read.

For diagnosis, `/api/analyze` includes `product_value.analysis_id`; failures also
include a stable `error_code` and a safe explanation. Search Worker logs for that
analysis ID. Events identify `product_sources`, `repo_research_plan`, `repository_research`, `product_context`, or
`pr_assessment`; Gemini events include the model, attempt, output budget, HTTP status, finish reason, token usage, and elapsed milliseconds when available. Jev events include model, attempt, HTTP status, token usage, elapsed time, and a stable error code on failure.
A PR failure also records its number and cohort. Workers receive structured
console objects; local runs emit JSON through Python logging. Prompts, source
text, model output, credentials, raw error bodies, and exception messages are
excluded. Cache hits reuse the previous analysis ID and do not run the model;
use `force_refresh: true` to verify a fresh attempt. Existing failed results can
remain cached for ten minutes after an update.

The offline/opt-in live evaluator is `scripts/evaluate_value_intent.py`. Its frozen
40-PR/two-repository fixture has a held-out partition and focused controls. Reference
labels are agent-provisional, not human ground truth; agreement is not validated
accuracy. See [benchmark protocol](docs/experiments/2026-10-26-value-intent-benchmark.md).
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

Branch builds use `npx wrangler preview` (Wrangler 4.135.0 or newer), which requires
an explicit `previews` block. Preview resources are declared in
`.cloudflare/preview-resources.json`. Run `python scripts/configure-cloudflare-previews.py`
to inspect the setup. After authorizing resource creation, export the existing
Cloudflare API token from Bitwarden and run the same command with `--apply`.
It reuses or creates the named Preview KV namespace and D1 database, initializes
the empty users/session schema, and writes their real IDs to `wrangler.jsonc`.
Rerunning it reuses those resources. It preserves production bindings and data.

Previews share these staging resources with other branches. They require Google
sign-in and use separate rate-limit namespaces with the same request limits.
Preview URLs are public; protected API endpoints still require a session. The
configured Google OAuth client must allow the Preview's JavaScript origin before
sign-in can work there. This setup leaves Google OAuth settings unchanged.
Preview service secrets are configured separately in Cloudflare's Previews Base;
Cost-of-delay assessment requires Preview `GEMINI_API_KEY` and `TYPESAFE_API_KEY` secrets, or caller-supplied headers.

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
