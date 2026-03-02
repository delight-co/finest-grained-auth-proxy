"""GitHub CLI wrapper — Python rewrite of fgh.

Routes all gh commands through the fgap proxy for credential injection.
Replaces the Bash fgh script with cleaner Python.

Usage:
    fgap-gh issue list
    fgap-gh pr view 123
    fgap-gh discussion list
    fgap-gh sub-issue list 123
"""

import asyncio
import os
import re
import sys

from .base import ProxyClient


# =============================================================================
# Resource Detection
# =============================================================================

_GITHUB_RE = re.compile(r"github\.com[:/]([^/]+)/([^/.]+)")
_GIT_PROXY_RE = re.compile(r"/git/([^/]+)/([^/.]+)")
_API_ENDPOINT_RE = re.compile(r"^/?repos/([^/]+)/([^/]+)")


def parse_git_remote_url(url: str) -> str | None:
    """Extract owner/repo from a git remote URL."""
    m = _GITHUB_RE.search(url)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    m = _GIT_PROXY_RE.search(url)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    return None


def parse_api_endpoint(endpoint: str) -> str | None:
    """Extract owner/repo from a REST API endpoint."""
    m = _API_ENDPOINT_RE.match(endpoint)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    return None


def detect_resource_from_args(args: list[str]) -> str | None:
    """Extract -R/--repo value from args."""
    for i, arg in enumerate(args):
        if arg in ("-R", "--repo") and i + 1 < len(args):
            return args[i + 1]
        if arg.startswith("-R") and len(arg) > 2:
            return arg[2:]
        if arg.startswith("--repo="):
            return arg[7:]
    return None


async def get_git_remote_url(*, _run=None) -> str | None:
    """Run ``git remote get-url origin``."""
    _run = _run or _run_git
    return await _run("remote", "get-url", "origin")


async def get_current_branch(*, _run=None) -> str | None:
    """Run ``git branch --show-current``."""
    _run = _run or _run_git
    return await _run("branch", "--show-current")


async def _run_git(*args: str) -> str | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            return stdout.decode().strip() or None
    except FileNotFoundError:
        pass
    return None


# =============================================================================
# Argument Transformation
# =============================================================================


def strip_repo_flag(args: list[str]) -> list[str]:
    """Remove -R/--repo and its value from args."""
    result = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in ("-R", "--repo"):
            skip_next = True
            continue
        if arg.startswith("-R") and len(arg) > 2:
            continue
        if arg.startswith("--repo="):
            continue
        result.append(arg)
    return result


def transform_body_file(args: list[str], *, _stdin=None) -> list[str]:
    """Convert ``--body-file`` and ``-F`` to ``--body`` with contents.

    ``-F`` means different things depending on the subcommand:
    - ``gh api``: ``-F`` is ``--field`` (``key=value``).  Passed through.
    - ``gh issue/pr create/comment/edit``: ``-F`` is ``--body-file``.
      A path is read client-side; ``-`` reads stdin.
    """
    _stdin = _stdin or sys.stdin
    is_api = len(args) > 0 and args[0] == "api"
    result = []
    skip_next = False
    for i, arg in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        next_arg = args[i + 1] if i + 1 < len(args) else None

        if arg == "--body-file" and next_arg is not None:
            if next_arg == "-":
                result.extend(["--body", _stdin.read()])
            else:
                result.extend(["--body", _read_file(next_arg)])
            skip_next = True
        elif arg.startswith("--body-file="):
            result.extend(["--body", _read_file(arg[len("--body-file="):])])
        elif arg == "-F" and next_arg is not None and not is_api:
            # Non-api context: -F is --body-file
            if next_arg == "-":
                result.extend(["--body", _stdin.read()])
            else:
                result.extend(["--body", _read_file(next_arg)])
            skip_next = True
        else:
            result.append(arg)
    return result


def transform_api_input(args: list[str], *, _stdin=None) -> tuple[list[str], str | None]:
    """Convert ``--input <path>`` to ``--input -`` with stdin data.

    For ``gh api``, ``--input`` reads a file as the request body.
    Since the file only exists on the client (sandbox), we read it
    client-side and return the contents as stdin_data to be piped
    to the subprocess on the server.

    Returns (transformed_args, stdin_data) where stdin_data is None
    if no --input flag was found.
    """
    _stdin = _stdin or sys.stdin
    if not args or args[0] != "api":
        return args, None

    result = []
    stdin_data = None
    skip_next = False
    for i, arg in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        next_arg = args[i + 1] if i + 1 < len(args) else None

        if arg == "--input" and next_arg is not None:
            if next_arg == "-":
                stdin_data = _stdin.read()
            else:
                stdin_data = _read_file(next_arg)
            result.append("--input")
            result.append("-")
            skip_next = True
        elif arg.startswith("--input="):
            path = arg[len("--input="):]
            if path == "-":
                stdin_data = _stdin.read()
            else:
                stdin_data = _read_file(path)
            result.append("--input=-")
        else:
            result.append(arg)
    return result, stdin_data


def _read_file(path: str) -> str:
    if not os.path.isfile(path):
        raise ValueError(f"File not found: {path}")
    with open(path) as f:
        return f.read()


# =============================================================================
# Help Text
# =============================================================================

MAIN_HELP = """\
fgap-gh - finest-grained auth proxy for gh

USAGE
  fgap-gh <command> [args...]

COMMANDS
  issue       Work with issues (via gh)
  pr          Work with pull requests (via gh)
  api         Call GitHub REST API (via gh)
  discussion  Work with discussions (custom)
  sub-issue   Work with sub-issues (custom)
  auth        Show authentication status

All commands are routed through the fgap proxy for credential injection.
Run 'fgap-gh <command> --help' for more information on a command.
"""

AUTH_HELP = """\
Display authentication status for configured GitHub credentials.

USAGE
  fgap-gh auth status
"""

DISCUSSION_HELP = """\
Work with GitHub Discussions.

USAGE
  fgap-gh discussion <command> [flags]

COMMANDS
  list             List discussions in a repository
  view             View a discussion
  create           Create a new discussion
  edit             Edit a discussion
  close            Close a discussion
  reopen           Reopen a discussion
  delete           Delete a discussion
  comment          Add a comment to a discussion
  comment edit     Edit a discussion comment
  comment delete   Delete a discussion comment
  answer           Mark a comment as the answer
  unanswer         Unmark a comment as the answer
  poll vote        Vote on a discussion poll

FLAGS
  -R, --repo <owner/repo>   Specify repository (default: from git remote)
  -t, --title <text>        Title (for create/edit)
  -b, --body <text>         Body text
  -c, --category <name>     Category name (for create)
  --reply-to <comment_id>   Reply to a comment
"""

SUB_ISSUE_HELP = """\
Work with GitHub sub-issues.

USAGE
  fgap-gh sub-issue <command> [flags]

COMMANDS
  list      List sub-issues of an issue
  parent    Show parent issue
  add       Add a sub-issue to an issue
  remove    Remove a sub-issue from an issue
  reorder   Reorder sub-issues

FLAGS
  -R, --repo <owner/repo>   Specify repository (default: from git remote)
"""


# =============================================================================
# Main
# =============================================================================


def _has_help_flag(args: list[str]) -> bool:
    return any(a in ("--help", "-h") for a in args)


async def run(
    args: list[str],
    proxy_url: str,
    *,
    _get_remote_url=None,
    _get_branch=None,
) -> int:
    """Main wrapper logic. Returns exit code.

    Args:
        args: CLI arguments (sys.argv[1:]).
        proxy_url: fgap proxy URL.
        _get_remote_url: Override for git remote detection (testing).
        _get_branch: Override for git branch detection (testing).
    """
    _get_remote_url = _get_remote_url or get_git_remote_url
    _get_branch = _get_branch or get_current_branch

    if not args or args[0] in ("--help", "-h"):
        print(MAIN_HELP, end="")
        return 0

    cmd = args[0]
    rest = args[1:]

    # Auth command: queries /auth/status instead of /cli
    if cmd == "auth":
        async with ProxyClient(proxy_url) as client:
            return await _handle_auth(rest, client)

    # Custom command help (gh doesn't handle these)
    if cmd == "discussion" and (not rest or _has_help_flag(rest)):
        print(DISCUSSION_HELP, end="")
        return 0

    if cmd == "sub-issue" and (not rest or _has_help_flag(rest)):
        print(SUB_ISSUE_HELP, end="")
        return 0

    # Prohibit raw GraphQL (use high-level commands instead)
    if cmd == "api" and rest and rest[0] == "graphql":
        print(
            "Error: GraphQL API is not supported. "
            "Use high-level commands (issue, pr, discussion, sub-issue) instead.",
            file=sys.stderr,
        )
        return 1

    # Detect resource: -R flag > api endpoint > git remote
    resource = detect_resource_from_args(args)

    if not resource and cmd == "api" and rest:
        resource = parse_api_endpoint(rest[0])

    if not resource:
        url = await _get_remote_url()
        if url:
            resource = parse_git_remote_url(url)

    if not resource:
        print(
            "Error: Could not determine repository. Use -R owner/repo",
            file=sys.stderr,
        )
        return 1

    # Transform args
    clean_args = strip_repo_flag(args)

    try:
        clean_args = transform_body_file(clean_args)
        clean_args, stdin_data = transform_api_input(clean_args)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # pr create: auto-inject --head if missing
    if (
        len(clean_args) >= 2
        and clean_args[0] == "pr"
        and clean_args[1] == "create"
        and not any(a.startswith("--head") for a in clean_args)
    ):
        branch = await _get_branch()
        if branch:
            owner = resource.split("/")[0]
            clean_args.extend(["--head", f"{owner}:{branch}"])

    # Call proxy
    async with ProxyClient(proxy_url) as client:
        try:
            result = await client.call_cli("gh", clean_args, resource, stdin_data=stdin_data)
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


async def _handle_auth(args: list[str], client: ProxyClient) -> int:
    if not args or _has_help_flag(args):
        print(AUTH_HELP, end="")
        return 0

    if args[0] != "status":
        print(f"Error: Unknown auth command: {args[0]}", file=sys.stderr)
        print("Run 'fgap-gh auth --help' for usage.", file=sys.stderr)
        return 1

    try:
        data = await client.get_auth_status()
    except (ConnectionError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    creds = data.get("plugins", {}).get("github", [])
    if not creds:
        print("No GitHub credentials configured.")
        return 0

    for i, cred in enumerate(creds):
        valid = cred.get("valid", False)
        token = cred.get("masked_token", "***")
        mark = "\u2713" if valid else "\u2717"
        print(f"  {mark} [{i}] {token}")
        if valid:
            if cred.get("user"):
                print(f"      User: {cred['user']}")
            if cred.get("scopes"):
                print(f"      Scopes: {cred['scopes']}")
            if cred.get("rate_limit_remaining"):
                print(f"      Rate limit remaining: {cred['rate_limit_remaining']}")
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
