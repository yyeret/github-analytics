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

