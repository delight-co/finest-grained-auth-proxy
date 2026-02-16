"""Issue command: partial body replacement for issues and comments via REST API.

Handles:
- issue edit <number> --old "..." --new "..." [--replace-all]
- issue comment edit <comment-id> --old "..." --new "..." [--replace-all]

Everything else falls through to gh CLI (returns None).
"""

import aiohttp

from fgap.core.http import get_session

_API_URL = "https://api.github.com"


async def execute(args: list[str], resource: str, credential: dict) -> dict | None:
    """Execute issue command. Returns None to fall through to gh CLI."""
    if not args:
        return None

    subcmd = args[0]
    rest = args[1:]
    owner, repo = resource.split("/", 1)
    token = credential["env"]["GH_TOKEN"]

    if subcmd == "edit" and _has_old_and_new(rest):
        return await _handle_edit(rest, owner, repo, token)

    if subcmd == "comment" and len(rest) > 0 and rest[0] == "edit":
        if _has_old_and_new(rest[1:]):
            return await _handle_comment_edit(rest[1:], owner, repo, token)

    return None


def _has_old_and_new(args: list[str]) -> bool:
    return any(a == "--old" for a in args) and any(a == "--new" for a in args)


def _parse_edit_args(args: list[str]) -> tuple[list[str], str, str, bool]:
    """Parse --old, --new, --replace-all from args.

    Returns (positional_args, old, new, replace_all).
    """
    positional = []
    old = None
    new = None
    replace_all = False

    i = 0
    while i < len(args):
        if args[i] == "--old":
            if i + 1 >= len(args):
                raise ValueError("--old requires a value")
            old = args[i + 1]
            i += 2
        elif args[i] == "--new":
            if i + 1 >= len(args):
                raise ValueError("--new requires a value")
            new = args[i + 1]
            i += 2
        elif args[i] == "--replace-all":
            replace_all = True
            i += 1
        else:
            positional.append(args[i])
            i += 1

    return positional, old, new, replace_all


def _partial_replace(body: str, old: str, new: str, replace_all: bool) -> str:
    """Replace old with new in body.

    Same semantics as Claude Code's Edit tool:
    - Fail if old not found
    - Fail if old matches multiple locations (unless --replace-all)
    """
    count = body.count(old)

    if count == 0:
        raise ValueError("old string not found in body")

    if count > 1 and not replace_all:
        raise ValueError(
            f"old string found {count} times in body "
            f"(use --replace-all to replace all occurrences)"
        )

    if replace_all:
        return body.replace(old, new)
    return body.replace(old, new, 1)


async def _github_rest(method: str, url: str, token: str, body: dict | None = None) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "fgap",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    session = get_session()
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()
    try:
        async with session.request(
            method, url, json=body, headers=headers,
        ) as resp:
            return await resp.json()
    finally:
        if own_session:
            await session.close()


async def _handle_edit(
    args: list[str], owner: str, repo: str, token: str,
    api_url: str | None = None,
) -> dict:
    """Handle `issue edit <number> --old "..." --new "..."`."""
    api_url = api_url or _API_URL

    try:
        positional, old, new, replace_all = _parse_edit_args(args)
    except ValueError as e:
        return {"exit_code": 1, "stdout": "", "stderr": str(e)}

    if not positional:
        return {"exit_code": 1, "stdout": "", "stderr": "issue number required"}

    try:
        issue_number = int(positional[0])
    except ValueError:
        return {"exit_code": 1, "stdout": "", "stderr": f"Invalid issue number: {positional[0]}"}

    url = f"{api_url}/repos/{owner}/{repo}/issues/{issue_number}"

    try:
        issue_data = await _github_rest("GET", url, token)
        current_body = issue_data.get("body") or ""
        updated_body = _partial_replace(current_body, old, new, replace_all)
        await _github_rest("PATCH", url, token, body={"body": updated_body})
    except ValueError as e:
        return {"exit_code": 1, "stdout": "", "stderr": str(e)}

    return {"exit_code": 0, "stdout": "", "stderr": f"Updated issue #{issue_number}"}


async def _handle_comment_edit(
    args: list[str], owner: str, repo: str, token: str,
    api_url: str | None = None,
) -> dict:
    """Handle `issue comment edit <comment-id> --old "..." --new "..."`."""
    api_url = api_url or _API_URL

    try:
        positional, old, new, replace_all = _parse_edit_args(args)
    except ValueError as e:
        return {"exit_code": 1, "stdout": "", "stderr": str(e)}

    if not positional:
        return {"exit_code": 1, "stdout": "", "stderr": "comment ID required"}

    comment_id = positional[0]
    url = f"{api_url}/repos/{owner}/{repo}/issues/comments/{comment_id}"

    try:
        comment_data = await _github_rest("GET", url, token)
        current_body = comment_data.get("body") or ""
        updated_body = _partial_replace(current_body, old, new, replace_all)
        await _github_rest("PATCH", url, token, body={"body": updated_body})
    except ValueError as e:
        return {"exit_code": 1, "stdout": "", "stderr": str(e)}

    return {"exit_code": 0, "stdout": "", "stderr": f"Updated comment {comment_id}"}
