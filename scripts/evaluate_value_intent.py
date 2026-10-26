#!/usr/bin/env python3
"""Bounded opt-in value-intent evaluation; reference labels are agent-provisional.

Offline: python scripts/evaluate_value_intent.py --results baseline.json candidate.json
Live: python scripts/evaluate_value_intent.py --live --output /tmp/run.json
Keys are read only from the environment and are never written to checkpoints.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "tests/fixtures/value_intent/public_prs.json"
SCALE = [1, 2, 3, 5, 8, 13]
MAX_REQUESTS = 60


def fingerprint(case, context, rubric_version):
    inputs = {key: case.get(key) for key in ("id", "repository", "number", "title", "body", "body_truncated")}
    inputs.update(context=context, rubric_version=rubric_version)
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _metrics(cases, records):
    by_id = {record["case_id"]: record["result"] for record in records}
    counts = dict.fromkeys(("cases", "completed", "missing", "assessed", "abstained", "provider_failures",
                            "expected_assessable", "expected_indeterminate", "assessable_scored",
                            "correct_abstentions", "unexpected_abstentions", "indeterminate_scored",
                            "compared_dimensions", "within_allowed_dimensions", "coarse_disagreement_dimensions"), 0)
    disagreements = []
    for case in cases:
        counts["cases"] += 1
        expected = case["expected"]
        counts["expected_assessable" if expected["assessable"] else "expected_indeterminate"] += 1
        result = by_id.get(case["id"])
        if result is None:
            counts["missing"] += 1
            continue
        counts["completed"] += 1
        if result.get("status") != "assessed":
            if result.get("error_code") == "insufficient_context":
                counts["abstained"] += 1
                counts["unexpected_abstentions" if expected["assessable"] else "correct_abstentions"] += 1
            else:
                counts["provider_failures"] += 1
            continue
        counts["assessed"] += 1
        if not expected["assessable"]:
            counts["indeterminate_scored"] += 1
            continue
        counts["assessable_scored"] += 1
        for dimension, allowed in expected["dimensions"].items():
            score = result.get("dimensions", {}).get(dimension, {}).get("score")
            if score not in SCALE:
                disagreements.append({"case_id": case["id"], "dimension": dimension, "issue": "invalid_or_missing_score"})
                continue
            distance = min(abs(SCALE.index(score) - SCALE.index(reference)) for reference in allowed)
            counts["compared_dimensions"] += 1
            counts["within_allowed_dimensions"] += distance == 0
            counts["coarse_disagreement_dimensions"] += distance > 1
            if distance:
                disagreements.append({"case_id": case["id"], "dimension": dimension, "score": score,
                                      "allowed": allowed, "rubric_level_distance": distance})
    def rate(numerator, denominator):
        return round(numerator / denominator, 4) if denominator else None
    return {**counts, "assessment_coverage": rate(counts["assessed"], counts["completed"]),
            "assessable_coverage": rate(counts["assessable_scored"], counts["expected_assessable"]),
            "allowed_dimension_agreement": rate(counts["within_allowed_dimensions"], counts["compared_dimensions"]),
            "coarse_dimension_disagreement": rate(counts["coarse_disagreement_dimensions"], counts["compared_dimensions"]),
            "disagreements": disagreements}


def evaluate(cases, records):
    known_ids = {case["id"] for case in cases}
    ids = [record["case_id"] for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate result case_id")
    if set(ids) - known_ids:
        raise ValueError("Results contain case IDs absent from selected dataset")
    return {"interpretation": "Agreement with agent-provisional references, not validated accuracy.",
            "overall": _metrics(cases, records),
            "partitions": {name: _metrics([case for case in cases if case["partition"] == name], records)
                           for name in sorted({case["partition"] for case in cases})},
            "repositories": {repo: _metrics([case for case in cases if case["repository"] == repo], records)
                             for repo in sorted({case["repository"] for case in cases})}}


def _save(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def offline_evaluate(cases, checkpoint, contexts=None):
    """Verify inference inputs before attributing saved scores to selected cases."""
    effective_contexts = contexts if contexts is not None else checkpoint.get("contexts")
    if not isinstance(effective_contexts, dict):
        raise ValueError("Offline comparison requires the original repository contexts")
    if any(case["repository"] not in effective_contexts for case in cases):
        raise ValueError("Repository context missing for selected case")
    rubric = checkpoint.get("rubric_version")
    expected = {case["id"]: fingerprint(case, effective_contexts[case["repository"]], rubric) for case in cases}
    if not isinstance(rubric, str) or checkpoint.get("manifest") != expected:
        raise ValueError("Offline fingerprint mismatch; descriptions or contexts changed")
    records = checkpoint["records"]
    if any(record.get("fingerprint") != expected.get(record.get("case_id")) for record in records):
        raise ValueError("Offline record fingerprint mismatch")
    return evaluate(cases, records)


async def run_cases(cases, contexts, rubric_version, output, key, assessor, *, budget=MAX_REQUESTS, resume=False):
    if not key:
        raise ValueError("Jev key is not configured in the environment")
    if not 1 <= budget <= MAX_REQUESTS:
        raise ValueError(f"Request budget must be between 1 and {MAX_REQUESTS}")
    if any(case["repository"] not in contexts for case in cases):
        raise ValueError("Repository context missing for selected case")
    output = Path(output)
    manifest = {case["id"]: fingerprint(case, contexts[case["repository"]], rubric_version) for case in cases}
    if len(manifest) != len(cases):
        raise ValueError("Duplicate dataset case ID")
    checkpoint = {"schema_version": 2, "rubric_version": rubric_version, "manifest": manifest,
                  "contexts": {repo: contexts[repo] for repo in {case["repository"] for case in cases}},
                  "attempts": 0, "records": []}
    if output.exists():
        if not resume:
            raise ValueError("Output already exists; use --resume or a new output file")
        checkpoint = json.loads(output.read_text(encoding="utf-8"))
        if checkpoint.get("rubric_version") != rubric_version or checkpoint.get("manifest") != manifest:
            raise ValueError("Resume fingerprint mismatch; inputs/rubric changed. Use a new output file")
        if checkpoint.get("attempts") != len(checkpoint.get("records", [])):
            raise ValueError("Invalid checkpoint attempt count")
        record_ids = [record["case_id"] for record in checkpoint["records"]]
        if len(record_ids) != len(set(record_ids)) or set(record_ids) - manifest.keys():
            raise ValueError("Invalid checkpoint case IDs; records must be unique selected cases")
        if any(record.get("fingerprint") != manifest[record["case_id"]] for record in checkpoint["records"]):
            raise ValueError("Resume record fingerprint mismatch; use a new output file")
        checkpoint["contexts"] = {repo: contexts[repo] for repo in {case["repository"] for case in cases}}
    completed = {record["case_id"] for record in checkpoint["records"]}
    for case in cases:
        if case["id"] in completed or checkpoint["attempts"] >= budget:
            continue
        record = {"case_id": case["id"], "fingerprint": manifest[case["id"]],
                  "result": {"status": "failed", "error_code": "interrupted_attempt"}}
        checkpoint["attempts"] += 1
        checkpoint["records"].append(record)
        _save(output, checkpoint)
        item = {"number": case["number"], "title": case["title"], "cohort": "evaluation", "level": "Unclassified"}
        body = case["body"]
        if case.get("body_truncated") and len(body) == 1500:
            body += " "
        try:
            record["result"] = await assessor(key, contexts[case["repository"]], item, {"body": body})
        except Exception:
            record["result"] = {"status": "failed", "error_code": "evaluator_error"}
        _save(output, checkpoint)
    return checkpoint


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--results", nargs="+", type=Path, help="Offline checkpoint files to summarize/compare")
    parser.add_argument("--live", action="store_true", help="Explicitly allow paid Jev calls; no Gemini calls")
    parser.add_argument("--contexts", type=Path, help="Repo-name to context-object JSON; overrides frozen draft contexts")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--limit", type=int, default=MAX_REQUESTS)
    parser.add_argument("--budget", type=int, default=MAX_REQUESTS)
    parser.add_argument("--partition", choices=("all", "development", "held_out"), default="all")
    parser.add_argument("--include-controls", action="store_true")
    args = parser.parse_args(argv)
    try:
        dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
        cases = [case for case in dataset["cases"] if args.partition == "all" or case["partition"] == args.partition]
        if args.include_controls:
            cases += dataset["controls"]
        if args.limit < 1:
            raise ValueError("--limit must be positive")
        cases = cases[:args.limit]
        if args.live:
            if args.results or args.output is None:
                raise ValueError("--live requires --output and cannot be combined with --results")
            key = next((os.environ[name] for name in ("JEV_API_KEY", "TYPESAFE_API_KEY", "TYPESAFE_API_TOKEN", "typesafe_api_token") if os.environ.get(name)), "")
            if not key:
                raise ValueError("Jev key not configured; export JEV_API_KEY or TYPESAFE_API_TOKEN from your secret manager")
            contexts = dict(dataset["contexts"])
            if args.contexts:
                contexts.update(json.loads(args.contexts.read_text(encoding="utf-8")))
            sys.path.insert(0, str(ROOT))
            import product_value
            rubric = hashlib.sha256(Path(product_value.__file__).read_bytes()).hexdigest()
            checkpoint = asyncio.run(run_cases(cases, contexts, rubric, args.output, key,
                                               product_value.assess_pr, budget=args.budget, resume=args.resume))
            summaries = {str(args.output): evaluate(cases, checkpoint["records"])}
        else:
            if not args.results:
                raise ValueError("Supply --results for offline evaluation or --live for explicit paid calls")
            summaries = {}
            for path in args.results:
                checkpoint = json.loads(path.read_text(encoding="utf-8"))
                contexts = None
                if args.contexts or not checkpoint.get("contexts"):
                    contexts = dict(dataset["contexts"])
                    if args.contexts:
                        contexts.update(json.loads(args.contexts.read_text(encoding="utf-8")))
                summaries[str(path)] = offline_evaluate(cases, checkpoint, contexts)
        print(json.dumps(summaries, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
