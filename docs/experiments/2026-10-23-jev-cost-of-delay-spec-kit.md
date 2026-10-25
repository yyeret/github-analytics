# Jev cost-of-delay trial on Spec Kit

Local implementation: four independent Fibonacci scores, summed in code. This is a SAFe-inspired relative estimate, using the four elements requested; it is not monetary cost of delay or a WSJF ratio.

## Verification

- HTTP POST `/api/analyze` returned 200 in 9.7 seconds; all 30 selected PRs were assessed with no Jev failures.
- Used the previously captured public GitHub collection, a recorded valid live Gemini product-context response, and live Jev 1.14.0 requests. This was not a hosted Workers or UI test.
- One earlier fresh-context API trial stopped at context generation. A subsequent diagnostic Gemini call produced valid context; that exact response was reused for the scoring run.
- Four Jev Score questions share one request per PR. Each dimension uses the highest-probability level, mapped to `1, 2, 3, 5, 8, 13`; code sums those four scores. Ties choose the lower level.
- All 30 time-criticality scores were 1: no imminent deadlines or active incidents were evidenced in the selected PR text. Do not equate this with a confirmed absence of urgency.
- The 30 Jev requests used 54,340 input tokens. Exact request/response pairs and the full API result are local, ignored files under `.cache/jev-cost-of-delay/`: `live-jev-requests.jsonl` and `spec-kit-jev-api-response.json`. They are not included in this repository; the sample table below is the published evidence.
- Request keys override configured defaults, do not mutate process environment, and bypass hosted shared caches. Caller-funded coaching snapshots use an opaque analysis ID scoped to the authenticated session. No keys occur in saved request/response evidence.
- Automated suite after PR feedback: 262 passed, 94.04% coverage. Ruff, six security stages, JavaScript syntax and renderer smoke checks passed. Browser layout was not exercised, following the API-first request.
- Compound Engineering code review completed (`jev-cost-of-delay-f80dca31`). Its one actionable finding, preserving hosted coaching for caller-key analyses, was fixed and verified with a failing-then-passing regression test. Same-session access works; other sessions cannot retrieve the snapshot. Separate tests cover single-header overrides, lower-score ties, malformed distributions, and Jev timeout isolation. The external peer attempt failed on expired OAuth; the review used its local adversarial fallback.

Two synthetic contrast probes used the same rubric: an actively exploited account-takeover incident with a four-hour deadline scored 13/13/3/8 (total 37), while cosmetic footer spacing scored 1/1/1/1 (total 4). These are checks of discrimination, not calibration evidence.

PR follow-up adds a 90-second total assessment budget that preserves completed scores, shortens unavailable-configuration caching to ten minutes, and prevents anonymous hosted chat from falling back to a different analysis. The local server remains a trusted single-user process. Each caller-key request starts a separate analysis; no cross-request deduplication contract is provided.

## Sample results

| PR | Value | Time criticality | Opportunity runway | Risk reduction | Total |
| --- | ---: | ---: | ---: | ---: | ---: |
| [#5065](https://github.com/github/spec-kit/pull/5065) fix(integrations): pass the Junie dispatch prompt via --task | 8 | 1 | 2 | 5 | 16 |
| [#5063](https://github.com/github/spec-kit/pull/5063) chore: release 1.2.3, begin 1.2.4.dev0 development | 1 | 1 | 1 | 1 | 4 |
| [#5054](https://github.com/github/spec-kit/pull/5054) Feat/4913 workflow mcp inventory | 3 | 1 | 8 | 5 | 17 |
| [#5055](https://github.com/github/spec-kit/pull/5055) fix(events): preserve dispatcher ownership during cleanup | 5 | 1 | 2 | 5 | 13 |
| [#5020](https://github.com/github/spec-kit/pull/5020) feat: add .presetignore support | 3 | 1 | 5 | 3 | 12 |
| [#5104](https://github.com/github/spec-kit/pull/5104) feat: Add Hermes Desktop integration for Spec-Kit | 8 | 1 | 8 | 1 | 18 |

The data-loss prevention PR #5055 had risk probabilities of 51% for 5 and 42% for 8. That makes the selected 5 a close call rather than a high-certainty rating. The probability distribution remains available in the API and dashboard.

In the earlier four-PR probe using a shorter manually supplied product context, #5055 totalled 17; the full context run scored 13. Context and model uncertainty affect rankings. No human-rated benchmark or claim of calibrated product-value accuracy was established by this trial.

## API usage

Configure local `GEMINI_API_KEY` and `TYPESAFE_API_KEY`, or pass request headers `X-Gemini-Api-Key` and `X-TypeSafe-Api-Key`. Send JSON `{"repo_url":"github/spec-kit","force_refresh":true}` to `POST /api/analyze`. Hosted users can provide keys per analysis or administrators can configure runtime secrets. Keys do not replace the hosted app’s session authentication.

## Description-only follow-up, 2026-10-25

The classification input now contains the first 1,500 characters of the PR description and the compact repository context. Titles remain response metadata; Jev does not receive them, changed-line counts, or framing labels. Empty descriptions return `insufficient_context` with a null total and no Jev call.

A local FastAPI `POST /api/analyze` probe reused saved public Spec Kit descriptions and valid recorded repository context. Five live `jev-1.14.0` requests succeeded; an empty-description control made no provider request. The endpoint returned HTTP 200 in 0.25 seconds. This was not fresh repository collection, a live Gemini context call, or a hosted Workers run.

| PR | Value | Time criticality | Opportunity runway | Risk reduction | Total |
| --- | ---: | ---: | ---: | ---: | ---: |
| 4960 | 5 | 1 | 5 | 5 | 16 |
| 5065 | 5 | 1 | 2 | 5 | 13 |
| 5062 | 3 | 1 | 2 | 1 | 7 |
| 5066 | 1 | 1 | 1 | 2 | 5 |
| 4993 | 2 | 1 | 2 | 1 | 6 |

The absence of evidenced urgency kept all time-criticality scores at 1. The authentication and integration changes ranked above the catalogue, dependency, and community-page changes. This sample is a functional check, not a human-rated calibration benchmark; truncation can omit relevant evidence. Jev's own rate limits still apply.

Exact key-free requests and responses are retained locally under the ignored `.cache/jev-description/` directory.
