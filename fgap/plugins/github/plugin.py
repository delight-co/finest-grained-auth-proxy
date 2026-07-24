import aiohttp

from fgap.core.http import get_session
from fgap.core.masking import mask_value
from fgap.plugins.base import Plugin

_GITHUB_API_URL = "https://api.github.com"


class GitHubPlugin(Plugin):
    """GitHub plugin: gh CLI execution and git smart HTTP proxy."""

    def __init__(self):
        from .app_token import AppTokenStore

        self._app_tokens = AppTokenStore()

    @property
    def name(self) -> str:
        return "github"

    @property
    def tools(self) -> list[str]:
        return ["gh"]

    def select_credential(self, resource: str, config: dict) -> dict | None:
        from .credential import select_credential

        return select_credential(resource, config)

    async def resolve_credential_env(self, credential: dict,
                                     config: dict) -> dict | None:
        app_cred = credential.get("app")
        if app_cred is None:
            return credential.get("env")
        token = await self._app_tokens.get_token(
            app_cred, credential["resource"],
            api_base=config.get("_github_api_base_url", _GITHUB_API_URL))
        return {"GH_TOKEN": token, "GH_HOST": "github.com"}

    def get_routes(self, config: dict) -> list[tuple[str, str, callable]]:
        from .git_proxy import make_routes

        async def resolve_env(credential: dict) -> dict | None:
            return await self.resolve_credential_env(credential, config)

        return make_routes(self.select_credential, resolve_env, config)

    def get_commands(self) -> dict[str, callable]:
        from .commands.discussion import execute as execute_discussion
        from .commands.issue import execute as execute_issue
        from .commands.pr import execute as execute_pr
        from .commands.sub_issue import execute as execute_sub_issue

        return {
            "issue": execute_issue,
            "pr": execute_pr,
            "discussion": execute_discussion,
            "sub-issue": execute_sub_issue,
        }

    def check_policy(self, args: list[str], resource: str,
                     config: dict) -> str | None:
        from .policy import check_policy as _check_policy

        return _check_policy(args, resource, config)

    async def health_check(
        self, config: dict, *, _api_url: str = _GITHUB_API_URL,
    ) -> list[dict]:
        """Check credential validity via the GitHub REST API.

        Token credentials are probed with GET /user (login, scopes,
        rate limit). App credentials have no token to probe — GET /user
        cannot validate them — so they are probed with GET /app using
        the App JWT (App name/slug and granted permissions).
        """
        from .app_token import check_app

        results = []
        for cred in config.get("credentials", []):
            if "app_id" in cred:
                entry = {
                    "app_id": cred.get("app_id"),
                    "installation_id": cred.get("installation_id"),
                    "resources": cred.get("resources", []),
                }
                try:
                    entry.update(await check_app(cred, api_base=_api_url))
                except Exception as e:
                    entry.update({"valid": False, "error": str(e)})
            else:
                token = cred.get("token", "")
                entry = {
                    "masked_token": mask_value(token),
                    "resources": cred.get("resources", []),
                }
                try:
                    status = await _check_token(token, _api_url)
                    entry.update(status)
                except Exception as e:
                    entry.update({"valid": False, "error": str(e)})
            results.append(entry)
        return results


async def _check_token(token: str, api_url: str) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "fgap",
    }
    health_timeout = aiohttp.ClientTimeout(total=10)
    session = get_session()
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()
    try:
        async with session.get(
            f"{api_url}/user", headers=headers, timeout=health_timeout,
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                return {
                    "valid": True,
                    "user": data.get("login", ""),
                    "scopes": resp.headers.get("X-OAuth-Scopes", ""),
                    "rate_limit_remaining": resp.headers.get(
                        "X-RateLimit-Remaining", "",
                    ),
                }
            text = await resp.text()
            return {"valid": False, "error": f"HTTP {resp.status}: {text}"}
    finally:
        if own_session:
            await session.close()
