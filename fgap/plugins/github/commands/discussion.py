"""Discussion command: GitHub Discussions via GraphQL API.

gh CLI doesn't have native discussion support, so all subcommands
are handled here (nothing falls through to subprocess).
"""

from ..graphql import execute_graphql, get_repository_id

_GRAPHQL_URL = None


async def execute(args: list[str], resource: str, credential: dict, *, url: str | None = None) -> dict:
    """Execute discussion command. Always returns a result dict (never None)."""
    url = url or _GRAPHQL_URL

    if not args:
        return _err("discussion subcommand required")

    owner, repo = resource.split("/", 1)
    token = credential["env"]["GH_TOKEN"]
    subcmd = args[0]
    rest = args[1:]

    try:
        if subcmd == "list":
            return await _list_discussions(owner, repo, token, url)
        elif subcmd == "view":
            if not rest:
                return _err("discussion number required")
            return await _view_discussion(owner, repo, int(rest[0]), token, url)
        elif subcmd == "create":
            title, body, category = _parse_create_args(rest)
            return await _create_discussion(owner, repo, title, body, category, token, url)
        elif subcmd == "edit":
            if not rest:
                return _err("discussion number required")
            title, body = _parse_edit_args(rest[1:])
            return await _update_discussion(owner, repo, int(rest[0]), title, body, token, url)
        elif subcmd == "close":
            if not rest:
                return _err("discussion number required")
            return await _close_discussion(owner, repo, int(rest[0]), token, url)
        elif subcmd == "reopen":
            if not rest:
                return _err("discussion number required")
            return await _reopen_discussion(owner, repo, int(rest[0]), token, url)
        elif subcmd == "delete":
            if not rest:
                return _err("discussion number required")
            return await _delete_discussion(owner, repo, int(rest[0]), token, url)
        elif subcmd == "comment":
            return await _handle_comment(rest, owner, repo, token, url)
        elif subcmd == "answer":
            if not rest:
                return _err("comment_id required")
            return await _mark_answer(rest[0], token, url)
        elif subcmd == "unanswer":
            if not rest:
                return _err("comment_id required")
            return await _unmark_answer(rest[0], token, url)
        elif subcmd == "poll":
            return await _handle_poll(rest, token, url)
        else:
            return _err(f"Unknown discussion subcommand: {subcmd}")
    except ValueError as e:
        return _err(str(e))


def _err(msg: str) -> dict:
    return {"exit_code": 1, "stdout": "", "stderr": msg}


# =============================================================================
# Argument Parsing
# =============================================================================


def _parse_create_args(args: list[str]) -> tuple[str, str, str]:
    """Parse --title, --body, --category from args."""
    title = body = category = None
    i = 0
    while i < len(args):
        if args[i] in ("--title", "-t") and i + 1 < len(args):
            title = args[i + 1]
            i += 2
        elif args[i] in ("--body", "-b") and i + 1 < len(args):
            body = args[i + 1]
            i += 2
        elif args[i] in ("--category", "-c") and i + 1 < len(args):
            category = args[i + 1]
            i += 2
        else:
            i += 1

    if not title:
        raise ValueError("--title is required")
    if not body:
        raise ValueError("--body is required")
    if not category:
        raise ValueError("--category is required")
    return title, body, category


def _parse_edit_args(args: list[str]) -> tuple[str | None, str | None]:
    """Parse --title, --body from args."""
    title = body = None
    i = 0
    while i < len(args):
        if args[i] in ("--title", "-t") and i + 1 < len(args):
            title = args[i + 1]
            i += 2
        elif args[i] in ("--body", "-b") and i + 1 < len(args):
            body = args[i + 1]
            i += 2
        else:
            i += 1

    if not title and not body:
        raise ValueError("--title or --body is required")
    return title, body


def _parse_comment_body(args: list[str]) -> str:
    """Parse --body from args."""
    i = 0
    while i < len(args):
        if args[i] in ("--body", "-b") and i + 1 < len(args):
            return args[i + 1]
        i += 1
    raise ValueError("--body is required")


def _parse_add_comment_args(args: list[str]) -> tuple[str, str | None]:
    """Parse --body and --reply-to from args."""
    body = reply_to = None
    i = 0
    while i < len(args):
        if args[i] in ("--body", "-b") and i + 1 < len(args):
            body = args[i + 1]
            i += 2
        elif args[i] == "--reply-to" and i + 1 < len(args):
            reply_to = args[i + 1]
            i += 2
        else:
            i += 1

    if not body:
        raise ValueError("--body is required")
    return body, reply_to


async def _handle_comment(
    args: list[str], owner: str, repo: str, token: str, url: str | None,
) -> dict:
    if not args:
        return _err("discussion number or 'edit'/'delete' required")

    if args[0] == "delete":
        if len(args) < 2:
            return _err("comment_id required")
        return await _delete_comment(args[1], token, url)

    if args[0] == "edit":
        if len(args) < 2:
            return _err("comment_id required")
        body = _parse_comment_body(args[2:])
        return await _update_comment(args[1], body, token, url)

    # Add comment: comment <number> --body "..."
    number = int(args[0])
    body, reply_to = _parse_add_comment_args(args[1:])
    return await _add_comment(owner, repo, number, body, reply_to, token, url)


async def _handle_poll(args: list[str], token: str, url: str | None) -> dict:
    if not args:
        return _err("poll subcommand required (vote)")

    if args[0] == "vote":
        if len(args) < 2:
            return _err("option_id required")
        return await _poll_vote(args[1], token, url)

    return _err(f"Unknown poll subcommand: {args[0]}")


# =============================================================================
# GraphQL Helpers
# =============================================================================


async def _get_discussion_category_id(
    owner: str, repo: str, category_name: str, token: str, url: str | None,
) -> str:
    query = """
    query($owner: String!, $repo: String!) {
        repository(owner: $owner, name: $repo) {
            discussionCategories(first: 100) {
                nodes { id name slug }
            }
        }
    }
    """
    result = await execute_graphql(
        query, {"owner": owner, "repo": repo}, token, url=url,
    )
    categories = result["data"]["repository"]["discussionCategories"]["nodes"]
    for cat in categories:
        if cat["name"].lower() == category_name.lower() or cat["slug"].lower() == category_name.lower():
            return cat["id"]
    available = [c["name"] for c in categories]
    raise ValueError(f"Category '{category_name}' not found. Available: {available}")


async def _get_discussion_node_id(
    owner: str, repo: str, number: int, token: str, url: str | None,
) -> str:
    query = """
    query($owner: String!, $repo: String!, $number: Int!) {
        repository(owner: $owner, name: $repo) {
            discussion(number: $number) { id }
        }
    }
    """
    result = await execute_graphql(
        query, {"owner": owner, "repo": repo, "number": number}, token, url=url,
    )
    discussion = result["data"]["repository"]["discussion"]
    if not discussion:
        raise ValueError(f"Discussion #{number} not found")
    return discussion["id"]


# =============================================================================
# GraphQL Operations
# =============================================================================


async def _list_discussions(
    owner: str, repo: str, token: str, url: str | None,
) -> dict:
    query = """
    query($owner: String!, $repo: String!) {
        repository(owner: $owner, name: $repo) {
            discussions(first: 30, orderBy: {field: CREATED_AT, direction: DESC}) {
                nodes {
                    number
                    title
                    author { login }
                    createdAt
                    category { name }
                    comments { totalCount }
                }
            }
        }
    }
    """
    result = await execute_graphql(
        query, {"owner": owner, "repo": repo}, token, url=url,
    )
    discussions = result["data"]["repository"]["discussions"]["nodes"]

    lines = []
    for d in discussions:
        author = d["author"]["login"] if d["author"] else "ghost"
        comments = d["comments"]["totalCount"]
        category = d["category"]["name"] if d["category"] else ""
        lines.append(f"#{d['number']}\t{d['title']}\t{author}\t{category}\t{comments} comments")

    return {"exit_code": 0, "stdout": "\n".join(lines), "stderr": ""}


async def _view_discussion(
    owner: str, repo: str, number: int, token: str, url: str | None,
) -> dict:
    query = """
    query($owner: String!, $repo: String!, $number: Int!) {
        repository(owner: $owner, name: $repo) {
            discussion(number: $number) {
                number
                title
                body
                author { login }
                createdAt
                category { name }
                url
                comments(first: 50) {
                    nodes {
                        id
                        author { login }
                        body
                        createdAt
                    }
                }
            }
        }
    }
    """
    result = await execute_graphql(
        query, {"owner": owner, "repo": repo, "number": number}, token, url=url,
    )
    d = result["data"]["repository"]["discussion"]
    if not d:
        raise ValueError(f"Discussion #{number} not found")

    author = d["author"]["login"] if d["author"] else "ghost"
    lines = [
        f"title:\t{d['title']}",
        f"number:\t{d['number']}",
        f"author:\t{author}",
        f"category:\t{d['category']['name'] if d['category'] else ''}",
        f"url:\t{d['url']}",
        f"created:\t{d['createdAt']}",
        "",
        "--- BODY ---",
        d["body"] or "(empty)",
        "",
        "--- COMMENTS ---",
    ]
    for c in d["comments"]["nodes"]:
        c_author = c["author"]["login"] if c["author"] else "ghost"
        lines.append(f"\n[{c['id']}] {c_author} at {c['createdAt']}:")
        lines.append(c["body"])

    return {"exit_code": 0, "stdout": "\n".join(lines), "stderr": ""}


async def _create_discussion(
    owner: str, repo: str, title: str, body: str, category: str,
    token: str, url: str | None,
) -> dict:
    repo_id = await get_repository_id(owner, repo, token, url=url)
    category_id = await _get_discussion_category_id(owner, repo, category, token, url)

    mutation = """
    mutation($repositoryId: ID!, $categoryId: ID!, $title: String!, $body: String!) {
        createDiscussion(input: {repositoryId: $repositoryId, categoryId: $categoryId, title: $title, body: $body}) {
            discussion { number url }
        }
    }
    """
    result = await execute_graphql(mutation, {
        "repositoryId": repo_id,
        "categoryId": category_id,
        "title": title,
        "body": body,
    }, token, url=url)
    d = result["data"]["createDiscussion"]["discussion"]

    return {"exit_code": 0, "stdout": d["url"], "stderr": f"Created discussion #{d['number']}"}


async def _update_discussion(
    owner: str, repo: str, number: int,
    title: str | None, body: str | None,
    token: str, url: str | None,
) -> dict:
    discussion_id = await _get_discussion_node_id(owner, repo, number, token, url)

    mutation = """
    mutation($discussionId: ID!, $title: String, $body: String) {
        updateDiscussion(input: {discussionId: $discussionId, title: $title, body: $body}) {
            discussion { number url }
        }
    }
    """
    result = await execute_graphql(mutation, {
        "discussionId": discussion_id,
        "title": title,
        "body": body,
    }, token, url=url)
    d = result["data"]["updateDiscussion"]["discussion"]

    return {"exit_code": 0, "stdout": d["url"], "stderr": f"Updated discussion #{d['number']}"}


async def _close_discussion(
    owner: str, repo: str, number: int, token: str, url: str | None,
) -> dict:
    discussion_id = await _get_discussion_node_id(owner, repo, number, token, url)

    mutation = """
    mutation($discussionId: ID!) {
        closeDiscussion(input: {discussionId: $discussionId}) {
            discussion { number url }
        }
    }
    """
    result = await execute_graphql(
        mutation, {"discussionId": discussion_id}, token, url=url,
    )
    d = result["data"]["closeDiscussion"]["discussion"]

    return {"exit_code": 0, "stdout": d["url"], "stderr": f"Closed discussion #{d['number']}"}


async def _reopen_discussion(
    owner: str, repo: str, number: int, token: str, url: str | None,
) -> dict:
    discussion_id = await _get_discussion_node_id(owner, repo, number, token, url)

    mutation = """
    mutation($discussionId: ID!) {
        reopenDiscussion(input: {discussionId: $discussionId}) {
            discussion { number url }
        }
    }
    """
    result = await execute_graphql(
        mutation, {"discussionId": discussion_id}, token, url=url,
    )
    d = result["data"]["reopenDiscussion"]["discussion"]

    return {"exit_code": 0, "stdout": d["url"], "stderr": f"Reopened discussion #{d['number']}"}


async def _delete_discussion(
    owner: str, repo: str, number: int, token: str, url: str | None,
) -> dict:
    discussion_id = await _get_discussion_node_id(owner, repo, number, token, url)

    mutation = """
    mutation($discussionId: ID!) {
        deleteDiscussion(input: {id: $discussionId}) {
            discussion { number }
        }
    }
    """
    result = await execute_graphql(
        mutation, {"discussionId": discussion_id}, token, url=url,
    )
    d = result["data"]["deleteDiscussion"]["discussion"]

    return {"exit_code": 0, "stdout": "", "stderr": f"Deleted discussion #{d['number']}"}


async def _add_comment(
    owner: str, repo: str, number: int, body: str, reply_to: str | None,
    token: str, url: str | None,
) -> dict:
    discussion_id = await _get_discussion_node_id(owner, repo, number, token, url)

    mutation = """
    mutation($discussionId: ID!, $body: String!, $replyToId: ID) {
        addDiscussionComment(input: {discussionId: $discussionId, body: $body, replyToId: $replyToId}) {
            comment { id url }
        }
    }
    """
    result = await execute_graphql(mutation, {
        "discussionId": discussion_id,
        "body": body,
        "replyToId": reply_to,
    }, token, url=url)
    c = result["data"]["addDiscussionComment"]["comment"]

    return {"exit_code": 0, "stdout": c["url"], "stderr": f"Added comment {c['id']}"}


async def _update_comment(
    comment_id: str, body: str, token: str, url: str | None,
) -> dict:
    mutation = """
    mutation($commentId: ID!, $body: String!) {
        updateDiscussionComment(input: {commentId: $commentId, body: $body}) {
            comment { id url }
        }
    }
    """
    result = await execute_graphql(
        mutation, {"commentId": comment_id, "body": body}, token, url=url,
    )
    c = result["data"]["updateDiscussionComment"]["comment"]

    return {"exit_code": 0, "stdout": c["url"], "stderr": f"Updated comment {c['id']}"}


async def _delete_comment(comment_id: str, token: str, url: str | None) -> dict:
    mutation = """
    mutation($commentId: ID!) {
        deleteDiscussionComment(input: {id: $commentId}) {
            comment { id }
        }
    }
    """
    result = await execute_graphql(
        mutation, {"commentId": comment_id}, token, url=url,
    )
    c = result["data"]["deleteDiscussionComment"]["comment"]

    return {"exit_code": 0, "stdout": "", "stderr": f"Deleted comment {c['id']}"}


async def _mark_answer(comment_id: str, token: str, url: str | None) -> dict:
    mutation = """
    mutation($commentId: ID!) {
        markDiscussionCommentAsAnswer(input: {id: $commentId}) {
            discussion { number url }
        }
    }
    """
    result = await execute_graphql(
        mutation, {"commentId": comment_id}, token, url=url,
    )
    d = result["data"]["markDiscussionCommentAsAnswer"]["discussion"]

    return {"exit_code": 0, "stdout": d["url"], "stderr": f"Marked as answer in discussion #{d['number']}"}


async def _unmark_answer(comment_id: str, token: str, url: str | None) -> dict:
    mutation = """
    mutation($commentId: ID!) {
        unmarkDiscussionCommentAsAnswer(input: {id: $commentId}) {
            discussion { number url }
        }
    }
    """
    result = await execute_graphql(
        mutation, {"commentId": comment_id}, token, url=url,
    )
    d = result["data"]["unmarkDiscussionCommentAsAnswer"]["discussion"]

    return {"exit_code": 0, "stdout": d["url"], "stderr": f"Unmarked answer in discussion #{d['number']}"}


async def _poll_vote(option_id: str, token: str, url: str | None) -> dict:
    mutation = """
    mutation($optionId: ID!) {
        addDiscussionPollVote(input: {pollOptionId: $optionId}) {
            pollOption { id option totalVoteCount }
        }
    }
    """
    result = await execute_graphql(
        mutation, {"optionId": option_id}, token, url=url,
    )
    opt = result["data"]["addDiscussionPollVote"]["pollOption"]

    return {
        "exit_code": 0,
        "stdout": f"Voted for: {opt['option']} (total: {opt['totalVoteCount']})",
        "stderr": "",
    }
