# Walkthrough: Live GitHub Flow Analytics Web Application & AI Chatbot

This walkthrough summarizes the complete architectural shift and implementation of **GitHub Flow & Value Stream Analytics**, transitioning it from a static report generator into a live web server featuring a dynamic single-page dashboard and an interactive AI flow coaching chatbot.

---

## Architecture Overview

The system is now structured as a modular client-server web application:

```mermaid
graph TD
    User([User Browser]) -->|Loads SPA| FastAPI[FastAPI Web Server (app.py)]
    User -->|POST /api/analyze| FastAPI
    User -->|POST /api/chat| FastAPI
    
    FastAPI -->|Executes query via gh CLI| GitHubAPI[GitHub GraphQL API]
    FastAPI -->|Computes metrics| AnalyticsEngine[Flow Analytics Engine (analyze.py)]
    FastAPI -->|Generates insights| GeminiAPI[Google Gemini 3.5 Flash API]
```

---

## Completed Implementations

### 1. Data Collection & Analytics Engine (`analyze.py`)
- **Refactored Compute Pipeline**: Extracted the core metric computations into a standalone, pure Python function `compute_flow_metrics(raw_data)`. This decoupled the mathematical computations (SLEs, Spearman correlation, WIP age classification, Tetris packing lanes, and throughput bins) from HTML generation, allowing direct consumption by the web backend.
- **Robust Subprocess Auth**: Added automatic environment cleanup to pop invalid parent-injected `GITHUB_TOKEN` values, enabling the GitHub CLI (`gh`) to cleanly fall back to the active logged-in keyring (`yyeret`) out-of-the-box.

### 2. FastAPI Web Server Backend (`app.py`)
- **API Endpoints**:
  - `POST /api/analyze`: Receives a repository URL/slug, runs the GraphQL query pipeline, caches the results in memory, and returns the computed flow metrics payload.
  - `POST /api/chat`: Accepts a user message, active repo slug, and chat history. Instantiates a contextual session with Google's state-of-the-art **Gemini 3.5 Flash** model, pre-injected with Yuval Yeret's flow coaching principles (reducing WIP, finishing over starting, batch size reduction, lead metrics) and the active repository's real-time metrics.
- **Environment Management**: Configured `python-dotenv` to automatically load `GEMINI_API_KEY` from the user's home directory `.env` file (`/Users/yuvalyeret/.env`).
- **Static Assets Serving**: Mounts and serves the single-page application frontend directly at the root (`/`) route.

### 3. Premium SPA Dashboard Frontend (`index.html`)
- **Visual Design**: Sleek indigo glassmorphic dark theme (`#080d18` background, `#141e35` card background, purple/blue gradients, and Outfit typography) with dynamic page-level transition states.
- **Instant Data Binding**: When a repository is analyzed, the dashboard shifts from a placeholder state to a populated dashboard. It binds the returned dataset to all 6 premium Chart.js charts:
  1. *Cumulative Flow Diagram (CFD)*: Tracking merged, in-review, and coding WIP over time.
  2. *Control Chart*: Cycle time scatter plot with horizontal lines at the 50% and 85% SLE thresholds.
  3. *Weekly Throughput Bar Chart*: Stacked bars categorized by contributor mix (Human, AI-Assisted, AI-Agentic).
  4. *Active WIP Age Scatter Chart*: Open PRs plotted against stages with colored threshold bands.
  5. *Batch Size Scatter Plot*: Correlation between lines churned and cycle time on a logarithmic scale.
  6. *PR Flow Timeline (Comet Chart)*: packed concurrency tracks (Tetris algorithm).
- **AI Flow Coach Floating Drawer**:
  - Persistent, sliding glassmorphic sidebar panel.
  - Quick-prompt chips to instantly query common flow questions.
  - Seamless message bubbles with custom markdown rendering.

---

## Verification & Validation

### 1. Server Boot & Health Checks
Launched the FastAPI application in the local python virtual environment:
```bash
./venv/bin/python3 app.py
```
*Result*: Server successfully bound to `http://0.0.0.0:8000`. Health queries to `/` returned 200 OK with the loaded HTML SPA shell.

### 2. End-to-End Analysis Pipeline
Tested repository analysis with a public repository:
```bash
curl -X POST http://127.0.0.1:8000/api/analyze -H "Content-Type: application/json" -d '{"repo_url": "openclaw/openclaw"}'
```
*Result*: Query successfully ran via keyring auth, collected all PR profiles and metrics, and returned the comprehensive dictionary payload of computed flow metrics (Status 200 OK).

### 3. AI Chatbot Completion
Tested the AI coaching completion drawer backend:
```bash
curl -X POST http://127.0.0.1:8000/api/chat -H "Content-Type: application/json" -d '{"repo": "openclaw/openclaw", "message": "What is our 85% SLE?", "history": []}'
```
*Result*: The backend fetched the loaded metrics for `openclaw/openclaw` from cache, built a highly customized flow coaching system prompt, successfully called the `gemini-3.5-flash` model, and returned structured flow coaching recommendations (Status 200 OK).
