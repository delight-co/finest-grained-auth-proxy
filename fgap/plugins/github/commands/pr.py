"""PR command: partial body replacement and review thread resolution.

Handles:
- pr edit <number> --old "..." --new "..." [--replace-all] [--title "..."]
- pr comment edit <comment-id> --old "..." --new "..." [--replace-all]
- pr review-thread resolve <comment-id>
- pr review-thread unresolve <comment-id>

Everything else falls through to gh CLI (returns None).
"""

from .issue import (
    _COMMENT_EDIT_HELP,
    _COMMENT_EXTRA_HELP,
    _EDIT_EXTRA_HELP,
    _github_rest,
    _handle_comment_edit,
    _has_help_flag,
    _has_old_and_new,
    _help_with_extra,
    _parse_edit_args,
    _partial_replace,
)
from ..graphql import execute_graphql

_API_URL = "https://api.github.com"

_REVIEW_THREAD_HELP = """\
Resolve or unresolve a PR review thread by comment ID.

USAGE
  gh pr review-thread resolve <comment-id>
  gh pr review-thread unresolve <comment-id>

The comment-id is a GraphQL node ID (e.g. PRRC_kwDO...).
Obtain it from: gh api repos/OWNER/REPO/pulls/NUMBER/comments --jq '.[].node_id'
"""

_PR_EXTRA_HELP = """
FGAP CUSTOM COMMANDS
  review-thread resolve|unresolve <comment-id>   Resolve/unresolve a review thread
"""[1:]  # strip leading newline


async def execute(args: list[str], resource: str, credential: dict) -> dict | None:
    """Execute pr command. Returns None to fall through to gh CLI."""
    if not args:
        return None

    subcmd = args[0]
    rest = args[1:]

    # Handle help before accessing resource/credential (help works without a repo)
    if _has_help_flag([subcmd]):
        return await _help_with_extra("gh", ["pr", "--help"], _PR_EXTRA_HELP)

    if subcmd == "edit" and _has_help_flag(rest):
        return await _help_with_extra("gh", ["pr", "edit", "--help"], _EDIT_EXTRA_HELP)

    if subcmd == "comment":
        if len(rest) > 0 and rest[0] == "edit" and _has_help_flag(rest[1:]):
            return {"exit_code": 0, "stdout": _COMMENT_EDIT_HELP, "stderr": ""}
        if _has_help_flag(rest):
            return await _help_with_extra("gh", ["pr", "comment", "--help"], _COMMENT_EXTRA_HELP)

    if subcmd == "review-thread" and (not rest or _has_help_flag(rest)):
        return {"exit_code": 0, "stdout": _REVIEW_THREAD_HELP, "stderr": ""}

    owner, repo = resource.split("/", 1)
    token = credential["env"]["GH_TOKEN"]

    if subcmd == "edit":
        if _has_old_and_new(rest):
            return await _handle_edit(rest, owner, repo, token)

    if subcmd == "comment":
        if len(rest) > 0 and rest[0] == "edit" and _has_old_and_new(rest[1:]):
            return await _handle_comment_edit(rest[1:], owner, repo, token)

    if subcmd == "review-thread":
        if rest[0] in ("resolve", "unresolve") and len(rest) > 1:
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


async def _thread_has_comment(
    thread_id: str, comment_id: str, token: str, *, url: str | None = None,
) -> bool:
    """Check remaining comment pages of a thread for a specific comment."""
    query = """
    query($threadId: ID!, $cursor: String) {
        node(id: $threadId) {
            ... on PullRequestReviewThread {
                comments(first: 100, after: $cursor) {
                    pageInfo { hasNextPage endCursor }
                    nodes { id }
                }
            }
        }
    }
    """
    cursor = None
    while True:
        result = await execute_graphql(
            query, {"threadId": thread_id, "cursor": cursor}, token, url=url,
        )
        comments = result["data"]["node"]["comments"]
        if any(c["id"] == comment_id for c in comments["nodes"]):
            return True
        if not comments["pageInfo"]["hasNextPage"]:
            return False
        cursor = comments["pageInfo"]["endCursor"]


async def _get_thread_id_for_comment(
    comment_id: str, token: str, *, url: str | None = None,
) -> str:
    """Resolve a review comment node ID to its parent review thread ID.

    Fetches the PR's review threads via the comment's parent PR,
    then finds the thread containing the given comment.
    Paginates both review threads and comments within each thread.
    """
    threads_cursor = None
    validated = False

    while True:
        query = """
        query($id: ID!, $threadsCursor: String) {
            node(id: $id) {
                ... on PullRequestReviewComment {
                    pullRequest {
                        reviewThreads(first: 100, after: $threadsCursor) {
                            pageInfo { hasNextPage endCursor }
                            nodes {
                                id
                                comments(first: 100) {
                                    pageInfo { hasNextPage }
                                    nodes { id }
                                }
                            }
                        }
                    }
                }
            }
        }
        """
        result = await execute_graphql(
            query,
            {"id": comment_id, "threadsCursor": threads_cursor},
            token,
            url=url,
        )
        node = result.get("data", {}).get("node")

        if not validated:
            if not node:
                raise ValueError(f"Comment {comment_id} not found")
            if not node.get("pullRequest"):
                raise ValueError(
                    f"Node {comment_id} is not a PullRequestReviewComment"
                )
            validated = True

        threads_data = node["pullRequest"]["reviewThreads"]

        for thread in threads_data["nodes"]:
            comments = thread["comments"]
            if any(c["id"] == comment_id for c in comments["nodes"]):
                return thread["id"]
            if comments["pageInfo"]["hasNextPage"]:
                if await _thread_has_comment(
                    thread["id"], comment_id, token, url=url,
                ):
                    return thread["id"]

        if not threads_data["pageInfo"]["hasNextPage"]:
            break
        threads_cursor = threads_data["pageInfo"]["endCursor"]

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
