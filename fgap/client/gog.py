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
  auth        Show authentication status

All commands are routed through the fgap proxy for credential injection.
Run 'fgap-gog <command> --help' for more information on a command.
"""

AUTH_HELP = """\
Display authentication status for configured Google credentials.

USAGE
  fgap-gog auth list
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
        return await _handle_auth(rest, proxy_url)

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


def _has_help_flag(args: list[str]) -> bool:
    return any(a in ("--help", "-h") for a in args)


async def _handle_auth(args: list[str], proxy_url: str) -> int:
    if not args or _has_help_flag(args):
        print(AUTH_HELP, end="")
        return 0

    if args[0] != "list":
        print(f"Error: Unknown auth command: {args[0]}", file=sys.stderr)
        print("Run 'fgap-gog auth --help' for usage.", file=sys.stderr)
        return 1

    client = ProxyClient(proxy_url)
    try:
        data = await client.get_auth_status()
    except (ConnectionError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    creds = data.get("plugins", {}).get("google", [])
    if not creds:
        print("No Google credentials configured.")
        return 0

    for i, cred in enumerate(creds):
        valid = cred.get("valid", False)
        masked = cred.get("masked_keyring_password", "***")
        mark = "\u2713" if valid else "\u2717"
        print(f"  {mark} [{i}] {masked}")
        if valid:
            if cred.get("accounts"):
                print(f"      Accounts: {cred['accounts']}")
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
