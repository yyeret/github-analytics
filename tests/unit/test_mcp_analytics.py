import asyncio

import pytest

import mcp_analytics


def test_failed_dashboard_search_cancels_and_awaits_siblings(monkeypatch):
    async def exercise():
        started, cleaned_up = set(), set()
        all_started = asyncio.Event()
        failing_query = 'repo:o/r is:pr is:merged sort:updated-desc'

        async def request(url, **kwargs):
            query = kwargs['body']['variables']['searchQuery']
            started.add(query)
            if len(started) == 6:
                all_started.set()
            if query == failing_query:
                await all_started.wait()
                return 200, {}, {'data': {'search': {'nodes': []}}}
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned_up.add(query)

        monkeypatch.setattr(mcp_analytics, 'request_json', request)
        async with asyncio.timeout(1):
            with pytest.raises(ValueError, match='GitHub search failed or returned an invalid response'):
                await mcp_analytics.collect_dashboard_data('o/r', 'caller', False, 1)
        assert cleaned_up == started - {failing_query}

    asyncio.run(exercise())
