"""Local ASGI version of the hosted API, using caller GitHub bearer credentials.

Run: uvicorn local_private_api:application --host 127.0.0.1 --port 8001
OAuth browser sessions require the hosted D1 binding; bearer API calls do not.
"""
import os
import time
from types import SimpleNamespace

from cloudflare_app import app


class LocalLimit:
    def __init__(self, maximum):
        self.maximum = maximum
        self.requests = {}

    async def limit(self, entry):
        now = time.monotonic()
        key = entry['key']
        recent = [stamp for stamp in self.requests.get(key, []) if stamp > now - 60]
        success = len(recent) < self.maximum
        if success:
            recent.append(now)
        self.requests[key] = recent
        return {'success': success}


def create_application():
    env = SimpleNamespace(AUTH_REQUIRED='true', ANALYZE_LIMIT=LocalLimit(10), CHAT_LIMIT=LocalLimit(20),
        GEMINI_API_KEY=os.getenv('GEMINI_API_KEY') or os.getenv('GEMINI_DEVELOPER_API_KEY'),
        TYPESAFE_API_KEY=os.getenv('TYPESAFE_API_KEY') or os.getenv('JEV_API_KEY') or os.getenv('typesafe_api_token'))
    async def application(scope, receive, send):
        scope['env'] = env
        await app(scope, receive, send)
    return application


application = create_application()
