## Phase 1 (Complete)
- [x] Enhance GraphQL Queries in `analyze.py` to collect additional open and closed PR details
- [x] Calculate Stage Percentiles (Coding, Review, Merge) in `analyze.py`
- [x] Compute Active WIP Stage Classification and Age in `analyze.py`
- [x] Implement pure Python Spearman Rank correlation utility in `analyze.py`
- [x] Implement PR Comet packing (Tetris algorithm) in `analyze.py`
- [x] Inject raw values and datasets as JSON to the HTML template in `analyze.py`
- [x] Add 85% SLE and Correlation Metric Cards to `dashboard.html` template
- [x] Enhance Control Chart with horizontal 50%/85% SLE lines in `dashboard.html` template
- [x] Render Active PR Age Chart with step-based background bands in `dashboard.html` template
- [x] Render PR Size vs Cycle Time Correlation Scatter Chart in `dashboard.html` template
- [x] Render PR Throughput Chart by Contributor Type in `dashboard.html` template
- [x] Render PR Comet Chart (Tetris Packing Flow Timeline) in `dashboard.html` template
- [x] Verify execution by running `python analyze.py` and viewing `dashboard.html`

## Phase 2 - Web Server & AI Chatbot (Complete)
- [x] Refactor `analyze.py`: extract `compute_flow_metrics(data)` that returns raw metrics dict
- [x] Create `requirements.txt` with fastapi, uvicorn, google-generativeai, httpx
- [x] Create `app.py` FastAPI server with `/api/analyze`, `/api/chat`, static file serving
- [x] Create premium `index.html` SPA with dynamic Chart.js charts + AI chatbot drawer
- [x] Verify web server boots and charts render dynamically
- [x] Verify AI chatbot responds with context-aware flow coaching insights
