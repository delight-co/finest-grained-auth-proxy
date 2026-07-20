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

    def check_policy(self, args: list[str], resource: str,
                     config: dict) -> str | None:
        from .policy import check_policy

        return check_policy(args, resource, config)

    def validate_config(self, config: dict) -> None:
        """Strict schema: Langfuse credentials carry permissions.

        Langfuse API keys are per-project (organization-scoped keys only
        cover management routes, not trace data), so each entry is one
        project's key pair plus the permissions granted through it.
        ``permissions`` is required — an entry without an explicit grant
        is an error, not an implicit full grant.
        """
        from fgap.core.config import ConfigError, check_keys

        from .policy import KNOWN_PERMISSIONS

        check_keys(
            config, required={"credentials"}, context="plugins.langfuse",
        )
        for i, cred in enumerate(config["credentials"]):
            ctx = f"plugins.langfuse credential {i}"
            check_keys(
                cred,
                required={"public_key", "secret_key", "resources",
                          "permissions"},
                optional={"host"},
                context=ctx,
            )
            resources = cred["resources"]
            if not isinstance(resources, list) or not resources:
                raise ConfigError(
                    f"{ctx}: 'resources' must be a non-empty array"
                )
            permissions = cred["permissions"]
            if not isinstance(permissions, list) or not permissions:
                raise ConfigError(
                    f"{ctx}: 'permissions' must be a non-empty array"
                )
            unknown = set(permissions) - KNOWN_PERMISSIONS
            if unknown:
                raise ConfigError(
                    f"{ctx}: unknown permission(s): "
                    f"{', '.join(sorted(unknown))} "
                    f"(known: {', '.join(sorted(KNOWN_PERMISSIONS))})"
                )

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
                "permissions": cred.get("permissions", []),
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
