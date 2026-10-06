"""
GitHub Analytics - FastAPI Web Server
Serves the premium SPA and provides REST endpoints for:
  POST /api/analyze  - Collect & compute flow metrics for a GitHub repo
  POST /api/chat     - AI chatbot powered by Gemini using the loaded metrics
"""

import os
# Remove any invalid GITHUB_TOKEN to let the gh CLI use the active logged-in keyring
os.environ.pop("GITHUB_TOKEN", None)

import json
import asyncio
import subprocess
import concurrent.futures
import datetime
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# Load environment variables from home directory or local environment files
load_dotenv(Path.home() / ".env")
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Import our analytics engine
# ---------------------------------------------------------------------------
import sys
sys.path.insert(0, str(Path(__file__).parent))
from analyze import collect_data, compute_flow_metrics

# ---------------------------------------------------------------------------
# App Setup
# ---------------------------------------------------------------------------
app = FastAPI(title="GitHub Flow Analytics", version="1.0.0")

# In-memory cache: repo -> metrics dict
_metrics_cache: dict[str, dict] = {}

# Thread pool for running blocking IO (gh CLI calls, computation)
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)


# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------
class AnalyzeRequest(BaseModel):
    repo_url: str          # e.g. "https://github.com/sveltejs/svelte" or "sveltejs/svelte"
    force_refresh: bool = False


class ChatMessage(BaseModel):
    role: str              # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    repo: str              # "owner/name" slug used as cache key
    message: str
    history: list[ChatMessage] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_repo_slug(repo_url: str) -> str:
    """Extract 'owner/name' slug from a GitHub URL or pass-through if already a slug."""
    url = repo_url.strip().rstrip("/")
    if url.startswith("https://github.com/"):
        url = url[len("https://github.com/"):]
    if url.startswith("http://github.com/"):
        url = url[len("http://github.com/"):]
    # Remove trailing .git if present
    if url.endswith(".git"):
        url = url[:-4]
    # Remove any /tree/... or /blob/... path segments
    parts = url.split("/")
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    raise ValueError(f"Cannot parse repo slug from: {repo_url!r}")


def _run_analysis(repo: str, force: bool) -> dict:
    """Blocking function that collects raw data and computes metrics."""
    raw = collect_data(repo)
    metrics = compute_flow_metrics(raw)
    return metrics


def _get_gemini_key() -> Optional[str]:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if key:
        return key
    # Try fetching via bws CLI
    try:
        result = subprocess.run(
            ["bws", "secret", "get", "GEMINI_API_KEY"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return data.get("value")
    except Exception:
        pass
    return None


def _build_system_prompt(metrics: dict) -> str:
    repo = metrics.get("repo", "unknown")
    rs = metrics.get("recent_stats", {})
    p50 = metrics.get("p50_sle", 0)
    p85 = metrics.get("p85_sle", 0)
    corr = metrics.get("spearman_corr", 0)
    total_prs = rs.get("total", 0)
    avg_cycle = rs.get("avg_cycle", 0)
    avg_size = rs.get("avg_size", 0)
    flow_eff = rs.get("flow_efficiency", 0)
    human_r = rs.get("human_ratio", 0) * 100
    assisted_r = rs.get("assisted_ratio", 0) * 100
    agentic_r = rs.get("agentic_ratio", 0) * 100

    throughput = metrics.get("throughput_data", [])
    recent_weeks = throughput[-8:] if throughput else []
    avg_weekly = sum(w.get("total", 0) for w in recent_weeks) / len(recent_weeks) if recent_weeks else 0

    active_wip = metrics.get("active_wip_points", [])
    oldest_wip = sorted(active_wip, key=lambda x: x.get("y", 0), reverse=True)[:3] if active_wip else []

    stage_p = metrics.get("stage_percentiles", {})

    recent_prs_summary = metrics.get("recent_prs_summary", [])
    pr_list_str = "\n".join(
        f"  - PR#{p['number']}: {p['title'][:60]} | {p['classification']} | {p['cycle_time_hours']:.1f}h | {p['pr_size']} lines"
        for p in recent_prs_summary[:20] if p.get("cycle_time_hours")
    )

    issue_block = _build_issue_block(metrics.get("issues"))

    return f"""You are the **Antigravity AI Flow Coach** — an expert in software engineering flow metrics, 
Value Stream Mapping, and the Theory of Constraints. You help engineering teams use data to improve 
delivery speed, predictability, and quality.

Your coaching philosophy:
- Finish over Starting: reduce WIP, finish current work before pulling new work
- Flow over Utilization: optimize for end-to-end throughput, not individual busyness
- Small Batches: smaller PRs move faster with higher quality
- Lead Metrics: cycle time, WIP age, queue sizes (not just velocity/story points)

## Repository Being Analyzed: {repo}

### Key Flow Metrics
- **Total PRs analyzed**: {total_prs}
- **Average Cycle Time**: {avg_cycle/24:.1f} days ({avg_cycle:.1f} hours)
- **50th Percentile SLE (Service Level Expectation)**: {p50/24:.1f} days
- **85th Percentile SLE**: {p85/24:.1f} days — this is the primary SLE target
- **Average PR Size**: {avg_size:.0f} lines changed
- **Flow Efficiency**: {flow_eff:.1f}% (% of cycle time in active work vs. waiting)
- **Spearman Correlation (Size→CycleTime)**: {corr:+.2f} ({_interpret_corr(corr)})
- **Average Weekly Throughput (last 8 weeks)**: {avg_weekly:.1f} PRs/week

### Contributor Mix
- Human: {human_r:.0f}% | AI-Assisted: {assisted_r:.0f}% | AI-Agentic: {agentic_r:.0f}%

### Stage Percentiles (P50/P85 in days)
- Coding: {stage_p.get('coding',{}).get('p50',0):.1f}d / {stage_p.get('coding',{}).get('p85',0):.1f}d
- Review Queue: {stage_p.get('review',{}).get('p50',0):.1f}d / {stage_p.get('review',{}).get('p85',0):.1f}d
- Merge Delay: {stage_p.get('merge',{}).get('p50',0):.1f}d / {stage_p.get('merge',{}).get('p85',0):.1f}d

### Oldest Active WIP Items (by age in days)
{chr(10).join(f'  - PR#{w["num"]}: {w["title"][:60]} | Stage: {w["x"]} | Age: {w["y"]:.1f}d' for w in oldest_wip) or "  No open PRs detected"}

### Sample of Recent PRs
{pr_list_str or "  No recent PR data available"}

{issue_block}Answer questions about this data with specific, actionable coaching insights. 
Reference specific PR numbers or metric values when relevant.
Keep answers concise but insightful. Use markdown formatting.
"""


def _build_issue_block(issues: Optional[dict]) -> str:
    """Prompt section for the Issues analytics; empty string when issues are unavailable."""
    if not issues:
        return ""
    ct = issues.get("cycle_time", {})
    modern, baseline = ct.get("modern", {}), ct.get("baseline", {})
    net_8w = issues.get("net_8w", 0)
    stale = issues.get("stale", {})
    res = issues.get("resolution", {})
    days = lambda hours: f"{hours / 24:.1f}d"
    oldest = stale.get("items", [])[:3]
    oldest_str = "\n".join(
        f"  - #{i['number']}: {i['title'][:60]} | idle {i['days_since_activity']:.0f}d" for i in oldest
    ) or "  None"
    truncated = ""
    if issues.get("closed_truncated") or issues.get("open_truncated"):
        truncated = "\n- Note: issue sample was capped, so early weeks or the open count may be incomplete"
    return f"""### Issue Backlog
- **Open issues now**: {issues.get('open_now', 0)}
- **Net backlog change (last 8 weeks)**: {net_8w:+d} issues (arrivals minus resolutions)
- **Issue cycle time, modern era**: median {days(modern.get('median_hours', 0))}, 85th pct {days(modern.get('p85_hours', 0))} ({modern.get('n', 0)} closed)
- **Issue cycle time, 2021 baseline**: median {days(baseline.get('median_hours', 0))}, 85th pct {days(baseline.get('p85_hours', 0))} ({baseline.get('n', 0)} closed)
- **Stale issues** (no comment for {stale.get('threshold_days', 90)}+ days): {stale.get('count', 0)}
- **Closed via a linked PR**: {res.get('pct_with_pr', 0):.0f}% ({res.get('with_pr', 0)} of {res.get('closed_total', 0)}){truncated}

Oldest stale issues:
{oldest_str}

"""


def _interpret_corr(corr: float) -> str:
    if corr > 0.6:
        return "strong positive — larger PRs significantly delay delivery"
    elif corr > 0.3:
        return "moderate positive — batch size is a meaningful drag"
    elif corr > 0.1:
        return "weak positive — some batch size effect"
    elif corr > -0.1:
        return "negligible — batch size not a primary factor here"
    else:
        return "negative — larger PRs may be moving faster (review your data)"


# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------

@app.post("/api/analyze")
async def analyze_repo(req: AnalyzeRequest):
    try:
        repo = _parse_repo_slug(req.repo_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Run blocking collection+computation in thread pool
    loop = asyncio.get_event_loop()
    try:
        metrics = await loop.run_in_executor(_executor, _run_analysis, repo, req.force_refresh)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {e}")

    # Store in cache keyed by slug
    _metrics_cache[repo] = metrics
    return JSONResponse(content=metrics)


@app.post("/api/chat")
async def chat(req: ChatRequest):
    repo = req.repo
    metrics = _metrics_cache.get(repo)
    if not metrics:
        raise HTTPException(
            status_code=404,
            detail=f"No metrics loaded for '{repo}'. Please analyze the repository first."
        )

    api_key = _get_gemini_key()
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="No Gemini API key found. Set GEMINI_API_KEY in your environment."
        )

    system_prompt = _build_system_prompt(metrics)

    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name="gemini-3.5-flash",
            system_instruction=system_prompt,
        )

        # Build conversation history for multi-turn chat
        history_for_gemini = []
        for msg in req.history:
            role = "model" if msg.role == "assistant" else "user"
            history_for_gemini.append({"role": role, "parts": [msg.content]})

        chat_session = model.start_chat(history=history_for_gemini)
        response = chat_session.send_message(req.message)
        answer = response.text

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI error: {e}")

    return JSONResponse(content={"reply": answer})


# ---------------------------------------------------------------------------
# Static File Serving (index.html SPA)
# ---------------------------------------------------------------------------

STATIC_DIR = Path(__file__).parent

@app.get("/", response_class=HTMLResponse)
async def root():
    html_path = STATIC_DIR / "index.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>index.html not found</h1>", status_code=404)


# ---------------------------------------------------------------------------
# Dev entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
