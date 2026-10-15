# Security

Report a suspected vulnerability privately using GitHub's private vulnerability
reporting for this repository, if enabled. Otherwise contact the maintainer at
yuval@yeretagility.com. Do not include credentials or private repository data
in public issues. Only the latest main branch receives security fixes.

## Deployment boundaries

`app.py` is a single-user local development server. Bind it to `127.0.0.1`;
never expose it through a public listener, tunnel, or shared reverse proxy. It
uses your GitHub CLI identity and optional Gemini API key. Host and Origin
checks reduce browser attacks; they do not provide multi-user authentication.
Local programs and users who can reach the listener are trusted.

The hosted entry point is `cloudflare_worker.py`. It requires Google sign-in
unless `AUTH_REQUIRED` is explicitly `false`; missing rate-limit bindings reject
analysis and chat. Keep authentication enabled in production. Sign-in is open to
Google accounts and is not an invitation-only access list. Rate limits are per IP,
not a global spending ceiling. Set provider quotas and billing alerts separately.

Both web APIs reject private repositories. Use public-only GitHub credentials;
private-to-public visibility transitions during a collection are not transactional.
The command-line collector is a trusted operator tool and can read whatever the
operator's GitHub CLI identity can access. Review generated output before sharing.

## Secrets and collected data

Use environment variables or a project-local ignored `.env` for local keys.
The app does not load your home `.env`, discard an injected GitHub token, or
query a secrets vault from a web request. The maintainer's deployment wrappers
use Bitwarden; other users can supply their own deployment tooling.
Never commit `.env*`, `.dev.vars*`, private keys, runtime databases, or analysis
caches. Example files contain placeholders only. `wrangler.jsonc` contains public
OAuth and deployment identifiers, not authentication secrets. Forks need their
own resources and OAuth configuration.

Generated analyses may contain contributor handles, PR/issue titles and bodies,
and private data when collected by the CLI. `dashboard.html` is a committed
historical public-data demo; new reports and caches must be reviewed before any
commit. Deleting a file or adding an ignore rule does not erase Git history.
Rotate a disclosed credential first, then coordinate any history cleanup.

Repository text and model replies are untrusted. Chat has no tools or account
write access; prompt injection can still affect the quality of coaching advice.
UI chat text is escaped before formatting, and generated script data is escaped.
The UI fetches scripts/fonts from external providers; pinned script versions and
integrity checks reduce dependency substitution risk but do not eliminate it.
