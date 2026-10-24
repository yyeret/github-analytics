"""GitHub JSON requests using Workers fetch in production and httpx locally."""

import asyncio
import json
import logging
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

def log_event(level, event, **fields):
    record = {"event": event, **fields}
    try:
        from js import Object, console
        from pyodide.ffi import to_js
    except (ImportError, ModuleNotFoundError):
        logger.log(level, json.dumps(record, separators=(",", ":")))
    else:
        emit = console.error if level >= logging.ERROR else console.warn if level >= logging.WARNING else console.log
        emit(to_js(record, dict_converter=Object.fromEntries))


async def request_json(
    url: str,
    *,
    headers: dict[str, str],
    method: str = "GET",
    body: dict[str, Any] | None = None,
    timeout: float = 15,
) -> tuple[int, dict[str, str], Any]:
    """Return status, lowercase response headers, and decoded JSON.

    Cloudflare Python Workers currently drops the User-Agent header when
    requests are sent through httpx. Its native JavaScript ``fetch`` API preserves
    request headers. A CPython/httpx fallback keeps local development simple.
    """
    try:
        from js import AbortController, Object, fetch as js_fetch
        from pyodide.ffi import to_js
    except (ImportError, ModuleNotFoundError):
        import httpx

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(method, url, headers=headers, json=body)
        status = response.status_code
        response_headers = {name.lower(): value for name, value in response.headers.items()}
        text = response.text
    else:
        worker_headers = dict(headers)
        controller = AbortController.new()
        options: dict[str, Any] = {"method": method, "headers": worker_headers, "signal": controller.signal}
        if body is not None:
            worker_headers.setdefault("Content-Type", "application/json")
            options["body"] = json.dumps(body, separators=(",", ":"))
        try:
            response = await js_fetch(url, to_js(options, dict_converter=Object.fromEntries))
            status = response.status
            response_headers = {
                name: response.headers.get(name, "")
                for name in ("content-type", "server", "cf-ray", "x-github-request-id", "x-ratelimit-remaining")
            }
            text = await response.text()
        except asyncio.CancelledError:
            controller.abort()
            raise


    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        payload = {"message": text[:1000]}
    if status >= 400:
        host = urlsplit(url).hostname
        provider = {"api.github.com": "github", "generativelanguage.googleapis.com": "gemini"}.get(host, "other")
        log_event(logging.ERROR, "upstream_http_error", provider=provider, http_status=status)
    return status, response_headers, payload
