from workers import asgi

from cloudflare_app import app
from github_auth import cleanup_expired_credentials


class Default(asgi.entrypoint(app)):
    async def scheduled(self, controller, env, ctx):
        await cleanup_expired_credentials(self.env.USERS_DB)
