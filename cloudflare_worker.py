from workers import asgi

from cloudflare_app import app
from github_auth import cleanup_expired_credentials
from mcp_oauth import cleanup_mcp_oauth


class Default(asgi.entrypoint(app)):
    async def scheduled(self, controller, env, ctx):
        await cleanup_expired_credentials(self.env.USERS_DB)
        await cleanup_mcp_oauth(self.env.USERS_DB)
