import aiohttp

from fgap.core.http import get_session
from fgap.core.masking import mask_value
from fgap.plugins.base import Plugin

_NOTION_API_URL = "https://api.notion.com"


class NotionPlugin(Plugin):
    """Notion plugin: notion CLI execution with token injection."""

    @property
    def name(self) -> str:
        return "notion"

    @property
    def tools(self) -> list[str]:
        return ["notion"]

    def select_credential(self, resource: str, config: dict) -> dict | None:
        from .credential import select_credential

        return select_credential(resource, config)

    async def health_check(
        self, config: dict, *, _api_url: str = _NOTION_API_URL,
    ) -> list[dict]:
        """Check token validity via Notion REST API.

        Calls GET /v1/users/me to verify the integration token.
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
        "Notion-Version": "2022-06-28",
        "User-Agent": "fgap",
    }
    health_timeout = aiohttp.ClientTimeout(total=10)
    session = get_session()
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()
    try:
        async with session.get(
            f"{api_url}/v1/users/me", headers=headers, timeout=health_timeout,
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                return {
                    "valid": True,
                    "bot_name": data.get("name", ""),
                    "type": data.get("type", ""),
                }
            text = await resp.text()
            return {"valid": False, "error": f"HTTP {resp.status}: {text}"}
    finally:
        if own_session:
            await session.close()
