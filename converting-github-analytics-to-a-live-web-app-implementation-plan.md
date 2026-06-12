# Implementation Plan: Converting GitHub Analytics to a Live Web App & Chatbot

This plan details the transition of `github-analytics` from a static Python script into a live web server featuring a dynamic repository analyzer and an AI chatbot agent to query flow metrics.

---

## User Review Required

> [!IMPORTANT]
> 1. **AI Chatbot Backend Model**:
>    To power the "Antigravity AI Agent", we will support two LLM integrations in the FastAPI backend:
>    - **Google Gemini** (`google-generativeai` SDK, using the `GEMINI_API_KEY` env var).
>    - **OpenAI GPT** (`openai` SDK, using the `OPENAI_API_KEY` env var).
>    We will check the environment for either key and use the available one, prompting the user if both are missing.
> 2. **Dependency Additions**:
>    We will add a simple `requirements.txt` file including `fastapi`, `uvicorn`, and AI SDKs (`google-generativeai`, `openai`).

---

## Proposed Changes

### [github-analytics](file:///Users/yuvalyeret/Github/github-analytics)

To convert this into a web application, we will build a FastAPI backend and a premium single-page frontend.

---

### [Component: FastAPI Web Backend]

We will create a FastAPI Python web server that serves endpoints for repository analysis, static files, and agent chat completions.

#### [NEW] [app.py](file:///Users/yuvalyeret/Github/github-analytics/app.py)
Create a FastAPI server with the following routes:
- `POST /api/analyze`: Parses the repository URL, retrieves the `GITHUB_TOKEN` (via `bws` subprocess if missing from `os.environ`), runs the GraphQL collection pipeline in a separate thread, computes all flow metrics (CFD, Spearman correlation, stage percentiles, active WIP categories, Tetris packing lanes, and throughput), and returns a unified JSON dataset.
- `POST /api/chat`: Accepts a user message and the current repository dataset. Prompts an LLM (Gemini or OpenAI) with system instructions detailing Yuval's Flow Coaching methodology (descaling, leading/lagging metrics, finishing over starting, batch size reduction) and the raw JSON metrics, returning the agent's textual insights.
- Serving static assets: Mounts a static folder or directly serves `index.html` (the SPA frontend).

#### [MODIFY] [analyze.py](file:///Users/yuvalyeret/Github/github-analytics/analyze.py)
- Refactor the calculations in `analyze_and_build_report` into a clean Python function `compute_flow_metrics(data)` that returns the raw dictionary of calculations instead of writing a hardcoded HTML file, enabling the FastAPI endpoints to consume and return the raw data directly.

#### [NEW] [requirements.txt](file:///Users/yuvalyeret/Github/github-analytics/requirements.txt)
Define backend web app dependencies:
- `fastapi`
- `uvicorn`
- `google-generativeai`
- `openai`

---

### [Component: Premium Dashboard UI & Chatbot Frontend]

We will build a responsive, single-page application (SPA) featuring high-fidelity dark aesthetics, dynamic chart rendering, and an interactive chat assistant.

#### [NEW] [index.html](file:///Users/yuvalyeret/Github/github-analytics/index.html)
- **Visual Theme**: Deep indigo glassmorphic dark theme (`#0b0f19` background, `#141b2d` card background, purple/blue gradients, backdrop blurs, Outfit typography).
- **Control Bar**: A premium header with a text input for the GitHub Repository URL and a styled "Analyze Repository" button with micro-animations and loading spinners.
- **Dynamic Charts**:
  - Implement all 6 premium Chart.js charts: CFD, SLE-augmented Control Chart, Active WIP Age Category bands, PR size logarithmic correlation scatter, weekly throughput stacked bars, and the packed Comet Timeline.
  - Implement instant data binding: when a new repository is analyzed, the frontend dynamically updates all chart datasets and scales in place.
- **AI Chatbot Drawer**:
  - A persistent, glassmorphic floating chat drawer on the right side of the screen.
  - Scrollable message history with distinct bubbles for User and the Antigravity Agent.
  - A input bar with quick-prompt chips:
    - *"What is our 85% SLE cycle time?"*
    - *"Where is our primary bottleneck?"*
    - *"Do larger PRs correlate with longer delays?"*
    - *"List the oldest open WIP items."*
  - Uses simple REST API calls to the `/api/chat` backend, sending the current loaded repository dataset in the payload so the model has real-time context.

---

## Verification Plan

### Automated Tests
- Run `pip install -r requirements.txt` (or install manually).
- Launch the web server using `python3 -m uvicorn app:app --port 8000 --reload` from the terminal.
- Verify the server boots up and mounts static assets successfully.

### Manual Verification
- Open `http://localhost:8000` in a web browser.
- Verify that the layout loads with our premium glassmorphic dark theme and a placeholder state.
- Input a repository (e.g. `openclaw/openclaw` or `sveltejs/svelte`) and click "Analyze". Verify the loading spinner is displayed and all 6 charts dynamically transition and populate with data.
- Open the Chat drawer, send a question (e.g., *"What is the bottleneck?"*), and verify that the Antigravity AI agent answers with detailed, context-aware flow insights based on the loaded dataset.
