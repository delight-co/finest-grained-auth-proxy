"""Langfuse CLI wrapper.

Routes all langfuse commands through the fgap proxy for credential injection.

Usage:
    fgap-langfuse api traces list --limit 10
    fgap-langfuse api prompts list
    fgap-langfuse api sessions list
"""

import asyncio
import os
import sys

from .base import ProxyClient


MAIN_HELP = """\
fgap-langfuse - finest-grained auth proxy for langfuse

USAGE
  fgap-langfuse <command> [args...]

All arguments are forwarded to the langfuse CLI via the fgap proxy.
Credentials are injected by the proxy — no local API keys needed.

COMMANDS
  api         Interact with Langfuse API resources
  auth        Show authentication status

EXAMPLES
  fgap-langfuse api traces list --limit 10
  fgap-langfuse api prompts list
  fgap-langfuse api sessions list --limit 5
  fgap-langfuse api __schema

Run 'fgap-langfuse api <resource> --help' for more information.
"""

AUTH_HELP = """\
Display authentication status for configured Langfuse credentials.

USAGE
  fgap-langfuse auth list
"""


async def run(args: list[str], proxy_url: str) -> int:
    """Main wrapper logic. Returns exit code."""
    if not args or args[0] in ("--help", "-h"):
        print(MAIN_HELP, end="")
        return 0

    cmd = args[0]
    rest = args[1:]

    # Auth command: queries /auth/status instead of /cli
    if cmd == "auth":
        async with ProxyClient(proxy_url) as client:
            return await _handle_auth(rest, client)

    # Langfuse uses "default" as resource (single project per config)
    resource = "default"

    # Longer timeout: langfuse CLI can be slow on large trace lists
    async with ProxyClient(proxy_url, timeout=120) as client:
        try:
            result = await client.call_cli("langfuse", args, resource)
        except (ConnectionError, ValueError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

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
        print("Run 'fgap-langfuse auth --help' for usage.", file=sys.stderr)
        return 1

    try:
        data = await client.get_auth_status()
    except (ConnectionError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    creds = data.get("plugins", {}).get("langfuse", [])
    if not creds:
        print("No Langfuse credentials configured.")
        return 0

    for i, cred in enumerate(creds):
        valid = cred.get("valid", False)
        host = cred.get("host", "https://cloud.langfuse.com")
        masked_pk = cred.get("masked_public_key", "***")
        mark = "\u2713" if valid else "\u2717"
        print(f"  {mark} [{i}] {masked_pk}")
        if valid:
            print(f"      Host: {host}")
            project = cred.get("project", "")
            if project:
                print(f"      Project: {project}")
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
