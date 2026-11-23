"""Operational review insights, ranked slices, and optional Gemini synthesis."""
from __future__ import annotations

import json
import math
from typing import Any

from flow_metrics_core import percentile
from github_api import request_json

SLICE_ORDER = (
    "wip_aging",
    "flow_predictability",
    "contributor_mode",
    "batch_size",
    "backlog_health",
    "value_framing",
)

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
GEMINI_TIMEOUT = 10


def _fmt_hours(hours: float | None) -> str:
    if hours is None or not math.isfinite(hours):
        return "n/a"
    if hours >= 48:
        return f"{hours / 24:.1f}d"
    return f"{hours:.1f}h"


def _fmt_days(days: float | None) -> str:
    if days is None or not math.isfinite(days):
        return "n/a"
    return f"{days:.1f}d"


def _size_bucket(size: float | int | None) -> str:
    val = size or 0
    if val < 50:
        return "XS (<50)"
    if val < 200:
        return "S (50-199)"
    if val < 500:
        return "M (200-499)"
    return "L (500+)"


def _severity(score: int) -> str:
    if score >= 70:
        return "high"
    if score >= 45:
        return "medium"
    return "info"


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return percentile(values, 0.50)


def _p85(values: list[float]) -> float | None:
    if not values:
        return None
    return percentile(values, 0.85)


def _build_wip_aging_slice(metrics: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    active = metrics.get("active_wip_points") or []
    p85_hours = metrics.get("p85_sle")
    merged_total = (metrics.get("recent_stats") or {}).get("total") or 0
    sle_days = (p85_hours / 24.0) if (p85_hours and merged_total > 0) else None
    if not active:
        return ({
            "id": "wip_aging",
            "title": "WIP Aging & Stage Queues",
            "available": False,
            "featured": False,
            "score": 0,
            "severity": "info",
            "headline_metric": "0 open PRs",
            "insight": "No open PRs in the sampled window.",
            "review_question": "Is work currently waiting upstream in issues rather than in open PRs?",
            "rows": [],
            "evidence_items": [],
            "drilldown": {"view": "work", "chart_id": "chart-wip", "filter": {}},
        }, None)

    stage_counts: dict[str, int] = {}
    for item in active:
        stage = item.get("x") or "Unknown"
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
    ordered_stages = sorted(stage_counts.items(), key=lambda pair: (-pair[1], pair[0]))
    top_stage, top_stage_count = ordered_stages[0]
    oldest = sorted(active, key=lambda item: item.get("y") or 0, reverse=True)
    oldest_pr = oldest[0]
    oldest_days = float(oldest_pr.get("y") or 0)
    aging_over_sle = [item for item in oldest if sle_days is not None and (item.get("y") or 0) > sle_days]
    aging_over_14d = [item for item in oldest if (item.get("y") or 0) >= 14.0]

    score = 30
    if aging_over_sle:
        ratio = len(aging_over_sle) / len(active)
        score = min(95, int(55 + ratio * 35 + min(len(aging_over_sle), 5) * 3))
    elif aging_over_14d:
        score = min(85, 50 + len(aging_over_14d) * 5)
    elif len(active) >= 8 and top_stage_count / len(active) >= 0.6:
        score = 55

    if aging_over_sle and sle_days is not None:
        headline = f"{len(aging_over_sle)} of {len(active)} open PRs exceed {_fmt_days(sle_days)} SLE"
        insight = (
            f"{len(aging_over_sle)} open PRs are older than the 85% SLE ({_fmt_days(sle_days)}), "
            f"with the oldest (#{oldest_pr.get('num')}) at {_fmt_days(oldest_days)}. "
            f"Largest estimated queue: {top_stage} ({top_stage_count}/{len(active)})."
        )
        refs = ", ".join(f"#{item.get('num')}" for item in aging_over_sle[:3])
        question = (
            f"Which aging PRs ({refs}) should we finish, split, or close before starting new work?"
        )
        title = f"{len(aging_over_sle)} open PRs have aged past the 85% SLE"
    else:
        headline = f"{len(active)} open PRs · oldest {_fmt_days(oldest_days)}"
        insight = (
            f"{len(active)} open PRs are active; {top_stage_count} sit in estimated stage '{top_stage}' "
            f"and the oldest (#{oldest_pr.get('num')}) is {_fmt_days(oldest_days)} old."
        )
        question = (
            f"Is '{top_stage}' ({top_stage_count} open PRs) a real handoff bottleneck or stale drafts waiting to be closed?"
        )
        title = f"{top_stage_count} of {len(active)} open PRs cluster in {top_stage}"

    rows = [
        {"label": stage, "value": f"{count} PRs", "detail": f"{round(count / len(active) * 100)}% of active WIP"}
        for stage, count in ordered_stages
    ]
    evidence_items = [
        {
            "kind": "pr",
            "number": item.get("num"),
            "title": str(item.get("title") or "")[:120],
            "metric": f"{_fmt_days(float(item.get('y') or 0))} · {item.get('x') or 'Unknown'}",
        }
        for item in oldest[:8]
    ]
    evidence = [
        f"{len(active)} active PRs",
        f"Oldest: #{oldest_pr.get('num')} ({_fmt_days(oldest_days)})",
        f"Top stage: {top_stage} ({top_stage_count})",
    ]
    if aging_over_sle and sle_days is not None:
        evidence.insert(0, f"{len(aging_over_sle)}/{len(active)} > {_fmt_days(sle_days)} SLE")

    slice_obj = {
        "id": "wip_aging",
        "title": "WIP Aging & Stage Queues",
        "available": True,
        "featured": False,
        "score": score,
        "severity": _severity(score),
        "headline_metric": headline,
        "insight": insight,
        "review_question": question,
        "rows": rows,
        "evidence_items": evidence_items,
        "drilldown": {"view": "work", "chart_id": "chart-wip", "filter": {}},
    }
    insight_obj = {
        "id": "wip_aging_signal",
        "slice_id": "wip_aging",
        "severity": _severity(score),
        "score": score,
        "title": title,
        "summary": insight,
        "evidence": evidence,
        "review_question": question,
        "drilldown": slice_obj["drilldown"],
    }
    return slice_obj, insight_obj


def _build_flow_predictability_slice(metrics: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    stats = metrics.get("recent_stats") or {}
    merged = int(stats.get("total") or 0)
    p50 = metrics.get("p50_sle")
    p85 = metrics.get("p85_sle")
    if merged == 0 or p50 is None or p85 is None:
        return ({
            "id": "flow_predictability",
            "title": "Flow Predictability & Trend",
            "available": False,
            "featured": False,
            "score": 0,
            "severity": "info",
            "headline_metric": "0 merged PRs",
            "insight": "No merged PRs in the sampled window.",
            "review_question": "Are changes shipping directly to main or waiting in unmerged branches?",
            "rows": [],
            "evidence_items": [],
            "drilldown": {"view": "flow", "chart_id": "chart-control", "filter": {}},
        }, None)

    tail_ratio = (p85 / p50) if p50 > 0 else 1.0
    tp = (metrics.get("throughput_data") or [])[1:-1]
    recent_4w = tp[-4:] if len(tp) >= 4 else tp
    prior_4w = tp[-8:-4] if len(tp) >= 8 else []
    recent_avg_tp = sum(row.get("total", 0) for row in recent_4w) / len(recent_4w) if recent_4w else 0.0
    prior_avg_tp = sum(row.get("total", 0) for row in prior_4w) / len(prior_4w) if prior_4w else None
    tp_delta_pct = (
        ((recent_avg_tp - prior_avg_tp) / prior_avg_tp) * 100.0
        if (prior_avg_tp is not None and prior_avg_tp > 0)
        else None
    )

    score = 35
    if merged >= 5 and tail_ratio >= 4.0:
        score = 82
    elif merged >= 5 and tail_ratio >= 2.5:
        score = 62
    if tp_delta_pct is not None and abs(tp_delta_pct) >= 25:
        score = max(score, 68 if tp_delta_pct < 0 else 55)

    if tail_ratio >= 3.0 and merged >= 5:
        title = f"Wide cycle-time tail: P85 ({_fmt_hours(p85)}) is {tail_ratio:.1f}x P50 ({_fmt_hours(p50)})"
        insight = (
            f"Half of merged PRs finish within {_fmt_hours(p50)}, but the 85th percentile stretches to "
            f"{_fmt_hours(p85)} ({tail_ratio:.1f}x median across {merged} merged PRs)."
        )
        question = (
            f"What separates the slowest 15% of PRs (>{_fmt_hours(p85)}) from the typical {_fmt_hours(p50)} PR?"
        )
    elif tp_delta_pct is not None and tp_delta_pct <= -25:
        title = f"Weekly throughput dropped {abs(tp_delta_pct):.0f}% over the last 4 complete weeks"
        insight = (
            f"Merged PR throughput averaged {recent_avg_tp:.1f}/wk over the last 4 complete weeks vs "
            f"{prior_avg_tp:.1f}/wk in the prior 4 weeks (P85 cycle time {_fmt_hours(p85)})."
        )
        question = (
            "Did throughput dip because work got larger, reviewers were constrained, or scope shifted?"
        )
    else:
        title = f"85% of PRs merge within {_fmt_hours(p85)} (median {_fmt_hours(p50)})"
        insight = (
            f"Across {merged} merged PRs, median cycle time is {_fmt_hours(p50)} and 85% SLE is {_fmt_hours(p85)} "
            f"(tail ratio {tail_ratio:.1f}x)."
        )
        question = (
            f"Is an {_fmt_hours(p85)} 85% SLE predictable enough for how this team plans releases and commitments?"
        )

    rows = [
        {"label": "Median (P50) cycle time", "value": _fmt_hours(p50), "detail": f"{merged} merged PRs sampled"},
        {"label": "85% SLE (P85)", "value": _fmt_hours(p85), "detail": f"{tail_ratio:.1f}x median tail ratio"},
    ]
    if recent_4w:
        detail = (
            f"{tp_delta_pct:+.0f}% vs prior 4w ({prior_avg_tp:.1f}/wk)"
            if tp_delta_pct is not None and prior_avg_tp is not None
            else f"{len(recent_4w)} complete weeks"
        )
        rows.append({"label": "Recent throughput", "value": f"{recent_avg_tp:.1f} PRs/wk", "detail": detail})
    pre_stats = metrics.get("pre_stats") or {}
    if pre_stats.get("total"):
        rows.append({
            "label": "Pre-AI baseline avg cycle",
            "value": _fmt_hours(pre_stats.get("avg_cycle")),
            "detail": f"{pre_stats['total']} baseline PRs",
        })

    slowest = sorted(
        [p for p in (metrics.get("recent_prs_summary") or []) if p.get("cycle_time_hours") is not None],
        key=lambda p: p.get("cycle_time_hours") or 0,
        reverse=True,
    )[:6]
    evidence_items = [
        {
            "kind": "pr",
            "number": p.get("number"),
            "title": str(p.get("title") or "")[:120],
            "metric": _fmt_hours(p.get("cycle_time_hours")),
        }
        for p in slowest
    ]
    evidence = [f"P50: {_fmt_hours(p50)}", f"P85: {_fmt_hours(p85)} ({tail_ratio:.1f}x)"]
    if tp_delta_pct is not None:
        evidence.append(f"4w throughput: {recent_avg_tp:.1f}/wk ({tp_delta_pct:+.0f}%)")

    slice_obj = {
        "id": "flow_predictability",
        "title": "Flow Predictability & Trend",
        "available": True,
        "featured": False,
        "score": score,
        "severity": _severity(score),
        "headline_metric": f"P50 {_fmt_hours(p50)} · P85 {_fmt_hours(p85)}",
        "insight": insight,
        "review_question": question,
        "rows": rows,
        "evidence_items": evidence_items,
        "drilldown": {"view": "flow", "chart_id": "chart-control", "filter": {}},
    }
    insight_obj = {
        "id": "flow_predictability_signal",
        "slice_id": "flow_predictability",
        "severity": _severity(score),
        "score": score,
        "title": title,
        "summary": insight,
        "evidence": evidence,
        "review_question": question,
        "drilldown": slice_obj["drilldown"],
    }
    return slice_obj, insight_obj


def _build_contributor_mode_slice(metrics: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    points = metrics.get("pr_size_scatter_points") or []
    if not points:
        points = [
            {"classification": p.get("classification"), "y": p.get("y"), "x": 0, "num": p.get("num"), "title": ""}
            for p in (metrics.get("recent_scatter_points") or [])
        ]
    if not points:
        return ({
            "id": "contributor_mode",
            "title": "Human vs AI Contributor Modes",
            "available": False,
            "featured": False,
            "score": 0,
            "severity": "info",
            "headline_metric": "No PR contributor data",
            "insight": "No merged PRs available to compare contributor modes.",
            "review_question": "How is work currently split across human and AI-assisted PRs?",
            "rows": [],
            "evidence_items": [],
            "drilldown": {"view": "work", "chart_id": "chart-throughput", "filter": {}},
        }, None)

    has_size = any((p.get("x") or 0) > 0 for p in points)
    groups: dict[str, list[dict[str, Any]]] = {}
    for p in points:
        cls = p.get("classification") or "Human"
        groups.setdefault(cls, []).append(p)

    total = len(points)
    stats_by_mode = []
    for cls in ("Human", "AI-Assisted", "AI-Agentic"):
        items = groups.get(cls, [])
        if not items:
            continue
        cycles = [float(i["y"]) for i in items if i.get("y") is not None]
        med = _median(cycles)
        p85_val = _p85(cycles)
        avg_size = (sum(float(i.get("x") or 0) for i in items) / len(items)) if has_size else None
        stats_by_mode.append({
            "mode": cls,
            "count": len(items),
            "share_pct": round(len(items) / total * 100),
            "median_hours": med,
            "p85_hours": p85_val,
            "avg_size": avg_size,
            "items": items,
        })

    multi_mode = [m for m in stats_by_mode if m["count"] >= 2 and m["median_hours"] is not None]
    score = 25
    target_mode = stats_by_mode[0]["mode"]
    if len(multi_mode) >= 2:
        slowest = max(multi_mode, key=lambda m: m["median_hours"])
        fastest = min(multi_mode, key=lambda m: m["median_hours"])
        ratio = (slowest["median_hours"] / fastest["median_hours"]) if fastest["median_hours"] > 0 else 1.0
        target_mode = slowest["mode"]
        if ratio >= 2.0:
            score = 78
        elif ratio >= 1.4:
            score = 60
        else:
            score = 45
        title = f"{slowest['mode']} PRs take {ratio:.1f}x longer than {fastest['mode']} PRs"
        insight = (
            f"Estimated {slowest['mode']} PRs ({slowest['count']}, {slowest['share_pct']}%) have a median cycle time of "
            f"{_fmt_hours(slowest['median_hours'])} vs {_fmt_hours(fastest['median_hours'])} for {fastest['mode']} "
            f"({fastest['count']}, {fastest['share_pct']}%)."
        )
        question = (
            f"Why are {slowest['mode']} PRs taking {ratio:.1f}x longer to merge—review friction, larger diffs, or unclear ownership?"
        )
    else:
        dominant = stats_by_mode[0]
        title = f"{dominant['share_pct']}% of sampled PRs are classified as {dominant['mode']}"
        insight = (
            f"Estimated contributor mix is concentrated in {dominant['mode']} ({dominant['count']}/{total} PRs, "
            f"median {_fmt_hours(dominant['median_hours'])})."
        )
        question = "Is contributor classification reflecting real AI adoption or missing commit/PR attribution signals?"

    rows = []
    for m in stats_by_mode:
        detail = f"median {_fmt_hours(m['median_hours'])} · P85 {_fmt_hours(m['p85_hours'])}"
        if m["avg_size"] is not None:
            detail += f" · avg {m['avg_size']:.0f} LOC"
        rows.append({"label": m["mode"], "value": f"{m['count']} PRs ({m['share_pct']}%)", "detail": detail})

    focus_items = sorted(groups.get(target_mode, points), key=lambda i: i.get("y") or 0, reverse=True)[:6]
    evidence_items = [
        {
            "kind": "pr",
            "number": i.get("num"),
            "title": str(i.get("title") or "")[:120],
            "metric": f"{i.get('classification')} · {_fmt_hours(i.get('y'))}",
        }
        for i in focus_items
    ]
    evidence = [f"{m['mode']}: {m['count']} ({m['share_pct']}%), P50 {_fmt_hours(m['median_hours'])}" for m in stats_by_mode]
    drilldown = {"view": "work", "chart_id": "chart-throughput", "filter": {"classification": target_mode} if len(stats_by_mode) > 1 else {}}

    slice_obj = {
        "id": "contributor_mode",
        "title": "Human vs AI Contributor Modes",
        "available": True,
        "featured": False,
        "score": score,
        "severity": _severity(score),
        "headline_metric": " · ".join(f"{m['mode']} {m['share_pct']}%" for m in stats_by_mode),
        "insight": insight,
        "review_question": question,
        "rows": rows,
        "evidence_items": evidence_items,
        "drilldown": drilldown,
    }
    insight_obj = {
        "id": "contributor_mode_signal",
        "slice_id": "contributor_mode",
        "severity": _severity(score),
        "score": score,
        "title": title,
        "summary": insight,
        "evidence": evidence,
        "review_question": question,
        "drilldown": drilldown,
    }
    return slice_obj, insight_obj


def _build_batch_size_slice(metrics: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    points = metrics.get("pr_size_scatter_points") or []
    sized = [p for p in points if (p.get("x") or 0) > 0 and p.get("y") is not None]
    if len(sized) < 3:
        return ({
            "id": "batch_size",
            "title": "Batch Size vs Cycle Time",
            "available": False,
            "featured": False,
            "score": 0,
            "severity": "info",
            "headline_metric": "PR size data unavailable",
            "insight": "PR line-change counts were not collected for this snapshot, so batch size correlation is omitted.",
            "review_question": "Should we collect PR diff sizes to test whether large batches are driving review delays?",
            "rows": [],
            "evidence_items": [],
            "drilldown": {"view": "batch", "chart_id": "chart-correlation", "filter": {}},
        }, None)

    corr = float(metrics.get("spearman_corr") or 0.0)
    buckets: dict[str, list[dict[str, Any]]] = {}
    for p in sized:
        buckets.setdefault(_size_bucket(p.get("x")), []).append(p)
    order = ("XS (<50)", "S (50-199)", "M (200-499)", "L (500+)")
    rows = []
    bucket_medians: dict[str, float] = {}
    for label in order:
        items = buckets.get(label, [])
        if not items:
            continue
        med = _median([float(i["y"]) for i in items])
        if med is not None:
            bucket_medians[label] = med
        rows.append({
            "label": label,
            "value": f"{len(items)} PRs ({round(len(items) / len(sized) * 100)}%)",
            "detail": f"median {_fmt_hours(med)}",
        })

    large_items = buckets.get("L (500+)", [])
    small_med = bucket_medians.get("XS (<50)") or bucket_medians.get("S (50-199)")
    large_med = bucket_medians.get("L (500+)")
    ratio = (large_med / small_med) if (large_med and small_med and small_med > 0) else None

    score = 32
    if corr >= 0.45 or (ratio is not None and ratio >= 3.0 and len(large_items) >= 2):
        score = 80
    elif corr >= 0.30 or (ratio is not None and ratio >= 2.0 and len(large_items) >= 2):
        score = 62

    if ratio is not None and ratio >= 2.0 and len(large_items) >= 2:
        title = f"Large PRs (500+ LOC) take {ratio:.1f}x longer to merge"
        insight = (
            f"PRs with 500+ lines ({len(large_items)} PRs) have a median cycle time of {_fmt_hours(large_med)} "
            f"vs {_fmt_hours(small_med)} for smaller PRs (Spearman ρ = {corr:+.2f})."
        )
        question = "Which recent 500+ line PRs could have been sliced into smaller, reviewable vertical increments?"
        drill_filter = {"size_bucket": "L (500+)"}
    else:
        strength = "moderate" if abs(corr) >= 0.3 else "weak"
        title = f"PR size shows a {strength} correlation with cycle time (ρ = {corr:+.2f})"
        insight = (
            f"Across {len(sized)} sized PRs, Spearman rank correlation between diff size and cycle time is {corr:+.2f}."
        )
        question = "If PR size is not the main driver of cycle time, is queue wait time or review availability dominating instead?"
        drill_filter = {}

    largest = sorted(sized, key=lambda i: (i.get("x") or 0), reverse=True)[:6]
    evidence_items = [
        {
            "kind": "pr",
            "number": i.get("num"),
            "title": str(i.get("title") or "")[:120],
            "metric": f"{int(i.get('x') or 0)} LOC · {_fmt_hours(i.get('y'))}",
        }
        for i in largest
    ]
    evidence = [f"Spearman ρ: {corr:+.2f}", f"{len(sized)} sized PRs"]
    if large_items:
        evidence.append(f"500+ LOC: {len(large_items)} PRs (P50 {_fmt_hours(large_med)})")

    slice_obj = {
        "id": "batch_size",
        "title": "Batch Size vs Cycle Time",
        "available": True,
        "featured": False,
        "score": score,
        "severity": _severity(score),
        "headline_metric": f"Spearman ρ = {corr:+.2f}",
        "insight": insight,
        "review_question": question,
        "rows": rows,
        "evidence_items": evidence_items,
        "drilldown": {"view": "batch", "chart_id": "chart-correlation", "filter": drill_filter},
    }
    insight_obj = {
        "id": "batch_size_signal",
        "slice_id": "batch_size",
        "severity": _severity(score),
        "score": score,
        "title": title,
        "summary": insight,
        "evidence": evidence,
        "review_question": question,
        "drilldown": slice_obj["drilldown"],
    }
    return slice_obj, insight_obj


def _build_backlog_health_slice(metrics: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    issues = metrics.get("issues")
    if not issues or (not issues.get("open_available", True) and not issues.get("closed_available", True)):
        return ({
            "id": "backlog_health",
            "title": "Issue Backlog & Lifecycle",
            "available": False,
            "featured": False,
            "score": 0,
            "severity": "info",
            "headline_metric": "Issue data unavailable",
            "insight": "Issue flow metrics were not available for this repository.",
            "review_question": "Are issues tracked outside GitHub (e.g. Jira or Linear)?",
            "rows": [],
            "evidence_items": [],
            "drilldown": {"view": "issues", "chart_id": "chart-issue-flow", "filter": {}},
        }, None)

    open_now = issues.get("open_now") if issues.get("open_available", True) else None
    net_8w = issues.get("net_8w")
    stale = issues.get("stale") or {}
    stale_count = stale.get("count", 0) if issues.get("open_available", True) else None
    stale_items = stale.get("items") or []
    stale_share = (stale_count / open_now) if (stale_count is not None and open_now) else 0.0

    score = 30
    if net_8w is not None and net_8w > 0:
        score = max(score, 68 if net_8w >= 10 else 52)
    if stale_count and stale_share >= 0.35:
        score = max(score, 74)
    elif stale_count and stale_count >= 5:
        score = max(score, 58)

    if net_8w is not None and net_8w > 0:
        title = f"Issue backlog grew by +{net_8w} issues over the last 8 weeks"
        insight = (
            f"Arrivals outpaced resolutions by +{net_8w} over 8 weeks "
            f"({open_now if open_now is not None else 'n/a'} open in sample; "
            f"{stale_count if stale_count is not None else 0} stale)."
        )
        question = "Should we tighten intake triage or explicitly close stale issues that no longer match priorities?"
    elif stale_count and stale_share >= 0.30:
        title = f"{round(stale_share * 100)}% of sampled open issues are stale ({stale_count}/{open_now})"
        insight = (
            f"{stale_count} of {open_now} open issues have had no activity for {stale.get('threshold_days', 90)}+ days."
        )
        question = "Which stale issues represent real unaddressed risk vs backlog clutter we should archive?"
    else:
        net_str = f"{net_8w:+d}" if net_8w is not None else "n/a"
        title = f"Issue backlog is steady ({open_now if open_now is not None else 0} open, 8w net {net_str})"
        insight = (
            f"Sample shows {open_now if open_now is not None else 'n/a'} open issues, 8-week net change {net_str}, "
            f"and {stale_count if stale_count is not None else 0} stale issues."
        )
        question = "Are the highest-value open issues actually moving into active delivery?"

    rows = []
    if open_now is not None:
        rows.append({
            "label": "Open issues (sample)",
            "value": str(open_now),
            "detail": "capped sample" if issues.get("open_truncated") else "complete open count",
        })
    if net_8w is not None:
        rows.append({"label": "8-week net change", "value": f"{net_8w:+d}", "detail": "arrivals minus resolutions"})
    if stale_count is not None:
        rows.append({
            "label": f"Stale ({stale.get('threshold_days', 90)}d+ idle)",
            "value": str(stale_count),
            "detail": f"{round(stale_share * 100)}% of sampled open" if open_now else "0 open",
        })
    lifecycle = issues.get("lifecycle_cfd") or {}
    if lifecycle.get("available") and lifecycle.get("series"):
        latest = lifecycle["series"][-1]
        stages = latest.get("stages") or {}
        if stages:
            top_lc = max(stages.items(), key=lambda pair: pair[1])
            rows.append({"label": "Largest lifecycle stage", "value": f"{top_lc[0]} ({top_lc[1]})", "detail": "latest snapshot"})

    evidence_items = [
        {
            "kind": "issue",
            "number": item.get("number"),
            "title": str(item.get("title") or "")[:120],
            "metric": f"{float(item.get('days_since_activity') or 0):.0f}d idle",
        }
        for item in stale_items[:8]
    ]
    evidence = []
    if open_now is not None:
        evidence.append(f"{open_now} open issues")
    if net_8w is not None:
        evidence.append(f"8w net: {net_8w:+d}")
    if stale_count is not None:
        evidence.append(f"{stale_count} stale")

    slice_obj = {
        "id": "backlog_health",
        "title": "Issue Backlog & Lifecycle",
        "available": True,
        "featured": False,
        "score": score,
        "severity": _severity(score),
        "headline_metric": f"{open_now if open_now is not None else 'n/a'} open · 8w net {f'{net_8w:+d}' if net_8w is not None else 'n/a'}",
        "insight": insight,
        "review_question": question,
        "rows": rows,
        "evidence_items": evidence_items,
        "drilldown": {"view": "issues", "chart_id": "chart-issue-flow", "filter": {}},
    }
    insight_obj = {
        "id": "backlog_health_signal",
        "slice_id": "backlog_health",
        "severity": _severity(score),
        "score": score,
        "title": title,
        "summary": insight,
        "evidence": evidence,
        "review_question": question,
        "drilldown": slice_obj["drilldown"],
    }
    return slice_obj, insight_obj


def _build_value_framing_slice(metrics: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    vc = metrics.get("value_classification") or {}
    prs_vc = vc.get("prs") or {}
    issues_vc = vc.get("issues") or {}
    primary = prs_vc if (prs_vc.get("total") or 0) > 0 else issues_vc
    primary_kind = "PRs" if primary is prs_vc else "Issues"
    total = int(primary.get("total") or 0)
    pv = metrics.get("product_value") or {}
    has_pv = pv.get("status") == "ok" and bool(pv.get("items"))

    if total == 0 and not has_pv:
        return ({
            "id": "value_framing",
            "title": "Content Framing & Expected Value",
            "available": False,
            "featured": False,
            "score": 0,
            "severity": "info",
            "headline_metric": "No framing data",
            "insight": "No PRs or issues were available to classify stated content framing.",
            "review_question": "Are PR and issue descriptions stating the user or business outcome intended?",
            "rows": [],
            "evidence_items": [],
            "drilldown": {"view": "value", "chart_id": "chart-value-framing", "filter": {}},
        }, None)

    counts = primary.get("counts") or {}
    outcome_impact = (counts.get("Outcome", 0) or 0) + (counts.get("Impact", 0) or 0)
    activity_output = (counts.get("Activity", 0) or 0) + (counts.get("Output", 0) or 0) + (counts.get("Input", 0) or 0)
    unknown = counts.get("Unknown", 0) or 0
    outcome_pct = round(outcome_impact / total * 100) if total else 0
    activity_pct = round(activity_output / total * 100) if total else 0

    score = 35
    if total >= 5 and outcome_pct <= 15 and activity_pct >= 60:
        score = 68
    elif total >= 5 and unknown / total >= 0.4:
        score = 55
    if has_pv:
        score = max(score, 60)

    if total >= 5 and outcome_pct <= 15 and activity_pct >= 60:
        title = f"{activity_pct}% of sampled {primary_kind} are framed as activity/output vs {outcome_pct}% outcome/impact"
        insight = (
            f"Based on stated titles/bodies (not verified achieved outcomes), {activity_output}/{total} {primary_kind} "
            f"describe tasks or deliverables while only {outcome_impact}/{total} state a user outcome or business impact."
        )
        question = (
            "Are we shipping the right outcomes, or are PRs/issues being specified purely as tasks without stating user impact?"
        )
    else:
        title = f"Stated framing: {outcome_pct}% outcome/impact · {activity_pct}% activity/output"
        insight = (
            f"Across {total} sampled {primary_kind}, {outcome_impact} state outcome/impact intent and "
            f"{activity_output} state input/activity/output (labels reflect stated intent, not achieved outcomes)."
        )
        question = "Do the highest-effort items in this sample align with the team's top expected outcomes?"

    rows = [
        {"label": "Outcome / Impact (stated)", "value": f"{outcome_impact} ({outcome_pct}%)", "detail": primary_kind},
        {"label": "Input / Activity / Output", "value": f"{activity_output} ({activity_pct}%)", "detail": primary_kind},
    ]
    if unknown:
        rows.append({"label": "Unknown framing", "value": f"{unknown} ({round(unknown / total * 100)}%)", "detail": "insufficient title/body evidence"})

    evidence_items: list[dict[str, Any]] = []
    if has_pv:
        ranked = sorted(
            [i for i in pv.get("items", []) if i.get("status") == "assessed" and i.get("cost_of_delay") is not None],
            key=lambda i: i.get("cost_of_delay") or 0,
            reverse=True,
        )
        if ranked:
            top_cod = ranked[0]
            rows.append({
                "label": "Highest expected CoD",
                "value": f"#{top_cod.get('number')} (CoD {top_cod.get('cost_of_delay')})",
                "detail": f"{len(ranked)} assessed items",
            })
            evidence_items = [
                {
                    "kind": "pr",
                    "number": i.get("number"),
                    "title": str(i.get("title") or "")[:120],
                    "metric": f"CoD {i.get('cost_of_delay')}",
                }
                for i in ranked[:6]
            ]
    if not evidence_items:
        evidence_items = [
            {
                "kind": "pr" if primary_kind == "PRs" else "issue",
                "number": i.get("number"),
                "title": str(i.get("title") or "")[:120],
                "metric": str(i.get("level") or "Unknown"),
            }
            for i in (primary.get("items") or [])[:6]
        ]

    evidence = [f"Outcome/Impact: {outcome_pct}%", f"Activity/Output: {activity_pct}%"]
    if has_pv:
        evidence.append(f"Expected CoD assessed: {pv.get('assessed', 0)}")

    slice_obj = {
        "id": "value_framing",
        "title": "Content Framing & Expected Value",
        "available": True,
        "featured": False,
        "score": score,
        "severity": _severity(score),
        "headline_metric": f"{outcome_pct}% outcome/impact intent",
        "insight": insight,
        "review_question": question,
        "rows": rows,
        "evidence_items": evidence_items,
        "drilldown": {"view": "value", "chart_id": "chart-value-framing", "filter": {}},
    }
    insight_obj = {
        "id": "value_framing_signal",
        "slice_id": "value_framing",
        "severity": _severity(score),
        "score": score,
        "title": title,
        "summary": insight,
        "evidence": evidence,
        "review_question": question,
        "drilldown": slice_obj["drilldown"],
    }
    return slice_obj, insight_obj


def build_operational_review(metrics: dict[str, Any]) -> dict[str, Any]:
    """Deterministically compute operational review insights, questions, and ranked slices."""
    builders = (
        _build_wip_aging_slice,
        _build_flow_predictability_slice,
        _build_contributor_mode_slice,
        _build_batch_size_slice,
        _build_backlog_health_slice,
        _build_value_framing_slice,
    )
    slices: dict[str, dict[str, Any]] = {}
    candidates: list[dict[str, Any]] = []
    for builder in builders:
        slice_obj, insight_obj = builder(metrics)
        slices[slice_obj["id"]] = slice_obj
        if insight_obj is not None:
            candidates.append(insight_obj)

    available_slices = [s for s in slices.values() if s["available"]]
    ranked_slices = sorted(available_slices, key=lambda s: (-s["score"], SLICE_ORDER.index(s["id"])))
    featured_ids = [s["id"] for s in ranked_slices[:4]]
    for sid in featured_ids:
        slices[sid]["featured"] = True

    ranked_insights = sorted(candidates, key=lambda i: (-i["score"], SLICE_ORDER.index(i["slice_id"])))[:5]
    if not ranked_insights:
        ranked_insights = [{
            "id": "sparse_repository_signal",
            "slice_id": "flow_predictability",
            "severity": "info",
            "score": 10,
            "title": "Limited recent PR and issue activity in this sample",
            "summary": "This repository had too few sampled PRs or issues to surface flow anomalies.",
            "evidence": ["0 merged PRs", f"{len(metrics.get('active_wip_points') or [])} open PRs"],
            "review_question": "Is active delivery happening on a different repository or branch workflow?",
            "drilldown": {"view": "overview", "chart_id": None, "filter": {}},
        }]

    review_questions: list[str] = []
    for item in ranked_insights:
        q = item.get("review_question")
        if q and q not in review_questions:
            review_questions.append(q)

    repo = metrics.get("repo") or "this repository"
    top = ranked_insights[0]
    executive_summary = (
        f"Operational review for {repo}: {top['title']}. "
        f"{len(ranked_insights)} key signals and {len(featured_ids)} highlighted slices ready to inspect."
    )

    points = metrics.get("pr_size_scatter_points") or []
    classifications = sorted({p.get("classification") for p in points if p.get("classification")})
    has_size = any((p.get("x") or 0) > 0 for p in points)
    vc_prs = ((metrics.get("value_classification") or {}).get("prs") or {}).get("counts") or {}
    framing_levels = [k for k, v in vc_prs.items() if v]

    return {
        "synthesis_source": "deterministic",
        "executive_summary": executive_summary,
        "insights": ranked_insights,
        "review_questions": review_questions[:5],
        "featured_slices": featured_ids,
        "slices": slices,
        "available_filters": {
            "classifications": classifications,
            "size_buckets": ["XS (<50)", "S (50-199)", "M (200-499)", "L (500+)"] if has_size else [],
            "framing_levels": framing_levels,
            "time_windows": ["all", "4w", "8w"],
        },
    }


def _synthesis_schema() -> dict[str, Any]:
    return {
        "type": "OBJECT",
        "properties": {
            "executive_summary": {"type": "STRING"},
            "insights": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "id": {"type": "STRING"},
                        "title": {"type": "STRING"},
                        "summary": {"type": "STRING"},
                        "review_question": {"type": "STRING"},
                    },
                    "required": ["id", "title", "summary", "review_question"],
                },
            },
            "slice_insights": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "id": {"type": "STRING"},
                        "insight": {"type": "STRING"},
                        "review_question": {"type": "STRING"},
                    },
                    "required": ["id", "insight", "review_question"],
                },
            },
        },
        "required": ["executive_summary", "insights", "slice_insights"],
    }


def _compact_synthesis_input(metrics: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    """Build a privacy-safe aggregate summary (no raw item titles/bodies) for LLM synthesis."""
    return {
        "repo": metrics.get("repo"),
        "visibility": metrics.get("repository_visibility", "public"),
        "insights": [
            {
                "id": item["id"],
                "slice_id": item["slice_id"],
                "severity": item["severity"],
                "title": item["title"],
                "summary": item["summary"],
                "evidence": item["evidence"],
                "review_question": item["review_question"],
            }
            for item in review["insights"]
        ],
        "featured_slices": [
            {
                "id": sid,
                "title": review["slices"][sid]["title"],
                "headline_metric": review["slices"][sid]["headline_metric"],
                "rows": review["slices"][sid]["rows"],
                "insight": review["slices"][sid]["insight"],
            }
            for sid in review["featured_slices"]
            if sid in review["slices"]
        ],
    }


async def synthesize_operational_review(
    metrics: dict[str, Any],
    *,
    gemini_key: str | None = None,
    allow_llm: bool = True,
) -> dict[str, Any]:
    """Compute deterministic operational review and optionally sharpen narrative via Gemini."""
    review = build_operational_review(metrics)
    if not allow_llm or not gemini_key or not review["featured_slices"]:
        return review

    payload_in = _compact_synthesis_input(metrics, review)
    prompt = (
        "You are an engineering flow coach preparing an operational review brief. "
        "Sharpen the executive_summary, insight titles/summaries, review_questions, and slice insights "
        "using ONLY the supplied aggregate numbers and item numbers. Never invent metrics, never treat missing "
        "data as zero, and preserve the distinction between stated intent (framing) and achieved outcomes. "
        "Make each review_question concrete and discussion-ready for a team operational review. "
        "Keep every id exactly as provided.\n"
        + json.dumps(payload_in, separators=(",", ":"), ensure_ascii=False)
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.25,
            "maxOutputTokens": 900,
            "responseMimeType": "application/json",
            "responseSchema": _synthesis_schema(),
        },
    }
    try:
        status, _, data = await request_json(
            GEMINI_URL,
            method="POST",
            headers={"x-goog-api-key": gemini_key},
            body=body,
            timeout=GEMINI_TIMEOUT,
        )
        if status < 200 or status >= 300 or not isinstance(data, dict):
            return review
        candidates = data.get("candidates") or []
        parts = (candidates[0].get("content") or {}).get("parts") or [] if candidates else []
        parsed = json.loads("".join(part.get("text", "") for part in parts if isinstance(part.get("text"), str)))
        if not isinstance(parsed, dict):
            return review
    except Exception:
        return review

    exec_summary = parsed.get("executive_summary")
    if isinstance(exec_summary, str) and exec_summary.strip():
        review["executive_summary"] = exec_summary.strip()[:500]

    by_id = {item["id"]: item for item in review["insights"]}
    for update in parsed.get("insights") or []:
        if not isinstance(update, dict):
            continue
        target = by_id.get(update.get("id"))
        if not target:
            continue
        for field, limit in (("title", 180), ("summary", 400), ("review_question", 260)):
            val = update.get(field)
            if isinstance(val, str) and val.strip():
                target[field] = val.strip()[:limit]

    for update in parsed.get("slice_insights") or []:
        if not isinstance(update, dict):
            continue
        target_slice = review["slices"].get(update.get("id"))
        if not target_slice:
            continue
        for field, limit in (("insight", 400), ("review_question", 260)):
            val = update.get(field)
            if isinstance(val, str) and val.strip():
                target_slice[field] = val.strip()[:limit]

    review["review_questions"] = [
        item["review_question"] for item in review["insights"] if item.get("review_question")
    ][:5]
    review["synthesis_source"] = "llm"
    return review


def _redact_items(items: list[dict[str, Any]], *, private: bool) -> list[dict[str, Any]]:
    if not private:
        return items
    return [
        {"kind": item.get("kind"), "number": item.get("number"), "metric": item.get("metric")}
        for item in items
    ]


def public_operational_review(review: dict[str, Any] | None, *, private: bool = False) -> dict[str, Any] | None:
    """Return a compact, privacy-safe operational review projection for MCP structuredContent."""
    if not isinstance(review, dict):
        return None
    slices = review.get("slices") or {}
    featured = []
    for sid in review.get("featured_slices") or []:
        s = slices.get(sid)
        if not s:
            continue
        featured.append({
            "id": s["id"],
            "title": s["title"],
            "severity": s["severity"],
            "headline_metric": s["headline_metric"],
            "insight": s["insight"],
            "review_question": s["review_question"],
            "rows": s.get("rows") or [],
            "drilldown": s.get("drilldown") or {},
        })
    return {
        "synthesis_source": review.get("synthesis_source", "deterministic"),
        "executive_summary": review.get("executive_summary", ""),
        "key_insights": [
            {
                "id": item["id"],
                "slice_id": item["slice_id"],
                "severity": item["severity"],
                "title": item["title"],
                "summary": item["summary"],
                "evidence": item.get("evidence") or [],
                "review_question": item.get("review_question", ""),
                "drilldown": item.get("drilldown") or {},
            }
            for item in (review.get("insights") or [])
        ],
        "review_questions": review.get("review_questions") or [],
        "featured_slices": featured,
        "available_slices": [sid for sid, s in slices.items() if s.get("available")],
        "private_titles_redacted": private,
    }


def get_slice_detail(metrics: dict[str, Any], slice_id: str, *, private: bool = False) -> dict[str, Any] | None:
    """Return focused slice details + evidence items for MCP drill-down."""
    review = metrics.get("operational_review") or build_operational_review(metrics)
    slice_obj = (review.get("slices") or {}).get(slice_id)
    if not slice_obj:
        return None
    return {
        "repo": metrics.get("repo"),
        "visibility": metrics.get("repository_visibility", "public"),
        "analysis_id": metrics.get("analysis_id"),
        "slice": {
            "id": slice_obj["id"],
            "title": slice_obj["title"],
            "available": slice_obj["available"],
            "featured": slice_obj["featured"],
            "severity": slice_obj["severity"],
            "score": slice_obj["score"],
            "headline_metric": slice_obj["headline_metric"],
            "insight": slice_obj["insight"],
            "review_question": slice_obj["review_question"],
            "rows": slice_obj.get("rows") or [],
            "evidence_items": _redact_items(slice_obj.get("evidence_items") or [], private=private),
            "drilldown": slice_obj.get("drilldown") or {},
        },
        "view": (slice_obj.get("drilldown") or {}).get("view", "insights"),
    }
