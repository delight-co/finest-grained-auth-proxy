"""Custom commands for the Fly plugin.

``credential`` is not a flyctl subcommand: it hands the resource app's
configured token to the caller for commands that must run client-side
(deploy needs the caller's build context; ssh and log streaming need a
live connection the buffered /cli round-trip cannot carry).

An ephemeral handout would be strictly better, but Fly's API does not
let tokens mint further tokens — measured 2026-07: the
createLimitedAccessToken mutation is denied both to app deploy tokens
and to live org tokens; only interactive user sessions may mint. So
what crosses is the stored long-lived app-scoped token, and the
*handout* is the audited event — subsequent use happens directly
against Fly's API, outside the proxy's sight. Keeping even that off the
client is the deploy-from-ref discussion (#104).
"""


def parse_credential_args(args: list[str]) -> str | None:
    """Validate ``credential`` arguments. Returns an error message or None."""
    if args:
        return f"credential: takes no arguments (got {args[0]!r})"
    return None


async def credential_command(args: list[str], resource: str,
                             credential: dict) -> dict:
    """Hand out the resource app's configured token (logged by the router)."""
    error = parse_credential_args(args)
    if error:
        return {"exit_code": 2, "stdout": "", "stderr": error}
    token = (credential.get("env") or {}).get("FLY_API_TOKEN", "")
    if not token:
        return {"exit_code": 1, "stdout": "",
                "stderr": "credential: no token configured for this app"}
    return {"exit_code": 0, "stdout": token + "\n", "stderr": ""}
