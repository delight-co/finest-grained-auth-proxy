"""Custom commands for the Fly plugin.

``mint`` is not a flyctl subcommand: it exists so a client on another
filesystem can obtain a short-lived, app-scoped deploy token without ever
seeing the long-lived credential. The proxy host runs
``flyctl tokens create deploy -a <app> --expiry <ttl>`` with the master
token injected and returns only the ephemeral token.
"""

from fgap.core.executor import execute_cli

DEFAULT_EXPIRY = "5m"
MINT_KINDS = ("deploy",)


def parse_mint_args(args: list[str]) -> tuple[str, str] | str:
    """Parse ``mint`` arguments into (kind, expiry).

    Returns an error message string on invalid input.
    """
    kind = args[0] if args else ""
    if kind not in MINT_KINDS:
        return (f"mint: unknown token kind {kind!r} "
                f"(supported: {', '.join(MINT_KINDS)})")
    expiry = DEFAULT_EXPIRY
    rest = args[1:]
    i = 0
    while i < len(rest):
        if rest[i] in ("--expiry", "-x"):
            if i + 1 >= len(rest):
                return f"mint: {rest[i]} requires a value (e.g. 5m, 1h)"
            expiry = rest[i + 1]
            i += 2
            continue
        return f"mint: unknown argument {rest[i]!r}"
    return kind, expiry


async def mint_command(args: list[str], resource: str, credential: dict,
                       *, _execute=execute_cli) -> dict:
    """Mint a short-lived deploy token for the resource app.

    The token is scoped to one app and expires on its own, so handing it
    back to the caller does not extend the trust boundary the way the
    master token would.
    """
    parsed = parse_mint_args(args)
    if isinstance(parsed, str):
        return {"exit_code": 2, "stdout": "", "stderr": parsed}
    _kind, expiry = parsed
    return await _execute(
        "flyctl",
        ["tokens", "create", "deploy", "-a", resource, "--expiry", expiry],
        credential["env"],
    )
