"""Notion CLI wrapper.

Routes all notion commands through the fgap proxy for credential injection.

Usage:
    fgap-notion search "query"
    fgap-notion page get <page-id>
    fgap-notion database query <database-id>
"""

import asyncio
import os
import sys

from .base import ProxyClient


# =============================================================================
# Help Text
# =============================================================================

MAIN_HELP = """\
fgap-notion - finest-grained auth proxy for notion

USAGE
  fgap-notion <command> [args...]

COMMANDS
  search      Search pages and databases
  page        Work with pages
  database    Work with databases
  block       Work with blocks
  user        Work with users
  comment     Work with comments
  auth        Show authentication status

All commands are routed through the fgap proxy for credential injection.
Run 'fgap-notion <command> --help' for more information on a command.
"""

AUTH_HELP = """\
Display authentication status for configured Notion credentials.

USAGE
  fgap-notion auth list
"""


# =============================================================================
# Main
# =============================================================================


async def run(
    args: list[str],
    proxy_url: str,
) -> int:
    """Main wrapper logic. Returns exit code.

    Args:
        args: CLI arguments (sys.argv[1:]).
        proxy_url: fgap proxy URL.
    """
    if not args or args[0] in ("--help", "-h"):
        print(MAIN_HELP, end="")
        return 0

    cmd = args[0]
    rest = args[1:]

    # Auth command: queries /auth/status instead of /cli
    if cmd == "auth":
        async with ProxyClient(proxy_url) as client:
            return await _handle_auth(rest, client)

    # Notion uses "default" as resource since there's no
    # multi-workspace routing (one token = one workspace)
    resource = "default"

    # Call proxy
    async with ProxyClient(proxy_url) as client:
        try:
            result = await client.call_cli("notion", args, resource)
        except (ConnectionError, ValueError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    # Output
    if result["exit_code"] != 0:
        if result["stderr"]:
            print(result["stderr"], file=sys.stderr)
        return result["exit_code"]

    if result["stderr"]:
        print(result["stderr"], file=sys.stderr)
    if result["stdout"]:
        print(result["stdout"])

    return 0


def _has_help_flag(args: list[str]) -> bool:
    return any(a in ("--help", "-h") for a in args)


async def _handle_auth(args: list[str], client: ProxyClient) -> int:
    if not args or _has_help_flag(args):
        print(AUTH_HELP, end="")
        return 0

    if args[0] != "list":
        print(f"Error: Unknown auth command: {args[0]}", file=sys.stderr)
        print("Run 'fgap-notion auth --help' for usage.", file=sys.stderr)
        return 1

    try:
        data = await client.get_auth_status()
    except (ConnectionError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    creds = data.get("plugins", {}).get("notion", [])
    if not creds:
        print("No Notion credentials configured.")
        return 0

    for i, cred in enumerate(creds):
        valid = cred.get("valid", False)
        masked = cred.get("masked_token", "***")
        mark = "\u2713" if valid else "\u2717"
        print(f"  {mark} [{i}] {masked}")
        if valid:
            bot_name = cred.get("bot_name", "")
            if bot_name:
                print(f"      Bot: {bot_name}")
        else:
            print(f"      Error: {cred.get('error', 'Unknown error')}")
        resources = cred.get("resources", [])
        if resources:
            print(f"      Resources: {', '.join(resources)}")

    return 0


def main():
    """CLI entry point."""
    proxy_url = os.environ.get("FGAP_PROXY_URL", "http://localhost:8766")
    sys.exit(asyncio.run(run(sys.argv[1:], proxy_url)))


if __name__ == "__main__":
    main()
