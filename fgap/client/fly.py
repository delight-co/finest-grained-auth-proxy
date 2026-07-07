"""Fly.io CLI wrapper.

Two execution paths, chosen per subcommand:

- API commands (status, machines, secrets, ...) run flyctl on the proxy
  host with the credential injected there — the token never reaches
  this side.
- Local commands (deploy, logs, ssh, ...) need this side's working
  directory (build context) or a connection the buffered /cli
  round-trip cannot carry (streaming, interactive shells, tunnels).
  For these the wrapper asks the proxy to mint a short-lived
  app-scoped deploy token and hands off to the local flyctl binary
  with only that ephemeral token injected.

Usage:
    fgap-fly status -a my-app
    fgap-fly machines list -a my-app
    fgap-fly deploy --remote-only          # app from ./fly.toml
    fgap-fly auth list
"""

import asyncio
import os
import re
import shutil
import sys

from .base import ProxyClient

# Commands handed off to the local flyctl (with a minted token) instead
# of the proxy-side subprocess. The minted token is a *deploy* token
# scoped to one app — commands needing broader scope (launch creating a
# new app, org-level wireguard) will fail with a permission error and
# belong on the proxy host itself.
LOCAL_COMMANDS = frozenset({
    "deploy", "launch", "logs", "console", "ssh", "sftp",
    "proxy", "agent", "wireguard",
})

# Long enough to survive a remote build; short enough that a leaked
# token is stale within the same working session.
DEFAULT_MINT_EXPIRY = "15m"

MAIN_HELP = """\
fgap-fly - finest-grained auth proxy for flyctl

USAGE
  fgap-fly <command> [args...]

Arguments are forwarded to flyctl. API commands run on the proxy host
with the credential injected there. Commands that need the local
working directory or a live connection (deploy, logs, ssh, ...) run the
local flyctl with a short-lived app-scoped token minted by the proxy.

The target app is taken from -a/--app, then $FLY_APP, then ./fly.toml.

COMMANDS
  auth        Show authentication status
  (everything else is a flyctl command)

OPTIONS
  --fgap-expiry <ttl>   TTL for the minted token (default 15m)

EXAMPLES
  fgap-fly status -a my-app
  fgap-fly machines list -a my-app
  fgap-fly secrets set KEY=value -a my-app
  fgap-fly deploy --remote-only
  fgap-fly logs -a my-app
"""

AUTH_HELP = """\
Display authentication status for configured Fly.io credentials.

USAGE
  fgap-fly auth list
"""


def _has_help_flag(args: list[str]) -> bool:
    return any(a in ("--help", "-h") for a in args)


def extract_app(args: list[str], *, environ: dict | None = None,
                toml_path: str = "fly.toml") -> str:
    """Resolve the target Fly app: -a/--app flag, then $FLY_APP, then
    the app key of ./fly.toml. Returns "" when nothing matches."""
    environ = os.environ if environ is None else environ
    for i, a in enumerate(args):
        if a in ("-a", "--app"):
            if i + 1 < len(args):
                return args[i + 1]
        elif a.startswith("--app=") or a.startswith("-a="):
            return a.split("=", 1)[1]
    if environ.get("FLY_APP"):
        return environ["FLY_APP"]
    try:
        with open(toml_path, encoding="utf-8") as f:
            for line in f:
                m = re.match(r'\s*app\s*=\s*"?([^"\s]+)"?', line)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return ""


def extract_expiry(args: list[str]) -> tuple[list[str], str]:
    """Strip the wrapper-level --fgap-expiry flag out of the args."""
    out = []
    expiry = DEFAULT_MINT_EXPIRY
    i = 0
    while i < len(args):
        if args[i] == "--fgap-expiry" and i + 1 < len(args):
            expiry = args[i + 1]
            i += 2
            continue
        if args[i].startswith("--fgap-expiry="):
            expiry = args[i].split("=", 1)[1]
            i += 1
            continue
        out.append(args[i])
        i += 1
    return out, expiry


def find_local_flyctl() -> str | None:
    """Locate the real flyctl, never this wrapper itself (the container
    may alias `fly` to fgap-fly)."""
    self_path = os.path.realpath(sys.argv[0])
    for name in ("flyctl", "fly"):
        p = shutil.which(name)
        if p and os.path.realpath(p) != self_path:
            return p
    return None


async def run(args: list[str], proxy_url: str) -> int:
    """Main wrapper logic. Returns exit code (local handoff never returns)."""
    if not args or args[0] in ("--help", "-h"):
        print(MAIN_HELP, end="")
        return 0

    cmd = args[0]

    if cmd == "auth":
        async with ProxyClient(proxy_url) as client:
            return await _handle_auth(args[1:], client)

    args, expiry = extract_expiry(args)
    app = extract_app(args)

    if cmd in LOCAL_COMMANDS and not _has_help_flag(args):
        return await _run_local(args, app, expiry, proxy_url)

    # Longer timeout: remote-builder-adjacent commands can be slow.
    # Always name the binary "flyctl": hosts don't always have the "fly"
    # alias installed
    async with ProxyClient(proxy_url, timeout=120) as client:
        try:
            result = await client.call_cli("flyctl", args, app)
        except (ConnectionError, ValueError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    if result["stderr"]:
        print(result["stderr"], file=sys.stderr, end=""
              if result["stderr"].endswith("\n") else "\n")
    if result["stdout"]:
        print(result["stdout"], end=""
              if result["stdout"].endswith("\n") else "\n")
    return result["exit_code"]


async def _run_local(args: list[str], app: str, expiry: str,
                     proxy_url: str) -> int:
    if not app:
        print("Error: could not determine the Fly app "
              "(pass -a/--app, set FLY_APP, or run where fly.toml lives)",
              file=sys.stderr)
        return 1
    local = find_local_flyctl()
    if local is None:
        print("Error: local flyctl not found on PATH (required for: "
              + ", ".join(sorted(LOCAL_COMMANDS)) + ")", file=sys.stderr)
        return 1

    async with ProxyClient(proxy_url) as client:
        try:
            result = await client.call_cli(
                "flyctl", ["mint", "deploy", "--expiry", expiry], app)
        except (ConnectionError, ValueError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
    if result["exit_code"] != 0:
        print(result["stderr"] or "Error: token mint failed",
              file=sys.stderr)
        return result["exit_code"] or 1
    token = result["stdout"].strip()
    if not token:
        print("Error: proxy returned an empty token", file=sys.stderr)
        return 1

    env = dict(os.environ)
    env["FLY_API_TOKEN"] = token
    env.setdefault("FLY_NO_UPDATE_CHECK", "1")
    # replace this process: streaming output and interactivity behave
    # exactly like a direct flyctl invocation
    os.execve(local, [os.path.basename(local), *args], env)


async def _handle_auth(args: list[str], client: ProxyClient) -> int:
    if not args or _has_help_flag(args):
        print(AUTH_HELP, end="")
        return 0

    if args[0] != "list":
        print(f"Error: Unknown auth command: {args[0]}", file=sys.stderr)
        print("Run 'fgap-fly auth --help' for usage.", file=sys.stderr)
        return 1

    try:
        data = await client.get_auth_status()
    except (ConnectionError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    creds = data.get("plugins", {}).get("fly", [])
    if not creds:
        print("No Fly.io credentials configured.")
        return 0

    for i, cred in enumerate(creds):
        valid = cred.get("valid", False)
        mark = "✓" if valid else "✗"
        print(f"  {mark} [{i}] {cred.get('masked_token', '***')}")
        if valid:
            email = cred.get("email", "")
            if email:
                print(f"      Account: {email}")
        else:
            print(f"      Error: {cred.get('error', 'Unknown error')}")
        resources = cred.get("resources", [])
        if resources:
            print(f"      Resources: {', '.join(resources)}")

    return 0


def main():
    args = sys.argv[1:]
    proxy_url = os.environ.get("FGAP_PROXY_URL", "http://localhost:8766")
    sys.exit(asyncio.run(run(args, proxy_url)))


if __name__ == "__main__":
    main()
