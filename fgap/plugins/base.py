from abc import ABC, abstractmethod


class Plugin(ABC):
    """Base class for tool plugins."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Plugin identifier. e.g. 'github', 'google'."""
        ...

    @property
    @abstractmethod
    def tools(self) -> list[str]:
        """CLI binaries this plugin handles. e.g. ['gh', 'git']."""
        ...

    @abstractmethod
    def select_credential(self, resource: str, config: dict) -> dict | None:
        """Select credential for the given resource.

        Args:
            resource: Resource identifier (e.g. 'owner/repo' for GitHub).
            config: Plugin-specific config section.

        Returns:
            Credential dict with 'env' key (env vars to inject into subprocess),
            or None if no credential matches.
        """
        ...

    def get_routes(self) -> list[tuple[str, str, callable]]:
        """Return custom HTTP routes: [(method, path_pattern, handler), ...]."""
        return []

    def get_commands(self) -> dict[str, callable]:
        """Return custom commands: {command_name: execute_fn}.

        execute_fn signature:
            async (args: list[str], resource: str, credential: dict) -> dict | None

        Return None from execute_fn to fall through to CLI subprocess.
        """
        return {}

    async def health_check(self, config: dict) -> list[dict]:
        """Check credential health. Returns list of status dicts."""
        return []
