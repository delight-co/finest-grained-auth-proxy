import aiohttp

from fgap.core.http import get_session
from fgap.core.masking import mask_value
from fgap.plugins.base import Plugin

_DEFAULT_HOST = "https://cloud.langfuse.com"


class LangfusePlugin(Plugin):
    """Langfuse plugin: langfuse CLI execution with credential injection."""

    @property
    def name(self) -> str:
        return "langfuse"

    @property
    def tools(self) -> list[str]:
        return ["langfuse"]

    def select_credential(self, resource: str, config: dict) -> dict | None:
        from .credential import select_credential

        return select_credential(resource, config)

    async def health_check(
        self, config: dict, *, _api_url: str | None = None,
    ) -> list[dict]:
        """Check credential validity via Langfuse REST API.

        Calls GET /api/public/projects to verify the key pair.
        """
        results = []
        for cred in config.get("credentials", []):
            host = cred.get("host", _DEFAULT_HOST)
            entry = {
                "masked_public_key": mask_value(cred.get("public_key", "")),
                "host": host,
                "resources": cred.get("resources", []),
            }
            try:
                api_url = _api_url or host
                status = await _check_credentials(
                    cred.get("public_key", ""),
                    cred.get("secret_key", ""),
                    api_url,
                )
                entry.update(status)
            except Exception as e:
                entry.update({"valid": False, "error": str(e)})
            results.append(entry)
        return results


async def _check_credentials(
    public_key: str, secret_key: str, api_url: str,
) -> dict:
    """Verify credentials via GET /api/public/projects with Basic Auth."""
    auth = aiohttp.BasicAuth(public_key, secret_key)
    health_timeout = aiohttp.ClientTimeout(total=10)
    session = get_session()
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()
    try:
        async with session.get(
            f"{api_url}/api/public/projects",
            auth=auth,
            timeout=health_timeout,
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                project_name = data.get("data", [{}])[0].get("name", "")
                return {"valid": True, "project": project_name}
            text = await resp.text()
            return {"valid": False, "error": f"HTTP {resp.status}: {text}"}
    finally:
        if own_session:
            await session.close()
