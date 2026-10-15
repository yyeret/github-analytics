# Public release audit — October 15, 2026

**Verdict:** The repository is already public. The prepared local patch addresses
the verified code and packaging findings below. Public `main` and the deployed
service still need the patch; the initial audit performed no external writes. The approved settings follow-up
is recorded below. No deployment, secret rotation, or history rewrite occurred.

## Scope and evidence

- GitHub: `yyeret/github-analytics`, visibility confirmed public; reviewed base
  `origin/main` at `c6c76f9` in an isolated `codex/public-release-preparation`
  worktree. The original checkout's unrelated unfinished work was preserved.
- Secret detection: Gitleaks 8.30.1, redacted output, all locally available refs
  with `--all --full-history`: 30 reachable commits, 23 non-merge commits scanned,
  approximately 2.28 MB. No detector matches. Also scanned all 63 publishable
  files in the original dirty checkout and the prepared source snapshot: no
  detector matches. Ignored environments, caches, and local credentials were
  deliberately excluded from the publishable snapshot.
- Historical inventory: inspected sensitive-looking filenames, private-key
  markers, filesystem paths, email-bearing files, and the historical raw data.
  `raw_data.json` entered history in `28be453` and was untracked in `39ec81f`;
  its repository is `openclaw/openclaw`. It contains public contributor/PR data.
  The committed dashboard is a public-data demo. Historical documentation
  exposes local filesystem paths; current copies were made portable.
- Dependencies: pip-audit 2.10.1 found zero known advisories for 47 applicable
  locked runtime packages and 47 packages in fresh local requirements resolution.
  These are distinct resolutions; the fresh requirements are not a lock file.
- Licensing: checked metadata for all 47 applicable locked runtime packages,
  the browser assets' upstream license files, and classifier provenance. The
  original Jira classifier's available file history attributes it to Yuval Yeret.
  MIT was selected explicitly by the maintainer during this review.
- Static analysis: Bandit 1.9.4 found no medium/high-severity source issues after
  fixes. Low-severity subprocess warnings were reviewed: argument lists are used
  without a shell. The local listener no longer binds to all interfaces.
- Independent read-only security, correctness/adversarial, and testing reviews
  examined the prepared patch. They identified the Enterprise-host mismatch,
  fork-targeting error, and hosted test gaps; these were addressed.
- Redacted machine evidence remains in ignored `.cache/security-tools/`:
  `history.json`, `source.json`, `original-source.json`, `bandit.json`,
  `locked-dependencies.json`, `dependencies.json`, `dependency-licenses.json`,
  and test logs. Do not publish scanner evidence or caches automatically.

## Findings and disposition

| Severity | Finding | Prepared resolution |
|---|---|---|
| High consequence if exposed | Local API could use the operator's credentials without authentication and bind to all interfaces. | Loopback default, Host/Origin validation, explicit single-user boundary. Use the Worker for hosting. |
| Medium | Local collection accepted private repositories; checking github.com alone could still collect private Enterprise data through `GH_HOST`. | Visibility preflight and explicit github.com hostname on every PR/issue collection command; Enterprise-host regression. |
| Medium | Hosted auth and rate limiting allowed requests when required configuration/bindings were missing. | Authentication required unless explicitly disabled; missing limiter rejects requests. Tests cover analysis and chat. |
| Medium | Upstream exception text was returned to clients. | Generic local and hosted collector errors; sentinel-based redaction tests. |
| Medium | Generated report embedded unescaped repository text in HTML. | HTML escaping for repository text; existing script-data escaping retained. |
| Medium | No project license or documented provenance/third-party boundary. | Maintainer-selected MIT license, package metadata, third-party notices; fetched GitHub content excluded from project license. |
| Low | Local server loaded a home `.env`, removed an injected GitHub token, and tried a malformed vault lookup on web requests. | Project-local `.env` only; preserve injected credentials; optional chat key comes from the environment. |
| Low | External scripts lacked integrity checks; generated report used an unversioned script. | Pinned Chart.js and annotation versions with verified SHA-384 integrity values, including the generated report and public assets. |
| Low | Secret-file variants, private keys, databases and analysis artifacts could be added accidentally. | Expanded ignore rules and safe `.env.example`; historical tracked files still require human review. |
| Low | New security-settings helper initially targeted upstream from forks. | Explicit `--repo` or checkout origin resolution; tests prove read-only defaults and target isolation. |
| Release controls | No continuous source/history/dependency security gate. | Repeatable cross-platform scanner, pinned tool versions, security workflow, full-history checkout, least-privilege CI, immutable action refs, Dependabot config. |

At the initial audit, GitHub reported secret scanning and push protection enabled
and no open secret-scanning alerts. Private vulnerability reporting and Dependabot
security updates/alerts were disabled.
The script below prepares these changes without requesting broader scopes:

```text
python scripts/configure-repository-security.py --repo yyeret/github-analytics
python scripts/configure-repository-security.py --repo yyeret/github-analytics --apply
```

The first command is read-only and was verified. The second changes account settings and requires approval. It enables private
reporting, vulnerability alerts, and automatic security fixes. Add `--protect-main`
to apply the checked-in branch policy; visibility is unchanged.

## Validation and practical limits

Final validation: **224 tests passed on Python 3.13.7**, configured coverage
**93.94%** (90% floor), Ruff passed, `git diff --check` passed, `uv lock --check`
passed, CI/Dependabot YAML parsed, ignore rules preserved both example files,
and root/public HTML bytes matched. The complete security runner passed after
the final source changes. Earlier full-suite validation also passed on Python
3.14.2. Integrity values were computed from the exact fetched CDN script bytes.
The configured coverage floor measures `analyze`, `app`, `issues`, and
`value_classification`; it does **not** claim 90% coverage of the hosted auth code
or security tooling. Added boundary tests exercise the changed hosted behavior
separately. No production requests with adversarial data or live Google identity
verification were performed. Native Linux/Windows execution and Python 3.11 are
left to CI; source paths and downloader variants support those platforms.

The scan covers locally available Git refs and current publishable files, not
GitHub issue/PR comments, release attachments, artifacts, forks, inaccessible
refs, or deleted remote content. Pattern scanners do not prove the absence of
all credentials or confidential prose. No active credential was found, so this
review does not prescribe a credential rotation or destructive history purge.
Public historical contributor data and machine paths remain accessible.

The MIT decision assumes the maintainer has authority to license their original
and adapted work, including any employer/client obligations; authorship history
alone cannot establish those rights. Python packages retain their own licenses.
In particular, certifi uses MPL-2.0 and tqdm declares MPL-2.0 AND MIT; the project
license does not relicense those packages. Preserve their notices when bundling.
Browser license sources: [Chart.js MIT](https://github.com/chartjs/Chart.js/blob/v4.4.2/LICENSE.md),
[annotation plugin MIT](https://github.com/chartjs/chartjs-plugin-annotation/blob/v3.0.1/LICENSE.md),
and [Outfit OFL](https://github.com/Outfitio/Outfit-Fonts/blob/main/OFL.txt).
[GitHub's license guidance](https://choosealicense.com/no-permission/) explains
why public visibility alone did not supply a reuse license before this patch.

Remaining deployment limits are documented in `SECURITY.md`: the local server
trusts local users/processes; hosted sign-in is open to Google accounts; limits
are per IP rather than a global budget; repository text can influence AI advice;
and repository visibility can change during or after collection. The CLI remains
a trusted operator tool. Forks must supply their own Cloudflare/OAuth resources;
checked-in resource identifiers are not credentials. No live infrastructure or
OAuth settings were changed or certified by this source review.


## Approved settings follow-up

On October 15, 2026, the maintainer authorized PR publication, private vulnerability
reporting, Dependabot, and protection of `main`. The declarative settings helper
was applied and independently read back through GitHub's API:

- Private vulnerability reporting enabled.
- Dependabot vulnerability alerts enabled (HTTP 204); automated security fixes
  enabled and not paused.
- `main` requires pull requests and resolved review conversations. Required
  approvals remain zero so a sole maintainer can merge their own PR.
- Both `test (3.11)` and `test (3.13)`, plus `audit`, must pass from the GitHub
  Actions app (15368); the branch must be up to date.
- Admin enforcement enabled; force pushes and deletion disabled.

The desired branch policy is `.github/main-branch-protection.json`; use
`python scripts/configure-repository-security.py --repo OWNER/REPO --apply --protect-main`
to apply it again. Opening the PR does not authorize merging or deployment.
