"""PR command: partial body replacement for pull requests via REST API.

Handles:
- pr edit <number> --old "..." --new "..." [--replace-all] [--title "..."]
- pr comment edit <comment-id> --old "..." --new "..." [--replace-all]

Everything else falls through to gh CLI (returns None).
"""

from .issue import (
    _github_rest,
    _handle_comment_edit,
    _has_old_and_new,
    _parse_edit_args,
    _partial_replace,
)

_API_URL = "https://api.github.com"


async def execute(args: list[str], resource: str, credential: dict) -> dict | None:
    """Execute pr command. Returns None to fall through to gh CLI."""
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


async def _handle_edit(
    args: list[str], owner: str, repo: str, token: str,
    api_url: str | None = None,
) -> dict:
    """Handle `pr edit <number> --old "..." --new "..." [--title "..."]`."""
    api_url = api_url or _API_URL

    try:
        positional, old, new, replace_all, title = _parse_edit_args(args)
    except ValueError as e:
        return {"exit_code": 1, "stdout": "", "stderr": str(e)}

    if not positional:
        return {"exit_code": 1, "stdout": "", "stderr": "PR number required"}

    try:
        pr_number = int(positional[0])
    except ValueError:
        return {"exit_code": 1, "stdout": "", "stderr": f"Invalid PR number: {positional[0]}"}

    url = f"{api_url}/repos/{owner}/{repo}/pulls/{pr_number}"

    try:
        pr_data = await _github_rest("GET", url, token)
        current_body = pr_data.get("body") or ""
        updated_body = _partial_replace(current_body, old, new, replace_all)
        patch_payload: dict[str, str] = {"body": updated_body}
        if title is not None:
            patch_payload["title"] = title
        await _github_rest("PATCH", url, token, body=patch_payload)
    except ValueError as e:
        return {"exit_code": 1, "stdout": "", "stderr": str(e)}

    return {"exit_code": 0, "stdout": "", "stderr": f"Updated PR #{pr_number}"}
