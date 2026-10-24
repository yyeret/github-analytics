"""Expected product value of each PR, judged by Gemini against the repository's product.

One call per analysis turns the README and repository description into product
context (value proposition, lean canvas, strategic goals). One call per sampled
PR then judges the value that PR is expected to deliver. This is an expectation
from stated intent and changed code size, never a measurement of realized impact,
and it is kept separate from the rule-based content framing.
"""
import asyncio
import base64
import json

from github_api import request_json
from value_classification import compute_value_classification

VERSION = "gemini-product-value-1"
MODEL = "gemini-3.5-flash-lite"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
VALUE_LEVELS = ["High", "Medium", "Low", "Unclear"]
CONFIDENCE_LEVELS = ["High", "Medium", "Low"]
DEFAULT_MAX_PRS = 30
README_CHARS = 12_000
BODY_CHARS = 4_000
CONCURRENCY = 5
GEMINI_TIMEOUT = 25
CANVAS_KEYS = ("customer_segments", "problem", "solution", "unique_value_proposition",
               "channels", "revenue_streams", "cost_structure", "key_metrics", "unfair_advantage")
NARRATIVE_KEYS = ("value_summary", "beneficiaries", "why_it_matters")
DISCLAIMER = ("Expected value judged by Gemini from PR text and the repository's stated product. "
              "Not verified realized impact. Clear outcome framing alone does not imply high value.")

_STRING = {"type": "STRING"}
CONTEXT_SCHEMA = {"type": "OBJECT", "properties": {
    "product_summary": _STRING,
    "value_proposition": _STRING,
    "lean_canvas": {"type": "OBJECT", "properties": {key: _STRING for key in CANVAS_KEYS}},
    "strategic_goals": {"type": "ARRAY", "items": _STRING},
    "missing_context": _STRING,
}, "required": ["product_summary", "value_proposition", "lean_canvas", "strategic_goals", "missing_context"]}

ASSESSMENT_SCHEMA = {"type": "OBJECT", "properties": {
    "expected_value": {"type": "STRING", "enum": VALUE_LEVELS},
    "value_summary": _STRING,
    "beneficiaries": _STRING,
    "why_it_matters": _STRING,
    "strategic_goal": _STRING,
    "confidence": {"type": "STRING", "enum": CONFIDENCE_LEVELS},
    "missing_context": _STRING,
}, "required": ["expected_value", "value_summary", "beneficiaries", "why_it_matters",
                "strategic_goal", "confidence", "missing_context"]}

CONTEXT_PROMPT = """You describe the product a GitHub repository provides, so later reviewers can judge
which changes matter to it. Use only the evidence below. Where the evidence is silent, say "Unknown"
rather than guessing, and list what is missing in missing_context.
Return a short product summary, a one-sentence value proposition, a lean canvas, and the 3 to 5
strategic goals the repository appears focused on now, most important first.

Repository: {repo}
Description: {description}
Topics: {topics}
Homepage: {homepage}

README (may be truncated):
{readme}"""

ASSESSMENT_PROMPT = """You judge the value a pull request is expected to deliver to the product below.
Rules:
- Judge expected value to this product and its users, not how well the PR is written.
- Outcome or impact language in the title is framing, not value. A clearly framed PR can be Low value,
  and a terse maintenance PR can be High value when the product depends on it.
- This is an expectation. Never claim the value was achieved or measured.
- Use Unclear when the evidence cannot support a judgment, and say what is missing.
- strategic_goal names the matching goal from the list, or "None".
Keep each text field to one or two sentences.

## Product context
{context}

## Pull request #{number} ({cohort})
Title: {title}
Lines changed: {size}
Rule-based content framing: {framing} ({rationale})
Body (may be truncated):
{body}"""


def _github_headers(token):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "github-flow-analytics/0.1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def _request(url, timeout, **kwargs):
    # The Workers fetch transport ignores request_json's timeout, so enforce it here.
    return await asyncio.wait_for(request_json(url, timeout=timeout, **kwargs), timeout)


async def fetch_product_sources(repo, token=None):
    """Repository description, topics, homepage and README text from GitHub's REST API.

    Raises ValueError unless the repository is public when read. The README is
    fetched without the token, so GitHub itself refuses it if the repository went
    private after the metadata check; a refused or rate-limited README is skipped.
    """
    base = f"https://api.github.com/repos/{repo}"
    meta_status, _, meta = await _request(base, 10, headers=_github_headers(token))
    if not 200 <= meta_status < 300 or meta.get("private") is not False:
        raise ValueError("Repository is not public")
    readme_status, _, readme = await _request(f"{base}/readme", 10, headers=_github_headers(None))
    text = ""
    if 200 <= readme_status < 300 and readme.get("encoding") == "base64":
        text = base64.b64decode(readme.get("content", "")).decode("utf-8", "replace")
    return {"description": meta.get("description") or "", "topics": meta.get("topics") or [],
            "homepage": meta.get("homepage") or "", "readme": text[:README_CHARS],
            "readme_truncated": len(text) > README_CHARS}


async def gemini_json(key, prompt, schema, max_tokens=1200):
    """Call Gemini for a structured JSON answer; raise ValueError on any failure."""
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": max_tokens,
                                 "responseMimeType": "application/json", "responseSchema": schema}}
    status, _, payload = await _request(GEMINI_URL, GEMINI_TIMEOUT, method="POST",
                                        headers={"x-goog-api-key": key}, body=body)
    if status < 200 or status >= 300:
        raise ValueError(f"Gemini returned HTTP {status}")
    try:
        parts = payload["candidates"][0]["content"]["parts"]
        return json.loads("".join(part.get("text", "") for part in parts))
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValueError("Gemini returned an unreadable answer") from exc


def select_prs(data, value_classification, max_prs=DEFAULT_MAX_PRS):
    """Most recent sampled PRs to assess: up to a third open, the rest merged."""
    raw = {(cohort, pr.get("number")): pr for cohort, key in (("open", "open_prs"), ("merged", "recent_prs"))
           for pr in reversed(data.get(key) or [])}  # reversed so the first record of a number wins

    def newest(cohort, date_key):
        pairs = [(i, raw.get((cohort, i["number"]), {})) for i in value_classification["prs"]["items"]
                 if i["cohort"] == cohort]
        return sorted(pairs, key=lambda pair: pair[1].get(date_key) or "", reverse=True)

    open_pairs = newest("open", "createdAt")[:max_prs // 3]
    return newest("merged", "mergedAt")[:max_prs - len(open_pairs)] + open_pairs


def _clean_context(context):
    """Normalize Gemini's product context; raise ValueError when it is not one."""
    if not isinstance(context, dict):
        raise ValueError("Gemini returned a context that is not an object")
    canvas = context.get("lean_canvas")
    canvas = canvas if isinstance(canvas, dict) else {}
    goals = context.get("strategic_goals")
    goals = goals if isinstance(goals, list) else []
    return {"product_summary": str(context.get("product_summary") or "").strip(),
            "value_proposition": str(context.get("value_proposition") or "").strip(),
            "lean_canvas": {key: str(canvas[key]).strip() for key in CANVAS_KEYS if canvas.get(key)},
            "strategic_goals": [str(goal).strip() for goal in goals if goal],
            "missing_context": str(context.get("missing_context") or "").strip()}


def _context_text(context):
    canvas = context.get("lean_canvas") or {}
    goals = context.get("strategic_goals") or []
    return "\n".join([f"Summary: {context.get('product_summary', '')}",
                      f"Value proposition: {context.get('value_proposition', '')}",
                      *(f"{key.replace('_', ' ').capitalize()}: {value}" for key, value in canvas.items()),
                      "Strategic goals:", *(f"{n}. {goal}" for n, goal in enumerate(goals, 1))])


def _clean(assessment):
    result = {key: str(assessment.get(key) or "").strip() for key in ASSESSMENT_SCHEMA["required"]}
    if result["expected_value"] not in VALUE_LEVELS:
        result["expected_value"] = "Unclear"
    if result["confidence"] not in CONFIDENCE_LEVELS:
        result["confidence"] = "Low"
    if not all(result[key] for key in NARRATIVE_KEYS):  # a rating without its reasoning is unsupported
        result["expected_value"], result["confidence"] = "Unclear", "Low"
        result["missing_context"] = result["missing_context"] or "Gemini gave no reasoning for this PR."
    return result


async def assess_pr(key, context_text, item, pr):
    prompt = ASSESSMENT_PROMPT.format(
        context=context_text, number=item["number"], cohort=item["cohort"], title=item["title"],
        size=(pr.get("additions") or 0) + (pr.get("deletions") or 0) or "unknown",
        framing=item["level"], rationale=item["rationale"],
        body=(pr.get("body") or "(no body)")[:BODY_CHARS])
    base = {"number": item["number"], "title": item["title"], "cohort": item["cohort"], "framing": item["level"]}
    try:
        return {**base, "status": "assessed", **_clean(await gemini_json(key, prompt, ASSESSMENT_SCHEMA, 600))}
    except Exception:  # one failed PR must not lose the others
        return {**base, "status": "failed"}


async def assess_product_value(repo, data, key, token=None, max_prs=DEFAULT_MAX_PRS):
    """Product context plus one expected-value assessment per sampled PR.

    Never raises: a missing key or failed context call returns a status the
    dashboard explains, so the rest of the analysis is unaffected.
    """
    value_classification = compute_value_classification(data)
    result = {"method": "gemini", "model": MODEL, "version": VERSION, "levels": VALUE_LEVELS,
              "disclaimer": DISCLAIMER, "context": None, "sources": None, "items": [],
              "total_prs": value_classification["prs"]["total"]}
    if not key:
        return {**result, "status": "unavailable", "reason": "No Gemini API key is configured."}
    try:
        sources = await fetch_product_sources(repo, token)
        context = await gemini_json(key, CONTEXT_PROMPT.format(
            repo=repo, description=sources["description"] or "(none)",
            topics=", ".join(sources["topics"]) or "(none)", homepage=sources["homepage"] or "(none)",
            readme=sources["readme"] or "(no README)"), CONTEXT_SCHEMA, 1500)
        context = _clean_context(context)
    except Exception:  # httpx errors locally, JS fetch errors on Workers, bad answers
        return {**result, "status": "failed", "reason": "Product context could not be generated."}
    result["sources"] = {"description": bool(sources["description"]), "readme": bool(sources["readme"]),
                         "readme_truncated": sources["readme_truncated"]}
    result["context"] = context
    context_text = _context_text(context)
    limit = asyncio.Semaphore(CONCURRENCY)

    async def bounded(item, pr):
        async with limit:
            return await assess_pr(key, context_text, item, pr)

    selected = select_prs(data, value_classification, max_prs)
    result["selected"] = len(selected)
    result["items"] = list(await asyncio.gather(*(bounded(item, pr) for item, pr in selected)))
    counts = dict.fromkeys(VALUE_LEVELS, 0)
    for item in result["items"]:
        if item["status"] == "assessed":
            counts[item["expected_value"]] += 1
    return {**result, "status": "ok", "counts": counts,
            "assessed": sum(counts.values()), "failed": sum(i["status"] == "failed" for i in result["items"])}
