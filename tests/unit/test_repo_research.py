import asyncio
import base64
import json

import pytest

from repo_research import build_research_sources


class ResearchServices:
    def __init__(self, paths=None, tree=None):
        self.paths = paths if paths is not None else ["docs/strategy.md"]
        self.tree = tree if tree is not None else [
            {"path": "docs/strategy.md", "type": "blob"},
            {"path": "README.md", "type": "blob"},
            {"path": "src/private.py", "type": "blob"},
        ]
        self.calls, self.prompts = [], []
        self.failures = {}
        self.document = "Users need secure, reliable exports."

    async def request(self, url, timeout, **kwargs):
        self.calls.append((url, timeout, kwargs))
        for fragment, failure in self.failures.items():
            if fragment in url:
                if isinstance(failure, Exception):
                    raise failure
                return failure, {}, {"error": "secret-sentinel"}
        if "/git/trees/" in url:
            return 200, {}, {"tree": self.tree, "truncated": False}
        if "/releases?" in url:
            return 200, {}, [{"name": "Reliable exports", "body": "Adds resumable exports.", "tag_name": "v2", "published_at": "2026-10-25"}]
        if "/issues?" in url:
            return 200, {}, [
                {"number": 7, "title": "Export failures", "body": "Large exports fail.", "comments": 20},
                {"number": 8, "title": "Not an issue", "body": "PR body", "pull_request": {}},
            ]
        if "/contents/" in url:
            return 200, {}, {"encoding": "base64", "content": base64.b64encode(self.document.encode()).decode()}
        raise AssertionError(url)

    async def generate(self, prompt, schema, max_tokens, **kwargs):
        self.prompts.append((prompt, schema, max_tokens, kwargs))
        return {"paths": self.paths}


def run(services, **metadata):
    return asyncio.run(build_research_sources(
        "owner/product", {"description": "An export tool", "topics": ["data"], "homepage": "https://product.test", "readme": "# Product", **metadata},
        "never-send-this-token", services.request, services.generate, {"request_id": "study"}))


def test_research_selects_catalog_documents_and_includes_public_signals():
    service = ResearchServices()
    result = run(service)
    assert {source["kind"] for source in result["sources"]} == {"metadata", "readme", "document", "release", "issue"}
    assert any(source["path"] == "docs/strategy.md" and "secure" in source["text"] for source in result["sources"])
    assert not any("Not an issue" in source["text"] for source in result["sources"])
    assert len({source["id"] for source in result["sources"]}) == len(result["sources"])
    assert all(timeout <= 8 and "Authorization" not in kwargs["headers"] for _, timeout, kwargs in service.calls)
    assert all(url.startswith("https://api.github.com/repos/owner/product/") for url, _, _ in service.calls)
    prompt = service.prompts[0][0].lower()
    assert all(word in prompt for word in ("users", "workflows", "adoption", "strategic", "security", "resilience", "productivity"))


def test_model_paths_are_restricted_to_the_catalog_and_five_documents():
    paths = [f"docs/guide-{i}.md" for i in range(10)]
    service = ResearchServices(paths=["https://evil.test/steal", "src/private.py", "../secret.md", *paths],
        tree=[{"path": path, "type": "blob"} for path in paths] + [{"path": "src/private.py", "type": "blob"}])
    result = run(service)
    documents = [source for source in result["sources"] if source["kind"] == "document"]
    assert len(documents) == 5
    assert {source["path"] for source in documents}.issubset(paths)
    assert len([url for url, _, _ in service.calls if "/contents/" in url]) == 5
    assert result["limitations"]


def test_catalog_and_source_budgets_are_enforced_with_truncation_visible():
    paths = [f"docs/guide-{i:03}.md" for i in range(300)]
    service = ResearchServices(paths=paths[:5], tree=[{"path": path, "type": "blob"} for path in paths])
    service.document = "x" * 20_000
    result = run(service, readme="r" * 20_000)
    prompt = service.prompts[0][0]
    assert sum(path in prompt for path in paths) <= 120
    assert sum(len(source["text"]) for source in result["sources"]) <= 36_000
    assert all(len(source["text"]) <= 4_000 and source["truncated"] for source in result["sources"] if source["kind"] == "document")
    readme = next(source for source in result["sources"] if source["kind"] == "readme")
    assert len(readme["text"]) == 12_000 and readme["truncated"]


@pytest.mark.parametrize("failure", [403, 429, TimeoutError("secret-sentinel")])
def test_optional_failures_preserve_readme_and_report_unavailable_not_absent(failure):
    service = ResearchServices()
    service.failures = {"/git/trees/": failure, "/issues?": failure, "/releases?": failure}
    result = run(service)
    assert any(source["kind"] == "readme" for source in result["sources"])
    assert len(result["limitations"]) >= 3
    assert "secret-sentinel" not in json.dumps(result)
    assert "never-send-this-token" not in json.dumps(result)
    assert not service.prompts


def test_selection_provider_failure_keeps_other_evidence():
    service = ResearchServices()

    async def failed(*args, **kwargs):
        raise ValueError("provider-secret-sentinel")

    service.generate = failed
    result = run(service)
    assert any(source["kind"] == "issue" for source in result["sources"])
    assert not any(source["kind"] == "document" for source in result["sources"])
    assert result["limitations"] and "provider-secret-sentinel" not in json.dumps(result)


@pytest.mark.parametrize("slow_stage", ["catalog", "planner", "documents"])
def test_optional_research_budget_preserves_available_sources(monkeypatch, slow_stage):
    import repo_research

    service = ResearchServices()
    request, generate = service.request, service.generate
    monkeypatch.setattr(repo_research, "RESEARCH_TIMEOUT", .02, raising=False)

    async def delayed_request(url, timeout, **kwargs):
        if slow_stage == "catalog" or (slow_stage == "documents" and "/contents/" in url):
            await asyncio.sleep(1)
        return await request(url, timeout, **kwargs)

    async def delayed_generate(*args, **kwargs):
        if slow_stage == "planner":
            await asyncio.sleep(1)
        return await generate(*args, **kwargs)

    async def bounded():
        return await asyncio.wait_for(build_research_sources(
            "owner/product", {"description": "Exports", "readme": "Existing product evidence"},
            None, delayed_request, delayed_generate), .2)

    result = asyncio.run(bounded())
    assert {"metadata", "readme"}.issubset({source["kind"] for source in result["sources"]})
    assert not any(source["kind"] == "document" for source in result["sources"])
    assert result["limitations"]


def test_authorized_private_research_uses_only_caller_token():
    service = ResearchServices()
    result = asyncio.run(build_research_sources('owner/private',
        {'private': True, 'readme': 'Private strategy'}, 'caller', service.request, service.generate,
        allow_private=True))
    assert result['sources']
    assert all(kwargs['headers'].get('Authorization') == 'Bearer caller' for _, _, kwargs in service.calls)
    refused = ResearchServices()
    with pytest.raises(ValueError):
        asyncio.run(build_research_sources('owner/private', {'private': True}, 'caller',
            refused.request, refused.generate))
    assert refused.calls == []


def test_owner_sources_total_budget_and_duplicates():
    service = ResearchServices()
    owner = [{'source_id': f's{i}', 'text': 'x' * 5000} for i in range(8)]
    result = asyncio.run(build_research_sources('owner/public', {}, None, service.request,
        service.generate, owner_context=owner))
    owner_sources = [source for source in result['sources'] if source['kind'] == 'owner_context']
    assert sum(len(source['text']) for source in owner_sources) == 12000
    assert all(len(source['text']) <= 4000 and source['truncated'] for source in owner_sources)
    assert result['limitations']
    for invalid in (owner + [owner[0]], [owner[0], owner[0]], [{'source_id': '../bad', 'text': 'x'}]):
        with pytest.raises(ValueError):
            asyncio.run(build_research_sources('owner/public', {}, None, service.request,
                service.generate, owner_context=invalid))
