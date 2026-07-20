"""AWS CLI wrapper (read-only observability).

Routes aws commands through the fgap proxy for credential injection.
The proxy enforces a curated read-only allowlist; write operations,
secret-returning reads and credential minting are denied server-side.

Usage:
    fgap-aws --account my-account logs tail /my/log-group --since 10m
    fgap-aws --account my-account ecs describe-services --cluster c --services s
    fgap-aws auth list
"""

import asyncio
import os
import sys

from .base import ProxyClient

MAIN_HELP = """\
fgap-aws - finest-grained auth proxy for the aws CLI (read-only)

USAGE
  fgap-aws [--account <alias>] <service> <operation> [args...]

Arguments are forwarded to the aws CLI via the fgap proxy. Credentials
are injected by the proxy — no local AWS credentials needed. Only a
curated read-only operation set is allowed (denials name the reason).

The account alias (--account or the FGAP_AWS_ACCOUNT environment
variable) selects which configured credential the proxy uses. It must
match a 'resources' pattern of a credential entry in the proxy config;
there is no default.

COMMANDS
  <service> <operation>   Forwarded to the aws CLI (allowlisted)
  auth                    Show authentication status

EXAMPLES
  fgap-aws --account my-account logs tail /my/log-group --since 10m
  fgap-aws --account my-account ecs describe-services --cluster c --services s
  FGAP_AWS_ACCOUNT=my-account fgap-aws cloudwatch list-metrics --namespace ECS/ContainerInsights
  fgap-aws auth list
"""

AUTH_HELP = """\
Display authentication status for configured AWS credentials.

USAGE
  fgap-aws auth list
"""

NO_ACCOUNT_ERROR = """\
Error: no account specified.

The proxy routes to a configured AWS credential by account alias. Pass
--account <alias> or set the FGAP_AWS_ACCOUNT environment variable. Run
'fgap-aws auth list' to see the configured accounts.
"""


def extract_account(args: list[str], environ: dict | None = None,
                    ) -> tuple[str | None, list[str]]:
    """Pull --account out of argv, falling back to FGAP_AWS_ACCOUNT.

    Returns (account or None, args with the flag removed). The flag wins
    over the environment variable.
    """
    if environ is None:
        environ = dict(os.environ)
    account = None
    remaining: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--account":
            if i + 1 < len(args):
                account = args[i + 1]
                i += 2
            else:
                i += 1
            continue
        if arg.startswith("--account="):
            account = arg.split("=", 1)[1]
            i += 1
            continue
        remaining.append(arg)
        i += 1
    if not account:
        account = environ.get("FGAP_AWS_ACCOUNT") or None
    return account, remaining


def _has_help_flag(args: list[str]) -> bool:
    return any(a in ("--help", "-h", "help") for a in args)


async def run(args: list[str], proxy_url: str) -> int:
    """Main wrapper logic. Returns exit code."""
    account, args = extract_account(args)

    if not args or args[0] in ("--help", "-h"):
        print(MAIN_HELP, end="")
        return 0

    cmd = args[0]
    rest = args[1:]

    # Auth command: queries /auth/status instead of /cli (no account needed)
    if cmd == "auth":
        async with ProxyClient(proxy_url) as client:
            return await _handle_auth(rest, client)

    if account is None:
        if not _has_help_flag(args):
            print(NO_ACCOUNT_ERROR, end="", file=sys.stderr)
            return 2
        resource = ""  # the server serves help without a resource
    else:
        resource = account

    # Longer timeout: log reads can take a while on large groups
    async with ProxyClient(proxy_url, timeout=120) as client:
        try:
            result = await client.call_cli("aws", args, resource)
        except (ConnectionError, ValueError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    if result["stderr"]:
        print(result["stderr"], file=sys.stderr)
    if result["stdout"]:
        print(result["stdout"])
    return result["exit_code"]


async def _handle_auth(args: list[str], client: ProxyClient) -> int:
    if not args or _has_help_flag(args):
        print(AUTH_HELP, end="")
        return 0

    if args[0] != "list":
        print(f"Error: Unknown auth command: {args[0]}", file=sys.stderr)
        print("Run 'fgap-aws auth --help' for usage.", file=sys.stderr)
        return 1

    try:
        data = await client.get_auth_status()
    except (ConnectionError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    creds = data.get("plugins", {}).get("aws", [])
    if not creds:
        print("No AWS credentials configured.")
        return 0

    for i, cred in enumerate(creds):
        valid = cred.get("valid", False)
        mark = "✓" if valid else "✗"
        print(f"  {mark} [{i}] {cred.get('credential', '***')}")
        if valid:
            if cred.get("account"):
                print(f"      Account: {cred['account']}")
            if cred.get("arn"):
                print(f"      Arn: {cred['arn']}")
        else:
            print(f"      Error: {cred.get('error', 'Unknown error')}")
        resources = cred.get("resources", [])
        if resources:
            print(f"      Resources: {', '.join(resources)}")
        services = cred.get("services", [])
        if services:
            print(f"      Services: {', '.join(services)}")

    return 0


def main():
    """CLI entry point."""
    proxy_url = os.environ.get("FGAP_PROXY_URL", "http://localhost:8766")
    sys.exit(asyncio.run(run(sys.argv[1:], proxy_url)))


if __name__ == "__main__":
    main()
