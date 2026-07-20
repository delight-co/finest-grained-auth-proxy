"""Policy judgment for the langfuse CLI.

The langfuse CLI surface routed through fgap is ``api <resource> <verb>``
(plus ``api __schema`` introspection). Verbs map to two permission
classes, granted per credential entry in the config:

- ``read``: list, get (and ``api __schema``)
- ``write``: create, update, delete

Classification is an allowlist: an argv shape not recognized here is
denied, because an unknown verb may be a new write path — it must be
classified before it can pass.
"""

from fgap.plugins.base import match_resource

_READ_VERBS = frozenset({"list", "get"})
_WRITE_VERBS = frozenset({"create", "update", "delete"})

KNOWN_PERMISSIONS = frozenset({"read", "write"})


def _required_permission(args: list[str]) -> str | None:
    """Map argv to the permission it needs, or None if unrecognized."""
    if len(args) >= 2 and args[0] == "api":
        if args[1] == "__schema":
            return "read"
        if len(args) >= 3:
            verb = args[2]
            if verb in _READ_VERBS:
                return "read"
            if verb in _WRITE_VERBS:
                return "write"
    return None


def check_policy(args: list[str], resource: str, config: dict) -> str | None:
    """Return None to allow, or a deny reason.

    The grant is read from the credential entry that will serve this
    resource — permissions ride on the same first-match-wins ``resources``
    routing as the credential itself, so grant and key can never drift
    apart.
    """
    if any(a in ("-h", "--help") for a in args):
        return None  # help output only, no data access

    needed = _required_permission(args)
    if needed is None:
        shape = " ".join(args[:3])
        return (
            f"unrecognized langfuse command shape: '{shape}'. Allowed: "
            f"'api <resource> list|get' / 'api __schema' (read), "
            f"'api <resource> create|update|delete' (write)"
        )

    for cred in config.get("credentials", []):
        for pattern in cred.get("resources", []):
            if match_resource(pattern, resource):
                granted = cred.get("permissions", [])
                if needed in granted:
                    return None
                return (
                    f"'{needed}' permission is not granted for project "
                    f"'{resource}' (granted: {', '.join(granted) or 'none'})"
                )

    # No credential entry matches: allow here so the credential-selection
    # step can fail with its clearer "No credential for ..." message.
    return None
