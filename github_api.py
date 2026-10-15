"""GitHub JSON requests using Workers fetch in production and httpx locally."""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

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
        from js import Object, fetch as js_fetch
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
        options: dict[str, Any] = {"method": method, "headers": worker_headers}
        if body is not None:
            worker_headers.setdefault("Content-Type", "application/json")
            options["body"] = json.dumps(body, separators=(",", ":"))
        response = await js_fetch(url, to_js(options, dict_converter=Object.fromEntries))
        status = response.status
        response_headers = {
            name: response.headers.get(name, "")
            for name in ("content-type", "server", "cf-ray", "x-github-request-id", "x-ratelimit-remaining")
        }
        text = await response.text()

    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        payload = {"message": text[:1000]}
    if status >= 400:
        logger.error("GitHub API returned HTTP %s for %s %s; response_headers=%s; body=%s",
                     status, method, url, response_headers, text[:300])
    return status, response_headers, payload
