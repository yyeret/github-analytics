# Insights-First Operational Review for UI & MCP

## Outcome-Oriented Goal

Shift both the GitHub Flow Analytics web UI (`index.html`) and MCP server/app (`mcp_server.py`, `mcp/dashboard.html`, `mcp/dashboard.js`) from a raw chart/metrics dump to an **insights-first operational review** experience: users and MCP host agents immediately see ranked key insights, operational review questions, and the 3–4 most interesting data slices with evidence, while all backing charts, tables, and interactive slice filters remain one click (or one tool call) away without cluttering the initial view.

### Measurable Leading Indicators

1. **Zero-click operational review signal**: 100% of analyzed repositories (public, private, PR-only, issue-only, or combined) produce a structured `operational_review` payload containing ranked `insights`, `review_questions`, and `slices` (top 3–4 highlighted + all available drill-downs), even when `GEMINI_API_KEY` is absent or fails.
2. **Insights-first default surface (Web + MCP)**: Both the web UI and the MCP inline dashboard open on the Operational Review view above the fold, with backing charts collapsed/secondary and directly linked from each insight via "Inspect backing data" and "Ask Coach".
3. **Targeted MCP drill-down & privacy safety**: `show_github_dashboard` defaults to `view="insights"` and returns insights/questions/slices in `structuredContent` without leaking private item titles/bodies; `inspect_flow_slice` returns focused slice breakdowns and evidence on demand.
4. **Verification & coverage**: `python -m ruff check .` passes with 0 errors, `python -m pytest --cov --cov-report=term-missing` passes with ≥90% branch coverage (including `operational_review.py`), `scripts/build-mcp-dashboard.py` builds cleanly, and browser verification confirms the live UI renders insights, slice drill-downs, and interactive backing filters cleanly.

## Architecture

### 1. Deterministic Insights & Slice Engine (`operational_review.py`)

Pure-Python module (included in `scripts/prepare-cloudflare-build.py` and `pyproject.toml` coverage) that computes `build_operational_review(metrics)` directly from `compute_flow_metrics()` output (+ optional `product_value`):

- **Candidate Slices** (computed only when underlying data exists; missing fields are never treated as zero):
  1. `contributor_mode`: Human vs AI-Assisted vs AI-Agentic (PR count, share, median/P85 cycle time, avg PR size when available, avg review loops when available).
  2. `batch_size`: PR size buckets (`XS <50`, `S 50–199`, `M 200–499`, `L 500+` LOC) + Spearman correlation (skipped cleanly when hosted REST collector has no size data).
  3. `flow_predictability`: P50 vs P85 tail ratio, recent 4-week vs prior 4-week throughput and cycle time trend, pre-AI baseline comparison (when present).
  4. `wip_aging`: Active PRs exceeding P85 SLE, stage concentration (`1. Active Coding`, `2. Review Queue`, `3. Merge Delay`), oldest active PRs.
  5. `backlog_health`: Open issues, 8-week net backlog delta, stale issue share, lifecycle stage bottlenecks (when lifecycle CFD exists), issue cycle time vs baseline.
  6. `value_framing`: Content framing distribution (`Input`/`Activity`/`Output` vs `Outcome`/`Impact`, clearly stating intent-not-outcome) + cycle time by framing level + Expected Cost of Delay highlights (when `product_value.status == "ok"`).
- **Interestingness Scoring**: Each slice and candidate insight gets a deterministic `score` (0–100) and `severity` (`high`, `medium`, `info`) based on concrete anomalies (e.g., fat-tail ratio > 3x, >25% active PRs older than P85 SLE, contributor-mode cycle-time gap > 1.5x, strong size-to-cycle correlation |ρ| ≥ 0.35, net backlog growing >10% or stale share ≥30%, >70% activity/output framing, high-CoD items with slow cycle time).
- **Highlighted Slices**: Top 3–4 highest-scoring slices are flagged `featured: true` with a crisp deterministic `insight` summary, `headline_metric`, `rows` (compact structured comparison), and `drilldown` target (`view`, `chart_id`, `filter`).
- **Key Insights & Review Questions**: Top 3–5 ranked insights, each with `id`, `title`, `summary`, `severity`, `evidence` (compact metrics + item numbers), `review_question` (specific operational review question for the team), `slice_id`, and `drilldown` target.
- **Privacy boundary**: Summary/public-safe fields use item numbers (`#42`) and aggregate stats; raw item titles live only in detail item records and are omitted from MCP `structuredContent` when `visibility == "private"`.

### 2. Optional Gemini Operational Review Synthesis (`synthesize_operational_review`)

- Runs during `/api/analyze` (both `cloudflare_app.py` and `app.py`) right after deterministic `operational_review` is built and `product_value` is assessed, then cached alongside `metrics` in KV/memory.
- Sends **only** the compact deterministic operational review summary (aggregate metrics, slice comparisons, item numbers — never raw private titles/bodies) to Gemini (`gemini-2.5-flash` / `product_value.gemini_json` or `github_api.request_json` with structured JSON schema and short timeout).
- Skips Gemini when `gemini_key` is absent or when `product_value` indicates a repository visibility change failure.
- Merges sharpened `executive_summary`, refined `insights` wording, `review_questions`, and `slice_insights` onto the deterministic structure, setting `synthesis_source: "llm"` (or `"deterministic"` on fallback).

### 3. MCP Server & Inline Dashboard (`mcp_server.py`, `mcp/dashboard.html`, `mcp/dashboard.js`)

- `show_github_dashboard`:
  - `view` defaults to `'insights'` (supports `'insights'`, `'slices'`, `'overview'`, `'flow'`, `'work'`, `'batch'`, `'issues'`, `'value'`).
  - `summary()` returns `executive_summary`, `key_insights`, `review_questions`, `featured_slices`, `available_slices`, plus compact `metrics_summary` (preserving backward-compatible keys like `repo`, `view`, `visibility`, `analysis_id`, `captured_at`, `merged_pr_sample`, `p50_cycle_hours`, `p85_cycle_hours`, `active_prs`, `recent_complete_weeks`, `open_issues_in_sample`, `open_issues_capped`, `net_backlog_8w`, `stale_issues_in_sample`).
  - Redacts raw item titles from `structuredContent` for private repos.
- New MCP tool `inspect_flow_slice`:
  - Arguments: `repo`, `slice` (`contributor_mode`, `batch_size`, `flow_predictability`, `wip_aging`, `backlog_health`, `value_framing`), optional `analysis_id`, `allow_private`.
  - Reads the cached snapshot (or analyzes if needed) and returns focused slice breakdown, evidence items (titles redacted for private repos in `structuredContent`, full details in `_meta`), operational review questions for that slice, and opens/focuses the matching backing view in the inline app.
- MCP Inline App (`mcp/dashboard.html`, `mcp/dashboard.js`):
  - Adds **Insights** (default active tab) and **Slices** tabs ahead of backing views (`Flow`, `WIP`, `Batch`, `Backlog`, `Value`, `Issue CoD`, `Coach`).
  - Clicking "Inspect backing data" on an insight or slice jumps directly to the relevant backing view and applies the slice filter.

### 4. Web UI (`index.html`, synced to `public/index.html` & `public/mcp-dashboard.html`)

- Above the fold inside `#dashboard`:
  - **Operational Review Brief** (`#insights-section`): Executive summary + 3–5 ranked Insight & Review Question cards, each with severity badge, evidence pills, **"Inspect backing data →"** button, and **"Ask Coach"** button (opens Flowy pre-populated with that review question).
  - **Most Interesting Slices** (`#slices-section`): 2–4 auto-selected slice cards showing compact comparison tables/bars + insight caption + **"Drill down →"** button.
- Secondary / Below the fold:
  - **Backing Data & Charts** (`#backing-data-section`): Clean collapsible/toggleable drawer (collapsed by default with compact KPI strip + "Explore backing charts & filters" toggle; auto-expands when clicking any "Inspect backing data" or "Drill down" action).
  - **Interactive Slice Filter Bar** inside Backing Data: Filter PR charts and item tables by **Contributor Mode** (`All`, `Human`, `AI-Assisted`, `AI-Agentic`), **PR Size** (`All`, `XS`, `S`, `M`, `L`), **Framing** (`All`, `Outcome/Impact`, `Output`, `Activity`, `Input`, `Unknown`), and **Time Window** (`All`, `Last 4w`, `Last 8w`), with live item count badge and reset button.
- Shared markup & renderer markers (`<!-- Shared dashboard start -->` .. `<!-- Shared dashboard end -->`, `// Shared dashboard renderer start` .. `// Shared dashboard renderer end`) kept intact so `scripts/build-mcp-dashboard.py` bundles the new insights/slices sections cleanly for the MCP inline app.

## Suggested `/goal` Loop Prompt

```text
Drive the insights-first UI and MCP implementation in /Users/yuvalyeret/Github/github-analytics-insights-first to completion.
Re-measure each cycle against the 4 leading indicators in docs/plans/2026-11-23-insights-first-ui-and-mcp.md:
1. Run `.venv/bin/python -m ruff check .` and `.venv/bin/python -m pytest --cov --cov-report=term-missing` (coverage >= 90%).
2. Run `.venv/bin/python scripts/build-mcp-dashboard.py` and verify `public/index.html` matches `index.html` and `public/mcp-dashboard.html` builds cleanly.
3. Verify the live UI in a browser (insights above the fold, backing data collapsed until expanded/drilled into, slice filters updating charts, coach handoff working).
Stop when all checks pass with zero regressions, or report any concrete blocker.
```
