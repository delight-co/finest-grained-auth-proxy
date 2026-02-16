"""Google Workspace CLI wrapper.

Routes all gog commands through the fgap proxy for credential injection.

Usage:
    fgap-gog calendar events <calendarId>
    fgap-gog sheets get <sheetId> "Tab!A1:D10"
    fgap-gog gmail search 'newer_than:7d'
"""

import asyncio
import os
import sys

from .base import ProxyClient


# =============================================================================
# Resource Detection
# =============================================================================


def detect_account_from_args(args: list[str]) -> str | None:
    """Extract --account value from args."""
    for i, arg in enumerate(args):
        if arg == "--account" and i + 1 < len(args):
            return args[i + 1]
        if arg.startswith("--account="):
            return arg[len("--account="):]
    return None


# =============================================================================
# Help Text
# =============================================================================

MAIN_HELP = """\
fgap-gog - finest-grained auth proxy for gog

USAGE
  fgap-gog <command> [args...]

COMMANDS
  calendar    Work with Google Calendar
  gmail       Work with Gmail
  sheets      Work with Google Sheets
  docs        Work with Google Docs
  drive       Work with Google Drive
  contacts    Work with Google Contacts
  auth        Manage authentication

All commands are routed through the fgap proxy for credential injection.
Run 'fgap-gog <command> --help' for more information on a command.
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

    # Resource detection: --account flag > GOG_ACCOUNT env > "default"
    resource = (
        detect_account_from_args(args)
        or os.environ.get("GOG_ACCOUNT")
        or "default"
    )

    # Call proxy
    client = ProxyClient(proxy_url)
    try:
        result = await client.call_cli("gog", args, resource)
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


def main():
    """CLI entry point."""
    proxy_url = os.environ.get("FGAP_PROXY_URL", "http://localhost:8766")
    sys.exit(asyncio.run(run(sys.argv[1:], proxy_url)))


if __name__ == "__main__":
    main()
