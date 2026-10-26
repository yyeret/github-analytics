# Private repository value intent and issue prioritization

## Outcome-oriented goal and leading indicators

An authenticated owner can inspect intended value in a private repository's open issues and give an agent a defensible next-work shortlist.

- Private reads use the caller's verified GitHub identity, never the deployment token: target 100% authorization-boundary checks passing.
- Private analysis cannot enter or be read through public/shared cache keys: target zero cross-user disclosures in API checks.
- Every assessed issue exposes four Fibonacci dimensions, their sum and uncertainty; indeterminate inputs remain unscored: target 100% contract checks passing.
- Run a bounded API study of up to 20 open yeret-agility-site issues using source-linked repository/business value drivers; report priorities, disagreements and limitations without claiming realized value.

## Users and outcomes

Repository owners and their agents need a ranked, inspectable backlog of intended contribution. Ranking informs selection; dependencies, effort, ownership and execution permission remain separate decisions. A logged-in browser identity must be explicitly authorized to this application before accessing private GitHub data.

Owner amendment, October 26: priority applies throughout refinement, implementation and learning, including merged work still on the board and ordering needs-yuval asks. Open/unlinked discovery is a convenience filter; explicit issue selection must accept any lifecycle state and PR linkage. The owner separately authorized milestone assignments using Hold/Normal/High; the initial application is bounded to the 20 studied issues. This supersedes the implementation-only shortlist and no-issue-mutation constraints below solely for that pilot; it does not authorize publication or execution.

## Solution and scenarios

Retain public analysis. Add GitHub authentication and an API bearer-token path for existing CLI identities. Verify identity and repository access, carry caller credentials only to GitHub, and scope private results to that identity. OAuth credentials remain server-side with short-lived sessions; state/PKCE protect browser authorization. Existing Google sign-in remains supported. Add an issue value endpoint independent of merged-PR flow metrics, including repositories with no merged PRs.

Reuse Gemini research and Jev scoring. Private research explicitly uses the caller credential; public research remains anonymous after metadata verification. Accept bounded owner-supplied strategic evidence so private business context need not be committed to this public code repository. Assess issue descriptions with the existing 1,500-character budget; include issue titles for issues because requests often state the proposed work there. Preserve description-only PR behavior. Keep explicit value eligibility independent of state or PR linkage, expose lifecycle metadata, and never infer that a textual mention proves no implementation exists.

## Hypotheses, conviction and learning

Richer owner context should make opportunity alignment more useful. This is medium conviction; coarse relative scores and weak issue descriptions can still produce misleading ranks. Compare the live shortlist with source evidence and inspect high/low/indeterminate examples. Do not change scores to fit desired rankings. Treat summed cost of delay as a contribution estimate, not WSJF or autonomous execution permission.

## Implementation units and verification

1. GitHub identity module and tests: API token verification plus OAuth authorization/callback/session/logout with bounded storage and sanitized errors. No client-secret provisioning or account changes in this run.
2. Reusable issue/private value engine and tests: preserve PR contracts, authorize private source retrieval explicitly, support bounded owner evidence and issue-specific item wording.
3. Root API integration, authenticated issue collection, private cache boundaries, build/docs/privacy and reusable CLI study. Verify revoked/insufficient access, identity isolation, zero-PR issue analysis, truncation and partial failures.
4. Read-only yeret-agility-site research: current business priorities and repository strategy, private source bundle, open-issue inventory. No site edits or issue mutations. Bound live study to 20 Jev requests and three Gemini requests initially, with exact key-free receipts retained privately.

Native execution; auth module, value engine and read-only business research are independent units with disjoint files. Root owns integration, full checks and commits. Use standard Python/HTTP APIs compatible with macOS/Windows and Python Workers; no global harness changes. Preserve unrelated dirty primary and d3fc checkouts. Branch starts from unmerged PR22's reviewed head; do not merge it or publish a dependent PR without authorization.

## Definition of done

Relevant red-before-green checks, required Ruff/full Python suite and frontend compatibility checks pass; substantive changes simplified and reviewed. Local API study uses the current gh identity and existing authorized Gemini/Jev keys. Private source text/results stay in ignored local evidence or private memory. GitHub OAuth server configuration may require a separate account setup decision, but the API path and local evaluation must work without it. No publication, merge, deployment, backlog mutation or automatic execution of ranked work.

## Suggested /goal loop prompt

```text
/goal Enable authorized private-repository value-intent audits and an inspectable issue shortlist for yeret-agility-site. Scope: docs/plans/2026-10-26-private-repo-issue-value-plan.md. Targets: all auth/cache isolation checks pass; all scored issues expose four Fibonacci dimensions/sum/uncertainty; indeterminate inputs unscored; bounded <=20-issue API study and source-linked value drivers saved privately. Re-measure each cycle, complete the next dependency-ready unit, record progress in commits/handoff, stop when verified and reviewed or concretely blocked. Preserve dirty checkouts and PR22. Do not publish, merge, deploy, mutate issues or execute prioritized work without authorization.
```
