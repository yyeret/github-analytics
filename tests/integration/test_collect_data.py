import json
import subprocess

import pytest

import analyze
from tests.factories import FakeGh, graphql_response, make_pr, small_raw_data


@pytest.fixture
def gh(monkeypatch):
    def install(responses):
        fake = FakeGh(responses)
        monkeypatch.setattr(subprocess, "run", fake)
        return fake
    return install


def _err():
    return subprocess.CalledProcessError(1, ["gh"], stderr="boom")


def test_run_graphql_query_returns_nodes_and_builds_command(gh):
    fake = gh([graphql_response([make_pr(1)])])
    nodes = analyze.run_graphql_query("repo:o/r is:pr", 7)
    assert [n["number"] for n in nodes] == [1]
    cmd = fake.calls[0]
    assert cmd[:3] == ["gh", "api", "graphql"]
    assert "searchQuery=repo:o/r is:pr" in cmd
    assert "first=7" in cmd


def test_run_graphql_query_returns_empty_on_cli_failure(gh):
    gh([_err()])
    assert analyze.run_graphql_query("q") == []


def test_run_graphql_query_returns_empty_on_error_payload(gh):
    gh([json.dumps({"errors": [{"message": "bad"}]})])
    assert analyze.run_graphql_query("q") == []


def test_collect_data_paginates_and_writes_cache(gh):
    fake = gh([
        graphql_response([make_pr(1)]),                                  # pre-AI sample
        graphql_response([make_pr(10), make_pr(11)], True, "CUR1"),      # merged page 1
        graphql_response([make_pr(12)], False, None),                    # merged page 2 (last)
        graphql_response([make_pr(20, merged=None)]),                    # open PRs
    ])
    data = analyze.collect_data("o/r")
    assert [p["number"] for p in data["pre_ai_prs"]] == [1]
    assert [p["number"] for p in data["recent_prs"]] == [10, 11, 12]
    assert [p["number"] for p in data["open_prs"]] == [20]
    assert data["repo"] == "o/r" and data["captured_at"]
    assert "cursor=CUR1" in fake.calls[2]          # second page carries the cursor
    assert "cursor=CUR1" not in " ".join(fake.calls[1])
    assert "merged:2021-06-01..2021-12-31" in " ".join(fake.calls[0])
    assert json.load(open(analyze.RAW_DATA_FILE)) == data


def test_collect_data_stops_after_six_pages(gh):
    pages = [graphql_response([make_pr(i)], True, f"C{i}") for i in range(1, 8)]
    fake = gh([graphql_response([])] + pages[:6] + [graphql_response([])])
    data = analyze.collect_data("o/r")
    assert len(data["recent_prs"]) == 6
    assert len(fake.calls) == 1 + 6 + 1


def test_collect_data_keeps_earlier_pages_when_a_page_fails(gh):
    gh([graphql_response([]), graphql_response([make_pr(10)], True, "C"), _err(), graphql_response([])])
    data = analyze.collect_data("o/r")
    assert [p["number"] for p in data["recent_prs"]] == [10]


def test_collect_data_tolerates_open_pr_failure(gh):
    gh([graphql_response([]), graphql_response([make_pr(10)]), _err()])
    assert analyze.collect_data("o/r")["open_prs"] == []


def test_load_or_collect_uses_cache_without_calling_gh():
    cached = small_raw_data()
    with open(analyze.RAW_DATA_FILE, "w") as f:
        json.dump(cached, f)
    assert analyze.load_or_collect("o/r") == cached  # subprocess is guarded: a gh call would fail


def test_load_or_collect_force_recollects(gh):
    with open(analyze.RAW_DATA_FILE, "w") as f:
        json.dump(small_raw_data("old/repo"), f)
    gh([graphql_response([]), graphql_response([make_pr(10)]), graphql_response([])])
    data = analyze.load_or_collect("new/repo", force=True)
    assert data["repo"] == "new/repo"


def test_load_or_collect_recollects_when_cache_is_corrupt(gh):
    with open(analyze.RAW_DATA_FILE, "w") as f:
        f.write("{not json")
    gh([graphql_response([]), graphql_response([]), graphql_response([])])
    assert analyze.load_or_collect("o/r")["repo"] == "o/r"
