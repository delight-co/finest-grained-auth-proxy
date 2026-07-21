"""Server-side policy for the gh CLI.

The capability model must hold even when a caller bypasses the fgap-gh
client and POSTs ``/cli`` directly. ``gh auth`` subcommands exfiltrate
the injected credential, so they are denied here at the choke point —
the router turns a non-None return into HTTP 403.

This mirrors the structure of :mod:`fgap.plugins.aws.policy`, giving the
GitHub plugin the same extension point an allowlist would later occupy.
"""

# gh subcommands that emit the injected credential (GH_TOKEN) to stdout
# or otherwise persist it beyond the call. Denied wholesale because the
# fgap-gh client already routes ``auth`` to /auth/status, so the only
# callers reaching /cli with ``auth`` are bypassing the client.
_LEAKING_SUBCOMMANDS = frozenset({"auth"})


def check_policy(args: list[str], resource: str, config: dict) -> str | None:
    """Return None to allow, or a human-readable deny reason.

    Denies ``gh auth`` subcommands:
      - ``gh auth token`` prints the injected GH_TOKEN to stdout.
      - ``gh auth status --show-token`` does the same.
      - ``gh auth setup-git`` writes the credential into the local git
        credential helper (a side-channel that persists beyond the call).

    The fgap-gh client routes ``auth`` to ``/auth/status`` and never
    touches ``/cli``, so this deny only affects direct-``/cli`` callers
    and does not break the legit client path.
    """
    if args and args[0] in _LEAKING_SUBCOMMANDS:
        return (
            "gh auth subcommands leak the injected credential; "
            "use `fgap-gh auth status` (queries /auth/status) instead"
        )
    return None
