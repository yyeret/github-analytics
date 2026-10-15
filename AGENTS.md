# GitHub Flow Analytics: agent working agreement

## Development workflow

Use Compound Engineering for new development in this repository. Load the
relevant installed skill's `SKILL.md` and its required references before acting;
do not merely imitate its output. These conventions apply across Codex, Claude
Code, Gemini, macOS, and Windows. Use the host's native skill invocation syntax.

- Unclear feature or product scope: `ce-brainstorm`, then `ce-plan` when an
  implementation plan is needed. Clear, small changes can go directly to
  `ce-work`; do not create a plan just to satisfy ceremony.
- Implementation from a concrete request or accepted plan: `ce-work`.
- Bugs and failing behavior: `ce-debug`.
- Before delivery: run the relevant verification, simplify substantive code
  with `ce-simplify-code` when its threshold applies, and review the change with
  `ce-code-review`. Resolve justified findings within the authorized scope.
- Capture verified, reusable, non-obvious lessons with `ce-compound` when its
  criteria apply. Routine changes do not need a learning document.
- Commit with `ce-commit` when requested. Use `ce-commit-push-pr` when publishing
  is authorized; use `lfg` only when explicitly requested. Pushes, PR creation,
  merges, deployments, and account changes require user authorization.

Keep work proportional to risk. Start with the smallest meaningful proof of
behavior, implement it, then improve structure. Use existing tests and machinery
before introducing new ones. Do not add tests that merely repeat configuration.
If a required skill is unavailable in the current harness, report the gap and
continue authorized local work using these stages; do not claim a skill ran.

## Repository context and verification

This is a Python flow analytics application with a static dashboard and a
Cloudflare Workers deployment. Inspect the current branch before choosing the
entry point: local development and deployed Workers have different boundaries.

- `analyze.py`, `issues.py`, `value_classification.py`: collection, metrics, and
  issue/PR framing. Preserve the distinction between stated intent and achieved
  outcomes; do not present item counts as effort or realized value.
- `app.py`: local FastAPI API; `cloudflare_worker.py`: deployed Worker API.
- `index.html`, `public/`, `dashboard.html`: inspect how the relevant UI is served
  or generated before changing it.
- `scripts/`, `wrangler.jsonc`: deployment/build and infrastructure configuration.
- `tests/`, `pyproject.toml`, `.github/workflows/ci.yml`: verification authority.

Use a Python environment with `requirements-dev.txt` installed. Run from the
feature checkout, using that environment's interpreter:

```text
python -m ruff check .
python -m pytest --cov --cov-report=term-missing
```

CI currently exercises Python 3.11 and 3.13. Respect the configured coverage
floor. Use focused unit or integration checks while iterating; use browser
verification for UI behavior that needs a browser. For instructions/config-only
changes, validate the config, paths, ignore rules, and diff without adding
application tests. Keep automated tests isolated from live GitHub, model APIs,
and credentials; follow existing fixtures. Never deploy as a verification step.

## Shared artifacts and checkout safety

Team CE defaults live in `.compound-engineering/config.yaml`; optional machine
overrides use ignored `config.local.yaml`. The artifact root is `docs/`:

- `docs/plans/`: implementation plans; reuse relevant accepted plans first.
- `docs/ideation/`: exploratory material.
- `docs/solutions/`: documented solutions, organized by category with YAML
  frontmatter such as `module`, `tags`, and `problem_type`; consult relevant
  entries when implementing or debugging in a documented area. Create entries
  only when there is a qualifying lesson.

Write human-readable artifacts in Markdown. Name new plans
`YYYY-MM-DD-short-task-slug.md`. Plans open with an outcome-oriented goal and
2–4 measurable leading indicators and close with a self-contained suggested
`/goal` loop prompt, following the shared planning contract. Record progress in
commits or handoffs rather than rewriting accepted plan bodies.

Run the shared session-start sync before repository work. Keep the primary
checkout on `main`; use a feature branch in an isolated worktree for edits.
Preserve all pre-existing dirty files and commits. Do not stash, reset, or absorb
another session's work. If dependency files have not changed, reuse primary
checkout dependencies; never duplicate Node dependencies in a worktree.
Remove temporary worktrees after their PR is merged or closed, preserving any
unrelated work first. Keep secrets and generated scratch out of Git.
