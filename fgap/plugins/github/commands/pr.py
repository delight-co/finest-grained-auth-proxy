"""PR command: partial body replacement and review thread resolution.

Handles:
- pr edit <number> --old "..." --new "..." [--replace-all] [--title "..."]
- pr comment edit <comment-id> --old "..." --new "..." [--replace-all]
- pr review-thread resolve <comment-id>
- pr review-thread unresolve <comment-id>

Everything else falls through to gh CLI (returns None).
"""

from .issue import (
    _github_rest,
    _handle_comment_edit,
    _has_old_and_new,
    _parse_edit_args,
    _partial_replace,
)
from ..graphql import execute_graphql

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

    if subcmd == "review-thread":
        if rest and rest[0] in ("resolve", "unresolve") and len(rest) > 1:
            return await _handle_review_thread(rest[0], rest[1], token)
        return {"exit_code": 1, "stdout": "", "stderr": "Usage: pr review-thread resolve|unresolve <comment-id>"}

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


async def _get_thread_id_for_comment(
    comment_id: str, token: str, *, url: str | None = None,
) -> str:
    """Resolve a review comment node ID to its parent review thread ID.

    Fetches the PR's review threads via the comment's parent PR,
    then finds the thread containing the given comment.
    """
    query = """
    query($id: ID!) {
        node(id: $id) {
            ... on PullRequestReviewComment {
                pullRequest {
                    reviewThreads(first: 100) {
                        nodes {
                            id
                            comments(first: 100) {
                                nodes { id }
                            }
                        }
                    }
                }
            }
        }
    }
    """
    result = await execute_graphql(query, {"id": comment_id}, token, url=url)
    node = result.get("data", {}).get("node")
    if not node:
        raise ValueError(f"Comment {comment_id} not found")

    pr = node.get("pullRequest")
    if not pr:
        raise ValueError(f"Node {comment_id} is not a PullRequestReviewComment")

    for thread in pr["reviewThreads"]["nodes"]:
        for comment in thread["comments"]["nodes"]:
            if comment["id"] == comment_id:
                return thread["id"]

    raise ValueError(f"Could not find review thread for comment {comment_id}")


async def _handle_review_thread(
    action: str, comment_id: str, token: str, *, url: str | None = None,
) -> dict:
    """Handle `pr review-thread resolve|unresolve <comment-id>`."""
    try:
        thread_id = await _get_thread_id_for_comment(comment_id, token, url=url)
    except ValueError as e:
        return {"exit_code": 1, "stdout": "", "stderr": str(e)}

    if action == "resolve":
        mutation = """
        mutation($threadId: ID!) {
            resolveReviewThread(input: {threadId: $threadId}) {
                thread { isResolved }
            }
        }
        """
    else:
        mutation = """
        mutation($threadId: ID!) {
            unresolveReviewThread(input: {threadId: $threadId}) {
                thread { isResolved }
            }
        }
        """

    try:
        await execute_graphql(mutation, {"threadId": thread_id}, token, url=url)
    except ValueError as e:
        return {"exit_code": 1, "stdout": "", "stderr": str(e)}

    verb = "Resolved" if action == "resolve" else "Unresolved"
    return {"exit_code": 0, "stdout": "", "stderr": f"{verb} review thread"}
