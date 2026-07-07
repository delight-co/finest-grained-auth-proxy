import aiohttp

from fgap.core.http import get_session
from fgap.core.masking import mask_value
from fgap.plugins.base import Plugin
from fgap.plugins.fly.commands import mint_command

_FLY_GRAPHQL_URL = "https://api.fly.io/graphql"


class FlyPlugin(Plugin):
    """Fly.io plugin: flyctl execution with token injection.

    The resource is the Fly app name, so credentials can be scoped down
    to a single app. Commands that need the caller's working directory
    or a long-lived connection (deploy, logs, ssh, ...) cannot run
    through the /cli round-trip; the client uses the ``mint`` custom
    command to obtain a short-lived app-scoped deploy token and runs its
    local flyctl with that instead (see fgap.client.fly).
    """

    @property
    def name(self) -> str:
        return "fly"

    @property
    def tools(self) -> list[str]:
        return ["fly", "flyctl"]

    def select_credential(self, resource: str, config: dict) -> dict | None:
        from .credential import select_credential

        return select_credential(resource, config)

    def get_commands(self) -> dict[str, callable]:
        return {"mint": mint_command}

    async def health_check(
        self, config: dict, *, _api_url: str = _FLY_GRAPHQL_URL,
    ) -> list[dict]:
        """Check token validity via the Fly GraphQL API.

        A 200 on a ``viewer`` query means the API accepted the token.
        Scoped tokens (org/deploy macaroons) may not expose viewer
        fields — the entry then reports valid with an empty email rather
        than failing.
        """
        results = []
        for cred in config.get("credentials", []):
            token = cred.get("token", "")
            entry = {
                "masked_token": mask_value(token),
                "resources": cred.get("resources", []),
            }
            try:
                entry.update(await _check_token(token, _api_url))
            except Exception as e:
                entry.update({"valid": False, "error": str(e)})
            results.append(entry)
        return results


async def _check_token(token: str, api_url: str) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "fgap",
    }
    health_timeout = aiohttp.ClientTimeout(total=10)
    session = get_session()
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()
    try:
        async with session.post(
            api_url,
            headers=headers,
            json={"query": "query { viewer { email } }"},
            timeout=health_timeout,
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                # GraphQL reports auth failures as HTTP 200 + errors array
                errors = data.get("errors") or []
                if errors:
                    msg = errors[0].get("message", "GraphQL error")
                    return {"valid": False, "error": msg}
                viewer = (data.get("data") or {}).get("viewer") or {}
                return {"valid": True, "email": viewer.get("email", "")}
            text = await resp.text()
            return {"valid": False, "error": f"HTTP {resp.status}: {text}"}
    finally:
        if own_session:
            await session.close()
