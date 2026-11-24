"""Jev-backed insight pattern catalog and outcome-framing classification."""
import asyncio
import logging
import math
import time

from github_api import log_event, request_json
from value_classification import LEVELS, QUARTERLY_BUCKET_RE

CATALOG_VERSION = "jev-patterns-1"
JEV_MODEL = "jev-1.14.0"
JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_TIMEOUT = 15
PATTERN_TIMEOUT = 25
CONCURRENCY = 4
MATCH_THRESHOLD = 0.6
BODY_CHARS = 1500

FRAMING_LEVELS = LEVELS[:5]  # Input, Activity, Output, Outcome, Impact

ITEM_PATTERNS = {
    "activity_without_outcome": {
        "label": "Activity without outcome",
        "description": "Work described as tasks, refactoring, or chores with no stated user or product outcome.",
        "coaching_hint": "Reframe the title/body around who benefits and what observable behavior changes.",
        "question": "Does this work item describe an activity, task, or technical chore without stating an observable user, product, or reliability outcome?",
    },
    "vague_scope_or_ac": {
        "label": "Vague scope / missing AC",
        "description": "Lacks clear scope boundaries, acceptance criteria, or a verifiable definition of done.",
        "coaching_hint": "Add explicit acceptance criteria or a concrete verification signal before starting work.",
        "question": "Does this work item lack clear scope boundaries, acceptance criteria, or a verifiable definition of done?",
    },
    "multi_concern_batch": {
        "label": "Multi-concern batch",
        "description": "Bundles multiple unrelated features, fixes, or concerns into a single PR or issue.",
        "coaching_hint": "Split independent concerns into smaller PRs/issues to reduce review queue time and risk.",
        "question": "Does this work item bundle multiple unrelated changes, features, or concerns into a single batch?",
    },
    "preventive_risk_or_debt": {
        "label": "Preventive risk / tech debt",
        "description": "Targets reliability, security, CI stability, or technical debt reduction.",
        "coaching_hint": "Track preventive work explicitly so capacity allocation is visible alongside feature delivery.",
        "question": "Does this work item intend to prevent product, security, or reliability risk or pay down technical debt?",
    },
    "customer_friction_fix": {
        "label": "Customer friction fix",
        "description": "Directly resolves a user-facing bug, UX friction, or broken workflow.",
        "coaching_hint": "Verify whether recurring friction points cluster in the same component or stage.",
        "question": "Does this work item directly resolve a user-facing bug, UX friction, or broken workflow?",
    },
}

FLOW_PATTERNS = {
    "review_queue_bottleneck": {
        "label": "Review queue bottleneck",
        "description": "PR review or approval wait time dominates overall cycle time or open WIP.",
        "coaching_hint": "Lower WIP in review, set review SLAs, and shrink PR batch sizes.",
        "question": "Do the supplied flow metrics show PR review or approval wait time dominating overall cycle time or open WIP?",
    },
    "wip_aging_tail_risk": {
        "label": "WIP aging tail risk",
        "description": "Open PRs or issues are aging past P85 SLE or piling up in active stages.",
        "coaching_hint": "Swarm aged items above P85 SLE before pulling new work.",
        "question": "Do the supplied flow metrics show open work items aging past P85 SLE or accumulating in active stages?",
    },
    "output_without_outcome_bias": {
        "label": "Output/activity framing bias",
        "description": "Most sampled work is framed as Input/Activity/Output rather than Outcome or Impact.",
        "coaching_hint": "Shift PR and issue templates toward intended user outcomes rather than task lists.",
        "question": "Do the supplied framing counts show most work framed as Input, Activity, or Output rather than Outcome or Impact?",
    },
    "unlinked_work_stream": {
        "label": "Unlinked work stream",
        "description": "Low linkage between PRs and tracked issues or missing strategic context.",
        "coaching_hint": "Link PRs to tracked issues so flow and value telemetry stay connected.",
        "question": "Do the supplied flow metrics show low linkage between PRs and tracked issues or missing strategic framing?",
    },
}

_FRAMING_CRITERIA = [
    "Input: staffing, hiring, headcount, budget, or resource allocation without a deliverable.",
    "Activity: effort, meetings, spikes, refactoring, upgrades, or bug-fixing tasks without a stated deliverable or outcome.",
    "Output: shipping a concrete feature, endpoint, UI component, or document without stating the user/business outcome.",
    "Outcome: changing user behavior, workflow speed, reliability, or adoption metrics.",
    "Impact: lagging business, revenue, retention, or strategic goal movement.",
]

_ITEM_QUESTIONS = {
    **{pid: {"type": "noul", "instructions": spec["question"]} for pid, spec in ITEM_PATTERNS.items()},
    "outcome_framing": {
        "type": "score",
        "instructions": "Classify the stated intent level of this work item from Input (0) to Impact (4).",
        "criteria": _FRAMING_CRITERIA,
    },
}

_FLOW_QUESTIONS = {
    pid: {"type": "noul", "instructions": spec["question"]} for pid, spec in FLOW_PATTERNS.items()
}


def _valid_prob(answer):
    if not isinstance(answer, dict) or answer.get("type") != "noul":
        return None
    p = answer.get("noul")
    if type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1:
        return None
    return float(p)


def _valid_framing(answer):
    if not isinstance(answer, dict) or answer.get("type") != "score":
        return None
    probs = answer.get("probabilities")
    if not isinstance(probs, dict) or set(probs) != {str(i) for i in range(len(FRAMING_LEVELS))}:
        return None
    weights = [probs.get(str(i)) for i in range(len(FRAMING_LEVELS))]
    if any(type(w) not in (int, float) or not math.isfinite(w) or not 0 <= w <= 1 for w in weights):
        return None
    if not 0.98 <= sum(weights) <= 1.02:
        return None
    idx = max(range(len(weights)), key=lambda i: weights[i])
    return FRAMING_LEVELS[idx], float(weights[idx])


async def _jev_call(jev_key, state, questions):
    status, _, payload = await asyncio.wait_for(
        request_json(
            JEV_URL,
            JEV_TIMEOUT,
            method="POST",
            headers={"Authorization": f"Bearer {jev_key}"},
            body={"model": JEV_MODEL, "state": state, "questions": questions},
        ),
        JEV_TIMEOUT,
    )
    if not 200 <= status < 300 or not isinstance(payload, dict):
        return None
    return payload.get("answers") if isinstance(payload.get("answers"), dict) else None


def _select_sample_items(data, max_items):
    items = []
    seen = set()
    for kind, key in (("pr", "recent_prs"), ("pr", "open_prs")):
        for pr in data.get(key) or []:
            num = pr.get("number")
            title = (pr.get("title") or "").strip()
            body = (pr.get("body") or "").strip()
            if num is None or (kind, num) in seen or not (title or body):
                continue
            seen.add((kind, num))
            items.append({"kind": kind, "number": num, "title": title[:200], "body": body[:BODY_CHARS]})
    issues = data.get("issues") or {}
    for key in ("open", "closed"):
        for issue in issues.get(key) or []:
            num = issue.get("number") or issue.get("identifier")
            title = (issue.get("title") or "").strip()
            body = (issue.get("body") or issue.get("description") or "").strip()
            if num is None or ("issue", num) in seen or not (title or body):
                continue
            seen.add(("issue", num))
            items.append({"kind": "issue", "number": num, "title": title[:200], "body": body[:BODY_CHARS]})
    return items[:max_items]


def _flow_snapshot(metrics):
    stats = metrics.get("recent_stats") or {}
    issues = metrics.get("issues") or {}
    vc = metrics.get("value_classification") or {}
    return {
        "pr_total": stats.get("total", 0),
        "pr_p50_days": stats.get("p50_days"),
        "pr_p85_days": stats.get("p85_days"),
        "stage_percentiles": metrics.get("stage_percentiles") or {},
        "open_wip_count": len(metrics.get("open_wip") or []),
        "issue_open_count": issues.get("open_count", 0),
        "issue_linked_pr_count": issues.get("linked_pr_count", 0),
        "framing_prs": (vc.get("prs") or {}).get("counts", {}),
        "framing_issues": (vc.get("issues") or {}).get("counts", {}),
    }


def _apply_jev_framing(metrics, item_results):
    vc = metrics.get("value_classification")
    if not isinstance(vc, dict):
        return
    by_key = {(r["kind"], r["number"]): r["framing"] for r in item_results if r.get("framing")}
    if not by_key:
        return
    updated = False
    for group_key, kind in (("prs", "pr"), ("issues", "issue")):
        group = vc.get(group_key)
        if not isinstance(group, dict) or not isinstance(group.get("items"), list):
            continue
        counts = {level: 0 for level in vc.get("levels", LEVELS)}
        for item in group["items"]:
            if QUARTERLY_BUCKET_RE.match(item.get("title") or ""):
                counts[item["level"]] = counts.get(item["level"], 0) + 1
                continue
            match = by_key.get((kind, item.get("number")))
            if match:
                level, prob = match
                item["level"] = level
                item["rationale"] = f"Jev classification ({prob * 100:.0f}% probability)."
                updated = True
            counts[item["level"]] = counts.get(item["level"], 0) + 1
        group["counts"] = counts
    if updated:
        vc["method"] = "jev"


async def analyze_patterns_and_framing(repo, data, metrics, *, jev_key=None, max_items=10, allow_private=False):
    """Evaluate Jev insight patterns on sampled PRs/issues + flow metrics, and upgrade framing."""
    started = time.monotonic()
    log_ctx = {} if allow_private else {"repo": repo}
    if not jev_key:
        return {
            "status": "unavailable",
            "method": "rules",
            "model": JEV_MODEL,
            "catalog_version": CATALOG_VERSION,
            "reason": "Configure TypeSafe API key to run Jev pattern classification.",
            "flow_patterns": [{"id": pid, "label": spec["label"], "description": spec["description"],
                               "coaching_hint": spec["coaching_hint"], "matched": False, "probability": None}
                              for pid, spec in FLOW_PATTERNS.items()],
            "item_patterns": [{"id": pid, "label": spec["label"], "description": spec["description"],
                               "coaching_hint": spec["coaching_hint"], "matched_count": 0, "sampled_count": 0,
                               "matched_items": []}
                              for pid, spec in ITEM_PATTERNS.items()],
            "sampled_items": 0,
            "assessed_items": 0,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
        }

    samples = _select_sample_items(data, max_items)
    sem = asyncio.Semaphore(CONCURRENCY)

    async def eval_item(sample):
        async with sem:
            state = {"work_item": {"kind": sample["kind"], "number": sample["number"],
                                   "title": sample["title"], "description": sample["body"]},
                     "scope": "Classify the supplied work item against each pattern and its stated intent framing."}
            answers = await _jev_call(jev_key, state, _ITEM_QUESTIONS)
            if not answers:
                return None
            patterns = {}
            for pid in ITEM_PATTERNS:
                p = _valid_prob(answers.get(pid))
                if p is not None:
                    patterns[pid] = p
            framing = _valid_framing(answers.get("outcome_framing"))
            return {"kind": sample["kind"], "number": sample["number"], "title": sample["title"],
                    "patterns": patterns, "framing": framing}

    async def eval_flow():
        state = {"flow_metrics": _flow_snapshot(metrics),
                 "scope": "Evaluate whether the supplied repository flow metrics exhibit each flow pattern."}
        return await _jev_call(jev_key, state, _FLOW_QUESTIONS)

    tasks = [asyncio.create_task(eval_item(s)) for s in samples]
    flow_task = asyncio.create_task(eval_flow())
    all_tasks = [*tasks, flow_task]
    try:
        try:
            done, _ = await asyncio.wait(all_tasks, timeout=PATTERN_TIMEOUT)
        finally:
            pending = [t for t in all_tasks if not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        flow_answers = flow_task.result() if flow_task in done and not flow_task.cancelled() and not flow_task.exception() else None
        item_results = [
            t.result() for t in tasks
            if t in done and not t.cancelled() and not t.exception() and t.result() is not None
        ]
    except Exception:
        log_event(logging.WARNING, "jev_patterns_failed", **log_ctx, elapsed_ms=round((time.monotonic() - started) * 1000))
        return {"status": "failed", "method": "rules", "model": JEV_MODEL, "catalog_version": CATALOG_VERSION,
                "reason": "Jev pattern classification failed.", "flow_patterns": [], "item_patterns": [],
                "sampled_items": len(samples), "assessed_items": 0,
                "elapsed_ms": round((time.monotonic() - started) * 1000)}

    _apply_jev_framing(metrics, item_results)

    flow_patterns = []
    for pid, spec in FLOW_PATTERNS.items():
        prob = _valid_prob((flow_answers or {}).get(pid))
        flow_patterns.append({
            "id": pid,
            "label": spec["label"],
            "description": spec["description"],
            "coaching_hint": spec["coaching_hint"],
            "probability": round(prob, 3) if prob is not None else None,
            "matched": bool(prob is not None and prob >= MATCH_THRESHOLD),
        })

    item_patterns = []
    for pid, spec in ITEM_PATTERNS.items():
        matched = []
        for res in item_results:
            prob = res["patterns"].get(pid)
            if prob is not None and prob >= MATCH_THRESHOLD:
                matched.append({"kind": res["kind"], "number": res["number"], "title": res["title"],
                                "probability": round(prob, 3)})
        matched.sort(key=lambda m: -m["probability"])
        item_patterns.append({
            "id": pid,
            "label": spec["label"],
            "description": spec["description"],
            "coaching_hint": spec["coaching_hint"],
            "matched_count": len(matched),
            "sampled_count": len(item_results),
            "matched_items": matched[:5],
        })

    elapsed_ms = round((time.monotonic() - started) * 1000)
    log_event(logging.INFO, "jev_patterns_completed", **log_ctx, sampled_items=len(samples),
              assessed_items=len(item_results), elapsed_ms=elapsed_ms)
    return {
        "status": "ok",
        "method": "jev",
        "model": JEV_MODEL,
        "catalog_version": CATALOG_VERSION,
        "flow_patterns": flow_patterns,
        "item_patterns": item_patterns,
        "sampled_items": len(samples),
        "assessed_items": len(item_results),
        "elapsed_ms": elapsed_ms,
    }
