import aiohttp

from fgap.core.http import get_session
from fgap.core.masking import mask_value
from fgap.plugins.base import Plugin

_GITHUB_API_URL = "https://api.github.com"


class GitHubPlugin(Plugin):
    """GitHub plugin: gh CLI execution and git smart HTTP proxy."""

    @property
    def name(self) -> str:
        return "github"

    @property
    def tools(self) -> list[str]:
        return ["gh"]

    def select_credential(self, resource: str, config: dict) -> dict | None:
        from .credential import select_credential

        return select_credential(resource, config)

    def get_routes(self, config: dict) -> list[tuple[str, str, callable]]:
        from .git_proxy import make_routes

        return make_routes(self.select_credential, config)

    def get_commands(self) -> dict[str, callable]:
        from .commands.discussion import execute as execute_discussion
        from .commands.issue import execute as execute_issue
        from .commands.sub_issue import execute as execute_sub_issue

        return {
            "issue": execute_issue,
            "discussion": execute_discussion,
            "sub-issue": execute_sub_issue,
        }

    async def health_check(
        self, config: dict, *, _api_url: str = _GITHUB_API_URL,
    ) -> list[dict]:
        """Check PAT validity via GitHub REST API.

        For each credential, calls GET /user to verify the token
        and reports scopes and rate limit.
        """
        results = []
        for cred in config.get("credentials", []):
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
