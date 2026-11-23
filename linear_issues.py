"""Collect Linear team issues and state-transition history for GitHub + Linear combo analysis.

Uses `github_api.request_json` so the same collector runs both locally and inside
Cloudflare Workers.
"""

import asyncio
import datetime
import json
import re
import subprocess
from collections import Counter
from urllib.parse import urlparse

import httpx

import github_api

LINEAR_API_URL = "https://api.linear.app/graphql"
LINEAR_ISSUE_PAGE_CAP = 5
LINEAR_PAGE_SIZE = 50
LINEAR_HISTORY_LIMIT = 25
ISSUE_WINDOW_WEEKS = 52

_TEAM_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
_ORG_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_LINEAR_ISSUE_URL_RE = re.compile(
    r"https?://linear\.app/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/issue/([A-Za-z][A-Za-z0-9_-]{0,31})-\d+\b",
    re.IGNORECASE,
)
_LINEAR_TEAM_URL_RE = re.compile(
    r"https?://linear\.app/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/team/([A-Za-z][A-Za-z0-9_-]{0,31})\b",
    re.IGNORECASE,
)
_LINEAR_LINKBACK_MARKER = "<!-- linear-linkback -->"

_STATE_TYPE_ORDER = {
    "triage": 0,
    "backlog": 1,
    "unstarted": 2,
    "started": 3,
    "completed": 4,
    "canceled": 5,
    "duplicate": 6,
}
_CLOSED_STATE_TYPES = frozenset({"completed", "canceled", "duplicate"})

_TEAM_QUERY = """
query($key: String!) {
  teams(filter: { key: { eq: $key } }, first: 10) {
    nodes {
      id
      key
      name
      organization {
        urlKey
        name
      }
      states(first: 100) {
        nodes {
          id
          name
          type
          position
        }
      }
    }
  }
}
"""

_TEAM_ISSUES_QUERY = """
query($teamId: String!, $first: Int!, $after: String) {
  team(id: $teamId) {
    issues(first: $first, after: $after, orderBy: updatedAt) {
      pageInfo {
        hasNextPage
        endCursor
      }
      nodes {
        id
        identifier
        number
        title
        description
        createdAt
        updatedAt
        startedAt
        completedAt
        canceledAt
        url
        branchName
        state {
          id
          name
          type
          position
        }
        labels(first: 5) {
          nodes {
            name
          }
        }
        attachments(first: 5) {
          nodes {
            url
            title
            sourceType
          }
        }
        history(first: 25) {
          pageInfo {
            hasNextPage
          }
          nodes {
            createdAt
            fromState {
              name
              type
            }
            toState {
              name
              type
            }
          }
        }
      }
    }
  }
}
"""


class LinearError(ValueError):
    """Sanitized Linear API or configuration error with an HTTP status code."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def parse_linear_team(value: str) -> dict:
    """Parse a Linear team URL, org/KEY slug, or bare team KEY.

    Examples:
      - https://linear.app/yeret-agility/team/CRM/overview -> {"org": "yeret-agility", "team_key": "CRM"}
      - yeret-agility/CRM -> {"org": "yeret-agility", "team_key": "CRM"}
      - CRM -> {"org": None, "team_key": "CRM"}
    """
    raw = (value or "").strip().rstrip("/")
    if not raw:
        raise LinearError("Enter a Linear team URL (linear.app/<org>/team/<KEY>) or team key.", status_code=400)

    org = None
    team_key = None
    if raw.startswith(("http://", "https://")) or raw.startswith("linear.app/"):
        candidate = raw if "://" in raw else f"https://{raw}"
        parsed = urlparse(candidate)
        if parsed.scheme not in ("http", "https") or (parsed.hostname or "").lower() != "linear.app":
            raise LinearError("Enter a valid linear.app team URL or team key.", status_code=400)
        parts = [segment for segment in parsed.path.split("/") if segment]
        # Expected: /<org>/team/<KEY>[/overview|/active|/all|...]
        if len(parts) >= 3 and parts[1].lower() == "team":
            org, team_key = parts[0], parts[2]
        else:
            raise LinearError(
                "Linear URL must point to a team (for example https://linear.app/<workspace>/team/<KEY>/overview).",
                status_code=400,
            )
    elif "/" in raw:
        parts = raw.split("/")
        if len(parts) != 2:
            raise LinearError("Enter a Linear team as workspace/KEY, KEY, or a linear.app team URL.", status_code=400)
        org, team_key = parts
    else:
        team_key = raw

    if org is not None and not _ORG_SLUG_RE.fullmatch(org):
        raise LinearError("Invalid Linear workspace slug.", status_code=400)
    if not team_key or not _TEAM_KEY_RE.fullmatch(team_key):
        raise LinearError("Invalid Linear team key.", status_code=400)
    return {"org": org.lower() if org else None, "team_key": team_key.upper()}


def order_workflow_states(states: list[dict]) -> list[dict]:
    """Order Linear workflow states from intake to completion."""
    valid = [s for s in (states or []) if isinstance(s, dict) and isinstance(s.get("name"), str) and s["name"]]
    valid.sort(key=lambda s: (
        _STATE_TYPE_ORDER.get(s.get("type"), 99),
        float(s.get("position")) if isinstance(s.get("position"), (int, float)) else 0.0,
        s["name"].lower(),
    ))
    seen = set()
    ordered = []
    for state in valid:
        if state["name"] not in seen:
            seen.add(state["name"])
            ordered.append({"name": state["name"], "type": state.get("type") or "started"})
    return ordered


def _parse_iso(date_str: str | None) -> datetime.datetime | None:
    if not isinstance(date_str, str) or not date_str:
        return None
    try:
        return datetime.datetime.strptime(date_str.replace("Z", "")[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def extract_linked_pr_numbers(attachments: list[dict] | None, description: str | None, repo: str | None) -> list[int]:
    """Find GitHub PR numbers in the paired repo referenced by attachments or description."""
    if not repo:
        return []
    pattern = re.compile(rf"https?://github\.com/{re.escape(repo)}/pull/(\d+)\b", re.IGNORECASE)
    found = []
    seen = set()
    sources = [a.get("url") for a in (attachments or []) if isinstance(a, dict) and isinstance(a.get("url"), str)]
    if isinstance(description, str) and description:
        sources.append(description)
    for text in sources:
        for match in pattern.finditer(text):
            number = int(match.group(1))
            if number not in seen:
                seen.add(number)
                found.append(number)
    return found


def detect_linear_team_from_texts(texts=(), *, weighted_texts=()) -> dict | None:
    """Detect the dominant Linear (org, team_key) referenced in GitHub PR bodies or bot comments."""
    scores: Counter[tuple[str, str]] = Counter()
    mentions: Counter[tuple[str, str]] = Counter()

    def scan(items, weight):
        for text in items or ():
            if not isinstance(text, str) or "linear.app/" not in text.lower():
                continue
            for pattern in (_LINEAR_ISSUE_URL_RE, _LINEAR_TEAM_URL_RE):
                for match in pattern.finditer(text):
                    pair = (match.group(1).lower(), match.group(2).upper())
                    scores[pair] += weight
                    mentions[pair] += 1

    scan(weighted_texts, 3)
    scan(texts, 1)
    if not scores:
        return None
    (org, team_key), _ = min(scores.items(), key=lambda item: (-item[1], -mentions[item[0]], item[0]))
    return {
        "org": org,
        "team_key": team_key,
        "url": f"https://linear.app/{org}/team/{team_key}/overview",
        "mentions": mentions[(org, team_key)],
    }


def _split_linear_comment_texts(comments) -> tuple[list[str], list[str]]:
    weighted, plain = [], []
    for comment in comments or ():
        if not isinstance(comment, dict):
            continue
        body = comment.get("body")
        if not isinstance(body, str) or "linear.app/" not in body.lower():
            continue
        login = ((comment.get("user") or {}).get("login") or "").lower()
        if _LINEAR_LINKBACK_MARKER in body.lower() or "linear" in login:
            weighted.append(body)
        else:
            plain.append(body)
    return weighted, plain


async def discover_linear_team_from_github(repo: str, token: str | None = None, *, pr_bodies=None) -> dict | None:
    """Discover a linked Linear workspace/team from GitHub Linear App comments and PR bodies."""
    weighted, plain = [], [b for b in (pr_bodies or ()) if isinstance(b, str) and b]
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "github-flow-analytics/0.1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        status, _, payload = await asyncio.wait_for(
            github_api.request_json(
                f"https://api.github.com/repos/{repo}/issues/comments?sort=created&direction=desc&per_page=30",
                headers=headers,
                timeout=8,
            ),
            8,
        )
        if status == 200 and isinstance(payload, list):
            w, p = _split_linear_comment_texts(payload)
            weighted.extend(w)
            plain.extend(p)
    except (TimeoutError, httpx.HTTPError, ValueError, RuntimeError):
        pass
    return detect_linear_team_from_texts(plain, weighted_texts=weighted)


def discover_linear_team_from_github_sync(repo: str, *, pr_bodies=None, run=subprocess.run) -> dict | None:
    """Local `gh` CLI version of Linear workspace/team auto-discovery."""
    weighted, plain = [], [b for b in (pr_bodies or ()) if isinstance(b, str) and b]
    try:
        res = run(
            [
                "gh", "api", "--hostname", "github.com",
                f"repos/{repo}/issues/comments?sort=created&direction=desc&per_page=30",
            ],
            capture_output=True, text=True, encoding="utf-8", check=True, timeout=10,
        )
        payload = json.loads(res.stdout)
        if isinstance(payload, list):
            w, p = _split_linear_comment_texts(payload)
            weighted.extend(w)
            plain.extend(p)
    except Exception:
        pass
    return detect_linear_team_from_texts(plain, weighted_texts=weighted)


def _build_state_events(history_nodes: list[dict], has_next_page: bool) -> tuple[list[dict], list[dict]]:
    """Extract chronological Linear state transitions and GitHub-compatible open/close timeline events."""
    transitions = []
    timeline_events = []
    for node in history_nodes or []:
        if not isinstance(node, dict):
            continue
        created_at = node.get("createdAt")
        from_state = node.get("fromState") or {}
        to_state = node.get("toState") or {}
        from_name = from_state.get("name") if isinstance(from_state, dict) else None
        to_name = to_state.get("name") if isinstance(to_state, dict) else None
        from_type = from_state.get("type") if isinstance(from_state, dict) else None
        to_type = to_state.get("type") if isinstance(to_state, dict) else None
        if not created_at or (not from_name and not to_name):
            continue
        transitions.append({
            "createdAt": created_at,
            "fromState": from_name,
            "toState": to_name,
            "fromType": from_type,
            "toType": to_type,
        })
        if to_type in _CLOSED_STATE_TYPES and from_type not in _CLOSED_STATE_TYPES:
            timeline_events.append({"__typename": "ClosedEvent", "createdAt": created_at})
        elif from_type in _CLOSED_STATE_TYPES and to_type and to_type not in _CLOSED_STATE_TYPES:
            timeline_events.append({"__typename": "ReopenedEvent", "createdAt": created_at})

    transitions.sort(key=lambda item: item["createdAt"])
    timeline_events.sort(key=lambda item: item["createdAt"])
    return transitions, timeline_events


def _normalize_linear_issue(node: dict, repo: str | None) -> dict | None:
    if not isinstance(node, dict) or not node.get("identifier") or not node.get("createdAt"):
        return None
    state = node.get("state") or {}
    state_name = state.get("name") if isinstance(state, dict) else None
    state_type = state.get("type") if isinstance(state, dict) else None
    is_closed = state_type in _CLOSED_STATE_TYPES

    history = node.get("history") or {}
    has_next_page = bool((history.get("pageInfo") or {}).get("hasNextPage"))
    transitions, timeline_events = _build_state_events(history.get("nodes") or [], has_next_page)

    closed_at = None
    if is_closed:
        closed_at = node.get("completedAt") or node.get("canceledAt") or node.get("updatedAt")

    labels = [
        lbl["name"]
        for lbl in ((node.get("labels") or {}).get("nodes") or [])
        if isinstance(lbl, dict) and isinstance(lbl.get("name"), str)
    ]
    attachments = (node.get("attachments") or {}).get("nodes") or []
    pr_numbers = extract_linked_pr_numbers(attachments, node.get("description"), repo)

    record = {
        "number": node.get("number"),
        "identifier": node["identifier"],
        "title": node.get("title") or "",
        "body": node.get("description") or "",
        "createdAt": node["createdAt"],
        "updatedAt": node.get("updatedAt") or node["createdAt"],
        "closedAt": closed_at,
        "state": "CLOSED" if is_closed else "OPEN",
        "linearState": state_name,
        "linearStateType": state_type,
        "url": node.get("url"),
        "timelineItems": {
            "pageInfo": {"hasNextPage": has_next_page},
            "nodes": timeline_events,
        },
        "linearHistory": {
            "hasNextPage": has_next_page,
            "transitions": transitions,
        },
        "closedByPullRequestsReferences": {
            "totalCount": len(pr_numbers),
            "nodes": [{"number": number} for number in pr_numbers],
        },
    }
    if labels:
        record["labels"] = labels
    return record


async def _linear_graphql(query: str, variables: dict, api_key: str) -> dict:
    token = (api_key or "").strip()
    if not token:
        raise LinearError(
            "Linear API key is required. Add your Linear API key in Settings or set LINEAR_API_KEY.",
            status_code=400,
        )
    headers = {
        "Authorization": token,
        "Content-Type": "application/json",
        "User-Agent": "github-flow-analytics/0.1",
    }
    try:
        status, _, payload = await asyncio.wait_for(
            github_api.request_json(
                LINEAR_API_URL,
                method="POST",
                headers=headers,
                body={"query": query, "variables": variables},
                timeout=15,
            ),
            15,
        )
    except (TimeoutError, httpx.HTTPError, RuntimeError) as exc:
        raise LinearError("Linear could not be reached. Please try again.", status_code=502) from exc

    if status in (401, 403):
        raise LinearError("Linear rejected the API key. Check your Linear API key and try again.", status_code=401)
    if status == 429:
        raise LinearError("Linear rate limit reached. Please try again shortly.", status_code=429)
    if status != 200 or not isinstance(payload, dict):
        raise LinearError("Linear could not be reached. Please try again.", status_code=502)
    if payload.get("errors"):
        messages = " ".join(str(err.get("message", "")) for err in payload["errors"] if isinstance(err, dict)).lower()
        if "authentication" in messages or "unauthorized" in messages:
            raise LinearError("Linear rejected the API key. Check your Linear API key and try again.", status_code=401)
        raise LinearError("Linear returned an error while loading team issues.", status_code=502)
    data = payload.get("data")
    if not isinstance(data, dict):
        raise LinearError("Linear returned an unreadable response.", status_code=502)
    return data


async def collect_linear_issues(team_ref: str, api_key: str, now=None, *, repo: str | None = None) -> dict:
    """Fetch team metadata, workflow states, and bounded issues from Linear."""
    parsed = parse_linear_team(team_ref)
    now_dt = now or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    if now_dt.tzinfo is not None:
        now_dt = now_dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    window_start = now_dt - datetime.timedelta(weeks=ISSUE_WINDOW_WEEKS)

    team_data = await _linear_graphql(_TEAM_QUERY, {"key": parsed["team_key"]}, api_key)
    teams = ((team_data.get("teams") or {}).get("nodes")) or []
    selected = None
    for candidate in teams:
        if not isinstance(candidate, dict):
            continue
        org_url_key = ((candidate.get("organization") or {}).get("urlKey") or "").lower()
        if parsed["org"] and org_url_key != parsed["org"]:
            continue
        selected = candidate
        break
    if not selected:
        raise LinearError(
            f"Linear team '{parsed['team_key']}' was not found or is not accessible with this API key.",
            status_code=404,
        )

    ordered_states = order_workflow_states(((selected.get("states") or {}).get("nodes")) or [])
    org = selected.get("organization") or {}
    org_slug = org.get("urlKey") or parsed["org"] or ""
    team_meta = {
        "id": selected["id"],
        "key": selected["key"],
        "name": selected.get("name") or selected["key"],
        "org": org_slug,
        "org_name": org.get("name") or org_slug,
        "url": f"https://linear.app/{org_slug}/team/{selected['key']}/overview" if org_slug else None,
    }

    open_issues = []
    closed_issues = []
    cursor = None
    truncated = False
    for _ in range(LINEAR_ISSUE_PAGE_CAP):
        variables = {"teamId": selected["id"], "first": LINEAR_PAGE_SIZE, "after": cursor}
        page_data = await _linear_graphql(_TEAM_ISSUES_QUERY, variables, api_key)
        issue_conn = ((page_data.get("team") or {}).get("issues")) or {}
        nodes = issue_conn.get("nodes") or []
        for node in nodes:
            normalized = _normalize_linear_issue(node, repo)
            if normalized is None:
                continue
            if normalized["state"] == "CLOSED":
                closed_at = _parse_iso(normalized.get("closedAt"))
                if closed_at is None or closed_at >= window_start:
                    closed_issues.append(normalized)
            else:
                open_issues.append(normalized)
        page_info = issue_conn.get("pageInfo") or {}
        if not page_info.get("hasNextPage") or not page_info.get("endCursor"):
            truncated = False
            break
        cursor = page_info["endCursor"]
        truncated = True

    return {
        "source": "linear",
        "linear_team": team_meta,
        "linear_states": ordered_states,
        "open": open_issues,
        "closed": closed_issues,
        "pre_ai": [],
        "open_truncated": truncated,
        "closed_truncated": truncated,
        "open_available": True,
        "closed_available": True,
        "retry_collection": False,
    }


def collect_linear_issues_sync(team_ref: str, api_key: str, now=None, *, repo: str | None = None) -> dict:
    """Synchronous wrapper for local CLI and FastAPI execution."""
    return asyncio.run(collect_linear_issues(team_ref, api_key, now=now, repo=repo))
