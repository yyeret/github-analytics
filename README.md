# GitHub Flow & Value Stream Analytics

A framework and dashboard prototype designed to extract and analyze flow metrics from public GitHub repositories, specifically focusing on the relationship between local AI usage, moving system bottlenecks (Theory of Constraints), and high-level strategic outcomes mapped to Scrum.org's Evidence-Based Management (EBM) framework.

## Overview

Modern developer dashboards (such as Jellyfish or DX) focus heavily on engineering-centric activity metrics (DORA metrics, pull request cycle times, developer satisfaction). 

This project implements a higher-level perspective:
1. **AI Adoption is Local Optimization**: Measuring how local coding acceleration affects downstream stages.
2. **Theory of Constraints (TOC)**: Visualizing whether AI code generation accelerates time-to-market or merely shifts the system bottleneck to review, QA, and release phases.
3. **Evidence-Based Management (EBM)**: Translating flow efficiency directly into Ability to Innovate (A2I), Time-to-Market (T2M), and Value metrics.

## Documentation

- **[Feasibility Study](file:///c:/Users/yuval/Github/github-analytics/feasibility_study.md)**: A detailed analysis of public GitHub API capabilities, data extraction strategies, AI footprint detection (explicit and heuristic), and the EBM metric mapping.

## Dashboard Capabilities

The visual dashboard (`dashboard.html`) renders a premium dark-themed developer intelligence interface featuring:
1. **Multi-Stage Kanban CFD (Stacked Line Chart)**: Maps the complete closed value stream system from the bottom up: Merged Code (resolved), PR Review Queue (QA/gates), Active Coding (work-in-progress), and Backlog Queue (upstream demand).
2. **Chronological Scatter Plot**: Chronologically plots pull requests against their actual merge dates on a linear time scale, color-coded by Human, AI-Assisted, and AI-Agentic contributors.
3. **Upstream-to-Merge Value Stream Map**: Visually parses the end-to-end issue lifecycle, calculating Backlog Wait, Coding Time, Review Queue Delay, and Merge Delay to pinpoint the system constraint.
4. **Era Comparison Scorecard**: Contrasts historical baselines against modern metrics, including a mathematically calculated **Flow Efficiency (Active/Total)** ratio.

## Usage

To fetch fresh data and compile the dashboard for any public GitHub repository, run the `analyze.py` script:

```bash
# Analyze Svelte (traditional framework with rich history)
python analyze.py sveltejs/svelte --refresh

# Analyze OpenClaw (modern agentic AI system created in late 2025)
python analyze.py openclaw/openclaw --refresh
```

To view the generated dashboard, open `dashboard.html` directly in your web browser. Cache files are stored locally in `raw_data.json` to prevent API rate-limiting.

The web UI keeps each analyzed repository's GitHub data under `.cache/repositories/` on the server. Reopening a repository, including after a browser reload or server restart, uses that saved data without querying GitHub again. The UI remembers the last repository in the browser and displays when its data was captured. Use **Refresh data** to fetch the latest GitHub data; saved data otherwise remains until the server cache is removed. Hosts with an ephemeral filesystem need persistent storage for the cache to survive a deployment restart. The CLI's `raw_data.json` cache is separate.


## Testing

CI (`.github/workflows/ci.yml`) runs on every PR and push to `main`. To run the same checks locally:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
ruff check .
pytest --cov            # fails below the coverage floor in pyproject.toml
pytest -m unit          # or -m integration / -m e2e
node --test tests/frontend/*.test.cjs  # UI reload and refresh logic, no browser
```

Tests never touch the network, the `gh`/`bws` CLIs, or Gemini; `subprocess.run` is blocked by default and each test runs in a temp directory.


## Issue and PR content framing

The API, web dashboard, AI coach context, and generated static report classify
sampled issues and PRs from their own titles and bodies as **Input, Activity,
Output, Outcome, or Impact**. Empty content is **Unknown**. This describes stated
intent, not achieved customer or business value. Counts represent items, not effort.
The dashboard shows separate PR and issue distributions plus filterable items and
classification rationales. Historical baseline items are excluded from this view.

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
