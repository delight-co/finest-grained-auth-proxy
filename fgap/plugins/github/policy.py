"""Server-side policy for the gh CLI.

The capability model must hold even when a caller bypasses the fgap-gh
client and POSTs ``/cli`` directly. Three classes of subcommand are denied
here at the choke point (the router turns a non-None return into HTTP 403):

- ``gh auth`` exfiltrates the injected credential to stdout / git credential
  helper.
- ``gh repo {clone,create,fork,sync}`` touches the filesystem on the SERVER
  side, where fgap actually runs the CLI. The client and server share
  path-namespace *names* but not the same filesystem, so a path the client
  intends for its own environment lands on the server's filesystem at the
  literal value sent — letting a ``/cli`` caller write to arbitrary
  server-writable paths via the server's privileges. These must go through
  the git proxy endpoint instead, which streams the pack to the client
  (no FS write on the server).
- Blocking watch commands (``gh run watch``, ``gh pr checks --watch``)
  cannot finish within the proxy's single-shot execution budget
  (``timeouts.cli``); letting them start only to be killed mid-flight
  produced timeouts that looked exactly like the watched run failing.
  Denying them up front turns a slow ambiguous death into an immediate
  error that names the polling alternative.

This mirrors the structure of :mod:`fgap.plugins.aws.policy`, giving the
GitHub plugin the same extension point an allowlist would later occupy.
"""

# gh subcommands that emit the injected credential (GH_TOKEN) to stdout
# or otherwise persist it beyond the call. Denied wholesale because the
# fgap-gh client already routes ``auth`` to /auth/status, so the only
# callers reaching /cli with ``auth`` are bypassing the client.
_LEAKING_SUBCOMMANDS = frozenset({"auth"})

# gh repo subcommands that touch the filesystem on the SERVER side (where
# fgap runs the CLI). The client and server share path names but not the
# same filesystem, so a ``gh repo clone <path>`` from the client lands on
# the server at the literal path — letting the caller write to arbitrary
# server-writable paths via the server's privileges. Route these through
# the git proxy endpoint instead (``http://<fgap>/git/<owner>/<repo>.git``),
# which streams the pack and never writes to the server's FS.
_REPO_FS_SUBCOMMANDS = frozenset({"clone", "create", "fork", "sync"})

# Watch-style commands block until an external event (a CI run finishing),
# which by design outlives any sane per-command budget. Under the proxy's
# single-shot execution model they can only ever die by timeout, and that
# death is easy to misread as the watched run failing. Deny them up front
# with the polling alternative instead.
_POLLING_HINT = (
    "poll instead: `gh run view <run-id> --json status,conclusion` "
    "in a sleep loop until status is \"completed\""
)


def _is_blocking_watch(args: list[str]) -> bool:
    if len(args) >= 2 and args[0] == "run" and args[1] == "watch":
        return True
    if (
        len(args) >= 2
        and args[0] == "pr"
        and args[1] == "checks"
        and "--watch" in args
    ):
        return True
    return False


def check_policy(args: list[str], resource: str, config: dict) -> str | None:
    """Return None to allow, or a human-readable deny reason.

    Denies ``gh auth`` subcommands (credential leak), ``gh repo``
    filesystem-touching subcommands (server-side FS write), and blocking
    watch commands (cannot finish within the proxy's execution budget).

    ``gh repo clone`` / ``create`` / ``fork`` / ``sync`` run the underlying
    git operation on the fgap server, so the destination path the client
    sends is interpreted in the server's filesystem — not the client's.
    Use the git proxy endpoint to clone (streams to the client, no server
    FS write).
    """
    if args and args[0] in _LEAKING_SUBCOMMANDS:
        return (
            "gh auth subcommands leak the injected credential; "
            "use `fgap-gh auth status` (queries /auth/status) instead"
        )
    if (
        len(args) >= 2
        and args[0] == "repo"
        and args[1] in _REPO_FS_SUBCOMMANDS
    ):
        return (
            f"gh repo {args[1]} runs on the fgap server and writes to the "
            f"server's filesystem at the path you give (the client and "
            f"server share path names but not the same filesystem, so the "
            f"call could land outside your environment). Clone via the git "
            f"proxy instead: "
            f"git clone http://<fgap-host>:<port>/git/<owner>/<repo>.git"
        )
    if _is_blocking_watch(args):
        return (
            f"gh {args[0]} {args[1]} blocks until the watched run finishes "
            f"and cannot complete within the proxy's single-shot execution "
            f"budget (timeouts.cli) — it would only die by timeout, which "
            f"is indistinguishable from the run failing; {_POLLING_HINT}"
        )
    return None
