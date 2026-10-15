import json

import pytest

from tests.factories import small_raw_data


def cacheable_raw(repo):
    data = small_raw_data(repo)
    data["issues"] = {"open": [], "closed": [], "pre_ai": []}
    return data


def test_cache_keeps_repositories_separate_across_instances(tmp_path):
    from repository_cache import RepositoryCache

    cache = RepositoryCache(tmp_path)
    alpha = cacheable_raw("owner/alpha")
    beta = cacheable_raw("owner/beta")
    cache.save("owner/alpha", alpha)
    cache.save("owner/beta", beta)

    reopened = RepositoryCache(tmp_path)
    assert reopened.load("owner/alpha") == alpha
    assert reopened.load("owner/beta") == beta
    assert reopened.load("other/alpha") is None


def test_cache_rejects_corrupt_or_mismatched_data(tmp_path):
    from repository_cache import RepositoryCache

    cache = RepositoryCache(tmp_path)
    path = cache.path_for("owner/alpha")
    path.parent.mkdir(parents=True)
    path.write_text("not json")
    assert cache.load("owner/alpha") is None

    path.write_text(json.dumps(cacheable_raw("owner/beta")))
    assert cache.load("owner/alpha") is None

    incomplete = cacheable_raw("owner/alpha")
    del incomplete["recent_prs"]
    path.write_text(json.dumps(incomplete))
    assert cache.load("owner/alpha") is None


def test_cache_rejects_unsafe_identity(tmp_path):
    from repository_cache import RepositoryCache

    cache = RepositoryCache(tmp_path)
    with pytest.raises(ValueError):
        cache.path_for("../outside")
    with pytest.raises(ValueError):
        cache.path_for("owner/../../outside")


def test_failed_replacement_keeps_previous_entry(tmp_path, monkeypatch):
    import repository_cache

    cache = repository_cache.RepositoryCache(tmp_path)
    old = cacheable_raw("owner/alpha")
    cache.save("owner/alpha", old)

    def fail_replace(*args):
        raise OSError("disk error")

    monkeypatch.setattr(repository_cache.os, "replace", fail_replace)
    with pytest.raises(OSError):
        cache.save("owner/alpha", cacheable_raw("owner/alpha"))
    assert cache.load("owner/alpha") == old
