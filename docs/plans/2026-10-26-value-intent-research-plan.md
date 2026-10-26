# Value-intent scans grounded in repository research

## Outcome-oriented goal and leading indicators

Repository owners can understand the contribution their PRs intend to make, using product context richer than a README and inspectable relative scores.

- Every successful research profile identifies users, critical workflows, value drivers and product risks, with supported source references; target100% of accepted profiles.
- Indeterminate descriptions receive no fabricated numeric total; target100% of blank/template/vague controls, while terse identifiable preventive work remains assessable.
- Preserve four Fibonacci dimensions and sum, description[:1500], bounded execution and safe diagnostics; target100% relevant contract checks passing.
- A resumable evaluation set contains40 public PR descriptions across2 contrasting repos plus focused controls, with explicit provisional label provenance and a held-out partition; report coverage/agreement without claiming human-validated accuracy.

## Users, outcomes and scenarios

Owners auditing intended contribution need to distinguish direct user benefit, time sensitivity, future enablement and preventive product risk. A terse2FA or CI-feedback change should express inferable intent; an empty template should remain indeterminate. Preventive risk reduction does not require a prior incident. Implementation risk and actual benefit realization are out of scope.

## Solution, hypotheses and learning

Keep Jev as the classifier. Gemini first chooses useful repository documents from a bounded public catalog, then researches what is valuable using the selected documents plus README, recent releases and issue signals. Questions probe beneficiaries, critical jobs, adoption/friction, strategic options and risks to those jobs. Profiles distinguish stated/inferred drivers, cite supplied source IDs and expose gaps. All retrieval stays on GitHub for the verified public repository; no arbitrary model-suggested URLs or external web crawling.

Hypothesis: better context and intent-specific rubrics reduce irrelevant strategic associations and missed preventive/developer contribution. This is plausible, not established accuracy. Draft reviewer labels and model agreement are not human ground truth. Freeze a holdout and preserve exact inputs/results for later review. Existing SpecKit study supplies regression cases, not a source of fitted thresholds.

## Implementation units and verification

1. **Research module:** new repo_research.py and focused tests, independent of application imports. Async build_research_sources(repo, metadata_sources, token, request, generate, diagnostics) uses existing async request/generate callbacks; returns bounded source records plus research planning limitations. Source records have id, kind, path/url, text, truncated. Public source fetches omit credentials following metadata verification. Model selects at most5 allowed document paths from a bounded catalog; code validates selection. Root integration owns shared deadlines and context schema. Verify selection membership, public-only egress, caps and graceful optional-source failures.
2. **Intent classification:** product_value.py and its integration tests. Rewrite questions/anchors to intended contribution, include developer productivity/preventive risks, add bundled typed evidence-sufficiency judgment, preserve inferred intent from terse changes. Treat indeterminate judgment as unscored, distinguish nonempty insufficient description in safe messages. Keepexisting probabilities/modal mapping and sum. Verify actual outbound payload and inadequate-description outcomes; no mocked score assertions presented as model accuracy.
3. **Integration:** root integrates source research, richer validated context schema and profile into existing API result and Jev state. Reuse existing model transports, cancellation, provider keys and safe diagnostics; keep90s overall/45s context budget. Deep research happens once per analysis and benefits existing analysis caching; no new database. Bump cache/result version. README documents API semantics/source budgets. UI redesign and caller-editable profiles are deferred; current API exposes research profile and existing dashboard remains compatible.
4. **Evaluation:** reusable CLI and40-case public fixture with draft labels/holdout plus focused intent controls. No credentials in artifacts. API calls opt-in, resume by input/version fingerprint, bounded budget. Offline scoring reports coarse disagreements, abstention and coverage. Live run compares saved baseline/new outputs without tuning holdout; accurately disclose label authorship and unresolved disagreements.

Research and evaluation implementation can proceed in parallel on disjoint files from the committed baseline. Product integration depends on research-module completion and is sequential. Root owns authoritative verification and commits. Use native execution; configured external work engine is off.

## Definition of done and boundaries

Tests first for behavior-bearing changes; focused checks then required Ruff/fullPython suite and relevant frontend compatibility checks. Use local API for a bounded live proof on public sources with existing authorized keys. No production deployment or merge. Python3.11/3.13 and Workers/Pyodide compatible APIs; no platform-specific runtime tools/new dependencies. EvaluationCLI works on macOS/Windows via pathlib/std library and environment keys. No harness-global changes. Review/simplification follow repository workflow before delivery; publication only within existing user authority.

## Suggested /goal loop prompt

```text
/goal Make value-intent scans faithful and useful using bounded repository research. Scope: docs/plans/2026-10-26-value-intent-research-plan.md. Targets: accepted profiles all source-linked; all indeterminate controls unscored and terse preventive work assessable; all description/Fibonacci/deadline/privacy contracts passing;40publicPRs/2repos with provisional labels and a holdout plus resumable evaluator. Each cycle inspect current evidence, complete the next dependency-ready unit, run relevant checks and record progress in commits or a separate handoff. Preserve unrelated primary WIP. Stop when implemented, locally verified and reviewed, or report a concrete blocker; do not merge/deploy. Never call draft/model labels human ground truth or tune on the holdout.
```
