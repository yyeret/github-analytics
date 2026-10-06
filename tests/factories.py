"""Builders for GraphQL-shaped PR nodes with explicit timestamps."""
import json
import subprocess
import types


def make_commit(date, name="Dev", email="dev@example.com", login="dev"):
    return {"commit": {"committedDate": date, "authors": {"nodes": [{"name": name, "email": email, "user": {"login": login} if login else None}]}}}


def make_review(date, state="APPROVED", login="rev"):
    return {"createdAt": date, "state": state, "author": {"login": login}}


def make_issue(number=1, created="2024-01-01T00:00:00Z", title="issue", closed=None):
    return {"number": number, "createdAt": created, "title": title, "closedAt": closed}


def make_pr(number=1, created="2024-01-10T00:00:00Z", merged="2024-01-11T00:00:00Z", additions=10, deletions=5,
            title="Fix thing", login="alice", typename="User", commits=None, reviews=None, issues=None, ready_for_review=None):
    """Build a PR node. `author=None` is expressed via login=None."""
    return {
        "number": number,
        "title": title,
        "createdAt": created,
        "mergedAt": merged,
        "closedAt": merged,
        "additions": additions,
        "deletions": deletions,
        "author": {"login": login, "__typename": typename} if login is not None else None,
        "closingIssuesReferences": {"nodes": issues or []},
        "commits": {"totalCount": len(commits or []), "nodes": commits or []},
        "reviews": {"totalCount": len(reviews or []), "nodes": reviews or []},
        "timelineItems": {"nodes": [{"createdAt": ready_for_review}] if ready_for_review else []},
    }


def graphql_response(nodes, has_next=False, cursor=None):
    return json.dumps({"data": {"search": {"pageInfo": {"hasNextPage": has_next, "endCursor": cursor}, "nodes": nodes}}})


class FakeGh:
    """Queue of canned `gh` results; records every command line."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, cmd, *args, **kwargs):
        self.calls.append(cmd)
        if not self.responses:
            raise AssertionError(f"unexpected extra gh call: {cmd}")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return subprocess.CompletedProcess(cmd, 0, stdout=item, stderr="")


def small_raw_data(repo="o/r"):
    """Raw cache payload: 1 pre-AI PR, 4 merged PRs (human, assisted, agentic), 2 open PRs."""
    fast = [make_commit("2024-01-03T00:00:00Z"), make_commit("2024-01-03T00:01:00Z"), make_commit("2024-01-03T00:02:00Z")]
    return {
        "repo": repo,
        "captured_at": "2024-03-01T00:00:00",
        "pre_ai_prs": [make_pr(1, "2021-07-01T00:00:00Z", "2021-07-03T00:00:00Z", additions=20, deletions=5)],
        "recent_prs": [
            make_pr(101, "2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z", additions=10, ready_for_review="2024-01-01T06:00:00Z",
                    reviews=[make_review("2024-01-01T12:00:00Z")], issues=[make_issue(5, "2023-12-20T00:00:00Z")],
                    commits=[make_commit("2023-12-31T20:00:00Z")]),
            make_pr(102, "2024-01-03T00:00:00Z", "2024-01-05T00:00:00Z", additions=60, deletions=40, commits=fast),
            make_pr(103, "2024-01-08T00:00:00Z", "2024-01-08T12:00:00Z", additions=5, login="dependabot"),
            make_pr(104, "2024-01-09T00:00:00Z", "2024-01-12T00:00:00Z", additions=200, deletions=50, reviews=[make_review("2024-01-10T00:00:00Z", "CHANGES_REQUESTED")]),
        ],
        "open_prs": [
            make_pr(201, "2024-02-20T00:00:00Z", None, title="[draft] wip"),
            make_pr(202, "2024-02-25T00:00:00Z", None, reviews=[make_review("2024-02-26T00:00:00Z", "APPROVED")]),
        ],
    }


class FakeGenai(types.ModuleType):
    """Stand-in for google.generativeai; records configuration and chat traffic."""

    def __init__(self, reply="coach says hi", error=None):
        super().__init__("google.generativeai")
        self.reply, self.error = reply, error
        self.api_key = self.model_kwargs = self.history = self.sent = None
        outer = self

        class _Resp:
            text = reply

        class _Chat:
            def send_message(self, message):
                if outer.error:
                    raise outer.error
                outer.sent = message
                return _Resp()

        class GenerativeModel:
            def __init__(self, **kwargs):
                outer.model_kwargs = kwargs

            def start_chat(self, history):
                outer.history = history
                return _Chat()

        self.GenerativeModel = GenerativeModel

    def configure(self, api_key):
        self.api_key = api_key
