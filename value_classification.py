"""Content framing, adapted from Jira's rule classifier (no runtime repo dependency).

Source: jira-flow-coaching-dashboard/offline_analysis_value.py @ 4d02926c8fe5766d876034dccca5a0c901a1c1a0
Taxonomy: ai-skill-library/skills/outcome-framing-coach.md, version 1.1.0.
This labels stated intent, never verifies achieved value. Keyword rules have known
semantic limits; preserve method, rationale, and missing-body coverage in results.
"""
import re
from html import escape

VERSION = "jira-rules-1.1.0-github-1"
LEVELS = ["Input", "Activity", "Output", "Outcome", "Impact", "Unknown"]

# Quarterly bucket prefix — any epic whose summary starts with FY##Q# or Y##Q# is a
# time-box bucket, not a deliverable, regardless of what's named inside it.
# This corrects a systematic LLM (Haiku) blind spot that picks up the deliverable
# described inside the bucket and classifies it as Output instead of Activity.
QUARTERLY_BUCKET_RE = re.compile(
    r"^(FY\s*'?\d{2,4}\s*Q\d|Y\s*'?\d{2,4}\s*Q\d)",
    re.IGNORECASE,
)

# ─── Keyword signals per taxonomy level ───────────────────────────────────────
INPUT_SIGNALS = [
    r"\bhiring\b",
    r"\bheadcount\b",
    r"\bstaffing\b",
    r"\bbudget\b",
    r"\bopex\b",
    r"\bcapex\b",
    r"\bresource allocation\b",
    r"\bonboarding\b",
]
ACTIVITY_SIGNALS = [
    r"\bbug\b",
    r"\bfix\b",
    r"\bfixes\b",
    r"\bfixed\b",
    r"\bqa\b",
    r"\bqat\b",
    r"\btest\b",
    r"\btesting\b",
    r"\bregression\b",
    r"\buat\b",
    r"\bspike\b",
    r"\bdiscovery\b",
    r"\bexplore\b",
    r"\bexploring\b",
    r"\btraining\b",
    r"\blearn\b",
    r"\bonboard\b",
    r"\bmaintenance\b",
    r"\bupgrade\b",
    r"\bupgrades\b",
    r"\bmigrate\b",
    r"\brefactor\b",
    r"\bdebug\b",
    r"\binvestigate\b",
    r"\breview\b",
    r"\bauditing\b",
    r"\bsupport\b",
    r"\bmeeting\b",
    r"\bgrooming\b",
    r"\bplanning\b",
    r"\bdeploy\b",
    r"\bdeployment\b",
    r"\bconfigure\b",
    r"\bsetup\b",
    r"\bset up\b",
    r"\bmonitor\b",
    r"\bmonitoring\b",
    r"\bscan\b",
    r"\bscanning\b",
    r"\bcleanup\b",
    r"\bcleaning\b",
    r"\bpatch\b",
    r"\bhardening\b",
    r"\bdevops\b",
    r"\bci\/cd\b",
    r"\bpipeline\b",
    r"\binfrastructure\b",
    r"\bsecurity\b",
    r"\bcompliance\b",
    r"\bingest\b",
    r"\bingestion\b",
    r"\bimport\b",
    r"\bexport\b",
    r"\bextract\b",
    r"\bparsing\b",
    r"\bprocessing\b",
    r"\bsync\b",
    r"\bscrape\b",
    r"\bcrawl\b",
]
OUTPUT_SIGNALS = [
    r"\bimplement\b",
    r"\bimplementation\b",
    r"\bbuild\b",
    r"\bbuildout\b",
    r"\bcreate\b",
    r"\bdevelop\b",
    r"\badd\b",
    r"\bintegrat\b",
    r"\blaunch\b",
    r"\brelease\b",
    r"\bship\b",
    r"\bdeploy\b",
    r"\bfeature\b",
    r"\benhancement\b",
    r"\bcomponent\b",
    r"\bendpoint\b",
    r"\bapi\b",
    r"\bui\b",
    r"\bpage\b",
    r"\bdashboard\b",
    r"\bmodal\b",
    r"\bmodule\b",
    r"\bservice\b",
    r"\btool\b",
    r"\bsystem\b",
    r"\bphase\b",
    r"\brollout\b",
    r"\bupdate\b",
    r"\bupgrade\b",
    r"\bredesign\b",
    r"\brebuild\b",
    r"\bnew\b",
]
OUTCOME_SIGNALS = [
    # Clear user-capability language
    r"\busers? can\b",
    r"\bability to\b",
    r"\bempower\b",
    r"\bimprove.{0,30}experience\b",
    r"\breduce.{0,30}time\b",
    r"\bfaster.{0,20}(for users?|workflow|process)\b",
    r"\beasier.{0,20}(for users?|to use|to access)\b",
    r"\bself.?serv\b",
    r"\bself.?service\b",
    # "enable users", "allow users" — only when tied to users, not just "enable SSL"
    r"\b(enable|allow)\b.{0,30}\busers?\b",
    r"\busers?.{0,30}\b(enable|allow|can|able to)\b",
]
# Impact: ONLY when improvement/goal verb is contextually linked to a business metric.
# Bare metric words (revenue, NPS, etc.) in artifact/data context do NOT qualify.
IMPACT_SIGNALS = [
    # Improvement verb + business metric (within ~60 chars).
    # "retention" excluded here because it's also a technical term (log retention, data retention).
    r"\b(increase|improve|boost|grow|accelerate|maximise|maximize)\b.{0,60}\b(revenue|conversion|profit|nps|satisfaction|margin|ltv|roi|arr|mrr)\b",
    # Customer/user retention specifically — requires "customer" or "user" nearby
    r"\b(increase|improve|reduce|boost|grow)\b.{0,40}\b(customer|user|subscriber|reader)\b.{0,40}\bretention\b",
    r"\bretention\b.{0,40}\b(customer|user|subscriber|reader)\b",
    r"\b(reduce|minimize|minimise|decrease|lower|cut|eliminate)\b.{0,60}\b(churn|cac|friction|dropout|attrition)\b",
    r"\b(reduce|cut|lower)\b.{0,30}\bcosts?\b.{0,20}\b(by|through|via|target|goal|objective)\b",
    # Metric + explicit goal language
    r"\b(revenue|conversion|profit|nps|satisfaction|margin|ltv|roi|arr|mrr)\b.{0,60}\b(growth|increase|improvement|goal|target|objective|uplift|opportunity)\b",
    # Explicit business-goal phrases
    r"\bbusiness (result|objective|goal|kpi|impact|value proposition)\b",
    r"\b(unlock|drive|generate|capture)\b.{0,40}\b(revenue|value|growth|profit|market)\b",
    r"\breturn on investment\b",
    r"\bnet promoter\b",
    # "cost savings" / "time savings" as standalone words (not "timezone")
    r"\b(cost|time)\b.{0,20}\b(savings?|reduction goal|reduction target)\b",
]

# Negative context: metric words used as data-artifact labels rather than goals.
# If a summary matches Impact but also matches this, demote to Output/Activity.
DATA_ARTIFACT_EXCLUSION_RE = re.compile(
    r"\b(revenue|conversion|retention|churn|profit|nps|satisfaction|cost|metric|kpi)\b"
    r".{0,50}"
    r"\b(report|dashboard|csv|file|table|dataset|feed|extract|ingest|import|export|"
    r"  tableau|bi|spreadsheet|data|query|schema|field|column|view|chart|graph|summary)\b"
    r"|"
    r"\b(report|dashboard|csv|file|table|dataset|feed|extract|ingest|import|export|"
    r"tableau|bi|spreadsheet|data|query|schema|field|column|view|chart|graph|summary)\b"
    r".{0,50}"
    r"\b(revenue|conversion|retention|churn|profit|nps|satisfaction|cost|metric|kpi)\b",
    re.IGNORECASE | re.VERBOSE,
)

# Prescriptive verbs (output-framing smell)
PRESCRIPTIVE_VERBS_RE = re.compile(
    r"\b(build|create|implement|setup|set up|add|integrate|develop|launch|deploy|"
    r"configure|migrate|establish|introduce|rollout|release|redesign|rebuild|"
    r"buildout|build out)\b",
    re.IGNORECASE,
)


def compile_patterns(signals):
    return [re.compile(p, re.IGNORECASE) for p in signals]


INPUT_RE = compile_patterns(INPUT_SIGNALS)
ACTIVITY_RE = compile_patterns(ACTIVITY_SIGNALS)
OUTPUT_RE = compile_patterns(OUTPUT_SIGNALS)
OUTCOME_RE = compile_patterns(OUTCOME_SIGNALS)
IMPACT_RE = compile_patterns(IMPACT_SIGNALS)


def score(text, patterns):
    return sum(1 for p in patterns if p.search(text))


def classify(summary, issue_type):
    """Return (taxonomy_level, prescriptive_verbs, rationale)"""
    s = summary.lower()

    # Bug type always → Activity
    if issue_type.lower() == "bug":
        verbs = ", ".join(sorted(set(PRESCRIPTIVE_VERBS_RE.findall(summary))))
        return "Activity", verbs, "Describes bug or defect repair work"

    scores = {
        "Impact": score(s, IMPACT_RE),
        "Outcome": score(s, OUTCOME_RE),
        "Output": score(s, OUTPUT_RE),
        "Activity": score(s, ACTIVITY_RE),
        "Input": score(s, INPUT_RE),
    }

    # Demote Impact when metric words appear in a data-artifact context
    # (e.g. "Revenue Report", "NPS dashboard CSV") — those are Outputs, not goals
    if scores["Impact"] > 0 and DATA_ARTIFACT_EXCLUSION_RE.search(s):
        scores["Impact"] = 0

    # Impact also requires no competing strong Activity signal —
    # ingestion/processing tasks are Activities even if the subject is a metric
    if scores["Impact"] > 0 and scores["Activity"] >= 2:
        scores["Impact"] = 0

    # Priority order: Impact > Outcome > Output > Activity > Input
    for level in ["Impact", "Outcome", "Output", "Activity", "Input"]:
        if scores[level] > 0:
            verbs = ", ".join(sorted(set(PRESCRIPTIVE_VERBS_RE.findall(summary))))
            rationale = _rationale(level, scores)
            return level, verbs, rationale

    # Default for epics with no strong signal
    if issue_type.lower() == "epic":
        verbs = ", ".join(sorted(set(PRESCRIPTIVE_VERBS_RE.findall(summary))))
        return (
            "Output",
            verbs,
            "Epic with no clear signal — defaulted to Output (describes a deliverable scope)",
        )

    return "Activity", "", "No strong signal — defaulted to Activity (task/work item)"


def _rationale(level, scores):
    hits = [k for k, v in scores.items() if v > 0]
    signal_str = ", ".join(hits) if hits else "none"
    if level == "Activity":
        return f"Contains activity/task language (testing, QA, maintenance, discovery, bug-fix). Signal: {signal_str}"
    if level == "Output":
        return (
            f"Describes a deliverable artifact or feature to build/implement. Signal: {signal_str}"
        )
    if level == "Outcome":
        return (
            f"References user capability or measurable experience improvement. Signal: {signal_str}"
        )
    if level == "Impact":
        return f"References a business metric or bottom-line result. Signal: {signal_str}"
    if level == "Input":
        return f"References resource, budget, or staffing allocation. Signal: {signal_str}"
    return ""



def classify_content(item):
    """Apply the shared taxonomy to a GitHub item's own title and body."""
    title = (item.get("title") or "").strip()
    body = (item.get("body") or "").strip()
    content = title + "\n" + body
    defaulted = False
    # Preserve Jira's quarterly-container rule; GitHub has no Epic type.
    if not content.strip():
        level, verbs, rationale = "Unknown", "", "No title or body available."
    elif QUARTERLY_BUCKET_RE.match(title):
        level, verbs, rationale = "Activity", "", "Quarterly bucket describes a container for work."
    else:
        # Explicit fix framing corresponds to Jira's Bug override. Labels are not content.
        bug = bool(re.match(r"^(?:fix(?:es|ed)?|bug)(?:\b|\()", title, re.I))
        level, verbs, rationale = classify(content, "Bug" if bug else "Task")
        defaulted = rationale.startswith("No strong signal")
    return {"level": level, "rationale": rationale, "prescriptive_verbs": verbs,
            "method": "rules", "version": VERSION, "defaulted": defaulted,
            "body_available": "body" in item and item["body"] is not None}


def summarize_items(cohorts):
    counts = dict.fromkeys(LEVELS, 0)
    items, seen = [], set()
    for cohort, rows in cohorts:
        for item in rows or []:
            number = item.get("number")
            if number is not None and number in seen:
                continue
            if number is not None:
                seen.add(number)
            result = classify_content(item)
            counts[result["level"]] += 1
            items.append({"number": number, "title": item.get("title") or "",
                          "cohort": cohort, **result})
    return {"total": len(items), "counts": counts, "items": items,
            "body_available": sum(i["body_available"] for i in items),
            "defaulted": sum(i["defaulted"] for i in items)}


def compute_value_classification(data):
    raw_issues = data.get("issues")
    return {"method": "rules", "version": VERSION, "levels": LEVELS,
            "scope": "Sampled recent merged and open PRs; sampled open and closed issues. Historical baseline excluded. Counts are items, not effort or value.",
            "prs": summarize_items([("merged", data.get("recent_prs")), ("open", data.get("open_prs"))]),
            "issues": None if raw_issues is None else summarize_items([
                ("open", raw_issues.get("open")), ("closed", raw_issues.get("closed"))])}


def render_value_report(data):
    value = compute_value_classification(data)
    parts = ['<section><h2>Content framing</h2><p>Rule-based title/body classification; not evidence of achieved outcomes. '
             'Input = resources; Activity = work; Output = deliverable; Outcome = user capability; Impact = business result.</p>',
             '<p>' + escape(value["scope"]) + '</p>']
    for label, key in [("PRs", "prs"), ("Issues", "issues")]:
        group = value[key]
        if group is None:
            parts.append('<p>Issues were not collected.</p>')
            continue
        parts.append('<h3>' + label + '</h3><p>' + escape(str(group["counts"])) + '</p>')
        parts.append(f'<p>Bodies available: {group["body_available"]}/{group["total"]}; '
                     f'defaulted: {group["defaulted"]}. Refresh older caches to fetch bodies.</p>')
        parts.append('<table><thead><tr><th>Item</th><th>State</th><th>Framing</th><th>Reason</th></tr></thead><tbody>')
        for item in group["items"]:
            parts.append('<tr>' + ''.join('<td>' + escape(str(v)) + '</td>' for v in [
                '#' + str(item['number']) + ' ' + item['title'], item['cohort'], item['level'], item['rationale']]) + '</tr>')
        parts.append('</tbody></table>')
    return ''.join(parts) + '</section>'
