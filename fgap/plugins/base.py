import fnmatch
from abc import ABC, abstractmethod


def match_resource(pattern: str, resource: str) -> bool:
    """Check if resource pattern matches (case-insensitive).

    Patterns:
    - "*" matches all resources
    - "owner/*" matches all repos of that owner
    - "owner/repo" matches exactly
    - fnmatch patterns (e.g. "owner/repo-?") for advanced matching
    """
    p = pattern.lower()
    r = resource.lower()
    if p == "*":
        return True
    if p.endswith("/*"):
        return r.split("/")[0] == p[:-2]
    return fnmatch.fnmatch(r, p)


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

    async def resolve_credential_env(self, credential: dict,
                                     config: dict) -> dict | None:
        """Resolve a selected credential into env vars to inject.

        The default returns the credential's static ``env``. Plugins whose
        credentials need asynchronous work to become usable — e.g. minting
        a short-lived token from a long-lived key — override this.
        """
        return credential.get("env")

    def get_routes(self, config: dict) -> list[tuple[str, str, callable]]:
        """Return custom HTTP routes: [(method, path_pattern, handler), ...].

        Args:
            config: Plugin-specific config section.
        """
        return []

    def get_commands(self) -> dict[str, callable]:
        """Return custom commands: {command_name: execute_fn}.

        execute_fn signature:
            async (args: list[str], resource: str, credential: dict) -> dict | None

        Return None from execute_fn to fall through to CLI subprocess.
        """
        return {}

    def check_policy(self, args: list[str], resource: str,
                     config: dict) -> str | None:
        """Decide whether this invocation is allowed.

        Called by the router before credential selection. Responsibilities
        are split three ways: the plugin owns the judgment logic (how to
        read args for this service — permission grammar and granularity
        are service-specific), the config owns the concrete grants, and
        the router owns the choke point (a deny becomes HTTP 403 with the
        returned reason).

        Args:
            args: Full CLI argv as received from the client.
            resource: Resource identifier the client is targeting.
            config: Plugin-specific config section.

        Returns:
            None to allow, or a human-readable deny reason.
        """
        return None

    def validate_config(self, config: dict) -> None:
        """Validate this plugin's config section at startup.

        Called once at app creation for plugins that have a config
        section. Raise fgap.core.config.ConfigError on schema violations
        so the server fails fast instead of misbehaving at request time.

        The default accepts anything (backward compatibility). Plugins
        that define a strict schema override this; strict schemas treat
        everything not explicitly optional as required, and reject unknown
        keys — a config that is missing something or contains something
        unrecognized is wrong either way.
        """

    async def health_check(self, config: dict) -> list[dict]:
        """Check credential health. Returns list of status dicts."""
        return []
