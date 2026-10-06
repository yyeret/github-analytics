import pytest

from analyze import classify_pr
from tests.factories import make_commit, make_pr


def _commits_at(*stamps):
    return [make_commit(s) for s in stamps]


def test_plain_human_pr():
    assert classify_pr(make_pr()) == "Human"


def test_bot_typename_is_agentic():
    assert classify_pr(make_pr(login="some-app", typename="Bot")) == "AI-Agentic"


@pytest.mark.parametrize("login", ["coderabbitai", "Sweep-Bot", "devin-ai", "github-actions", "dependabot", "kapa-ai", "mobb-fix", "DEPENDABOT"])
def test_known_bot_logins_are_agentic(login):
    assert classify_pr(make_pr(login=login)) == "AI-Agentic"


@pytest.mark.parametrize("title", ["CodeRabbit suggestions", "sweep-ai: fix", "Devin did this"])
def test_agent_signature_in_title_is_agentic(title):
    assert classify_pr(make_pr(title=title)) == "AI-Agentic"


@pytest.mark.parametrize("field", ["email", "name", "login"])
@pytest.mark.parametrize("term", ["copilot", "aider", "claude", "gpt"])
def test_ai_coauthor_signature_is_assisted(field, term):
    kwargs = {"name": "Dev", "email": "dev@example.com", "login": "dev"}
    kwargs[field] = f"x-{term.upper()}-y@example.com" if field == "email" else f"x-{term}-y"
    pr = make_pr(commits=[make_commit("2024-01-01T00:00:00Z", **kwargs)])
    assert classify_pr(pr) == "AI-Assisted"


def test_rapid_commit_burst_is_assisted():
    # gaps of 60s and 60s -> two quick commits
    pr = make_pr(commits=_commits_at("2024-01-01T00:00:00Z", "2024-01-01T00:01:00Z", "2024-01-01T00:02:00Z"))
    assert classify_pr(pr) == "AI-Assisted"


def test_gap_of_exactly_120s_is_not_quick():
    pr = make_pr(commits=_commits_at("2024-01-01T00:00:00Z", "2024-01-01T00:02:00Z", "2024-01-01T00:04:00Z"))
    assert classify_pr(pr) == "Human"


def test_only_one_quick_gap_is_human():
    pr = make_pr(commits=_commits_at("2024-01-01T00:00:00Z", "2024-01-01T00:01:00Z", "2024-01-01T05:00:00Z"))
    assert classify_pr(pr) == "Human"


def test_fewer_than_three_commits_never_use_velocity_heuristic():
    pr = make_pr(commits=_commits_at("2024-01-01T00:00:00Z", "2024-01-01T00:00:30Z"))
    assert classify_pr(pr) == "Human"


def test_commits_unsorted_are_sorted_before_gap_check():
    pr = make_pr(commits=_commits_at("2024-01-01T00:02:00Z", "2024-01-01T00:00:00Z", "2024-01-01T00:01:00Z"))
    assert classify_pr(pr) == "AI-Assisted"


def test_missing_author_is_human_without_error():
    assert classify_pr(make_pr(login=None)) == "Human"


def test_agentic_takes_precedence_over_assisted():
    pr = make_pr(login="dependabot", commits=[make_commit("2024-01-01T00:00:00Z", name="Claude")])
    assert classify_pr(pr) == "AI-Agentic"


def test_commit_author_without_user_is_handled():
    pr = make_pr(commits=[make_commit("2024-01-01T00:00:00Z", login=None)])
    assert classify_pr(pr) == "Human"
