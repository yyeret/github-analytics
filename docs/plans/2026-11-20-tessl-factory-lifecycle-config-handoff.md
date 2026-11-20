# Handoff: Tessl Factory Issue Lifecycle Signals

**Target agent:** Agent configuring Tessl Factory workflow / bot behavior (e.g. `yyeret/mini-crm` and shared Tessl Factory config)  
**Upstream consumer:** `yyeret/github-analytics` (branch `feat/tessl-factory-issue-lifecycle`, `issues.py`)  
**Date:** 2026-11-20

## Why this handoff exists

`github-analytics` now detects Tessl Factory issue lifecycles automatically on repos using `tessl` / `factory:*` labels and `<!-- tessl-factory -->` comments, charting daily/weekly Issue Lifecycle CFDs (`Backlog`, `Queued`, `Agent Working`, `Blocked`, `PR Review`, `Done`).

On `yyeret/mini-crm` (65 issues), the detector already surfaces the real bottleneck — **18 of 25 open issues (72%) sitting in `PR Review`, 2 `Blocked`, 5 `Backlog`, 40 `Done`** — but today it relies on prose heuristics over `<!-- tessl-factory -->` comments. Standardizing what Tessl Factory emits eliminates heuristic fragility and fixes three telemetry blind spots observed on `yyeret/mini-crm`.

## Supported contract (already implemented in `github-analytics`)

### Primary: Structured HTML comment marker on the issue

Whenever Tessl Factory transitions an issue's workflow state, include this HTML comment in the **issue** comment body:

```html
<!-- tessl-lifecycle: stage=<stage> -->
```

Optional key-value metadata may follow `stage=<stage>` (e.g. `run=<id> pr=<number> reason=<slug>`). `github-analytics` parses `stage=<stage>` case-insensitively and maps:

| Canonical `stage` value | Accepted aliases | Charted CFD stage |
|---|---|---|
| `backlog` | — | `Backlog` |
| `queued` | — | `Queued` |
| `in_progress` | `working`, `agent_working` | `Agent Working` |
| `blocked` | `needs_human` | `Blocked` |
| `pr_review` | `review`, `in_review` | `PR Review` |
| `done` | — | `Done` |

`<!-- tessl-lifecycle: ... -->` takes precedence over legacy prose matching in `issues.py` (`_parse_tessl_factory_comment`) and can move an issue in any direction (including `PR Review -> Blocked` if CI/merge fails, or `Blocked -> Agent Working` when unblocked).

### Optional durable upgrade: GitHub Project v2 `Status`

If Tessl Factory also updates a single open GitHub Project v2 linked to the repository (`Status` field), `github-analytics` prefers Project v2 status history over comment heuristics automatically. Project history is immune to repo-wide comment-volume truncation (GitHub REST `/issues/comments` mixes PR and issue comments and is capped at 300 most recent comments in hosted mode).

## Specific gaps to fix in Tessl Factory config (observed on `yyeret/mini-crm`)

1. **Emit explicit `<!-- tessl-lifecycle: stage=... -->` markers on state transitions only:**
   - Pickup / run start: `<!-- tessl-lifecycle: stage=in_progress run=<run-id> -->`
   - Blocked / refused / missing dependency (`#183`, `#153`): `<!-- tessl-lifecycle: stage=blocked reason=<dependency|missing_tool|ci_failed> -->`
   - PR opened / ready for human review: `<!-- tessl-lifecycle: stage=pr_review pr=<pr-number> -->`
   - **Do NOT emit `<!-- tessl-lifecycle: ... -->`** on non-transition narrative comments (`**What changed for you:**`, `CE trace for this round`, `Release check`).
2. **Always include `Closes #<issue-number>` in Tessl-opened PR descriptions:**
   - On `mini-crm`, issues `#152` (PR `#157`) and `#162` (PR `#164`) were cross-referenced without a closing keyword, so GitHub's `closedByPullRequestsReferences` missed them.
3. **Mirror PR-level CI / merge blocks back to the parent issue:**
   - On Nov 18, `tessl-app` posted 13 CI-refusal comments on PRs `#121`–`#132`, not on their parent issues. When a PR cannot proceed because CI or checks are blocked, post `<!-- tessl-lifecycle: stage=blocked reason=ci_blocked pr=<pr-number> -->` on the parent issue (and `stage=pr_review` once green again).
4. **Reduce PR-comment noise or prefer Project/label signals if comment volume grows:**
   - On `mini-crm`, 60+ of the 100 most recent `/issues/comments` entries were PR-level `PR ready for review` / CI comments. Updating a single comment in place on PRs (or moving lifecycle state to a linked GitHub Project v2) keeps repo-wide issue history uncapped.

## Acceptance criteria

- [ ] Starting a Tessl Factory run on a labeled issue posts `<!-- tessl-lifecycle: stage=in_progress ... -->` on the issue.
- [ ] Deferring/blocking an issue posts `<!-- tessl-lifecycle: stage=blocked reason=... -->` on the issue.
- [ ] Opening a PR posts `<!-- tessl-lifecycle: stage=pr_review pr=<N> -->` on the issue **and** includes `Closes #<issue>` in the PR body.
- [ ] Narrative summary / CE trace / release-check comments do **not** include `<!-- tessl-lifecycle: ... -->`.
- [ ] A sample run on `yyeret/mini-crm` (or a test issue) transitions cleanly through `build_tessl_factory_issue_lifecycle()` in `github-analytics/issues.py` without falling back to prose heuristics.
