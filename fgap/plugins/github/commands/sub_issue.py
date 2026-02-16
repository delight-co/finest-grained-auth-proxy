"""Sub-issue command: GitHub Sub-Issues via GraphQL API.

gh CLI doesn't have native sub-issue support, so all subcommands
are handled here (nothing falls through to subprocess).
"""

from ..graphql import execute_graphql, get_issue_node_id

_GRAPHQL_URL = None


async def execute(args: list[str], resource: str, credential: dict, *, url: str | None = None) -> dict:
    """Execute sub-issue command. Always returns a result dict (never None)."""
    url = url or _GRAPHQL_URL

    if not args:
        return _err("sub-issue subcommand required")

    owner, repo = resource.split("/", 1)
    token = credential["env"]["GH_TOKEN"]
    subcmd = args[0]
    rest = args[1:]

    try:
        if subcmd == "list":
            if not rest:
                return _err("issue number required")
            return await _list_sub_issues(owner, repo, int(rest[0]), token, url)
        elif subcmd == "parent":
            if not rest:
                return _err("issue number required")
            return await _get_parent(owner, repo, int(rest[0]), token, url)
        elif subcmd == "add":
            if len(rest) < 2:
                return _err("parent and child issue numbers required")
            return await _add_sub_issue(owner, repo, int(rest[0]), int(rest[1]), token, url)
        elif subcmd == "remove":
            if len(rest) < 2:
                return _err("parent and child issue numbers required")
            return await _remove_sub_issue(owner, repo, int(rest[0]), int(rest[1]), token, url)
        elif subcmd == "reorder":
            if len(rest) < 2:
                return _err("parent and child issue numbers required")
            before, after = _parse_reorder_args(rest[2:])
            if not before and not after:
                return _err("--before or --after required")
            return await _reorder_sub_issue(
                owner, repo, int(rest[0]), int(rest[1]), before, after, token, url,
            )
        else:
            return _err(f"Unknown sub-issue subcommand: {subcmd}")
    except ValueError as e:
        return _err(str(e))


def _err(msg: str) -> dict:
    return {"exit_code": 1, "stdout": "", "stderr": msg}


# =============================================================================
# Argument Parsing
# =============================================================================


def _parse_reorder_args(args: list[str]) -> tuple[int | None, int | None]:
    """Parse --before and --after from args."""
    before = after = None
    i = 0
    while i < len(args):
        if args[i] == "--before" and i + 1 < len(args):
            before = int(args[i + 1])
            i += 2
        elif args[i] == "--after" and i + 1 < len(args):
            after = int(args[i + 1])
            i += 2
        else:
            i += 1
    return before, after


# =============================================================================
# GraphQL Operations
# =============================================================================


_SUB_ISSUES_HEADER = {"GraphQL-Features": "sub_issues"}


async def _list_sub_issues(
    owner: str, repo: str, issue_number: int, token: str, url: str | None,
) -> dict:
    query = """
    query($owner: String!, $repo: String!, $number: Int!) {
        repository(owner: $owner, name: $repo) {
            issue(number: $number) {
                subIssues(first: 50) {
                    nodes { number title state }
                }
            }
        }
    }
    """
    result = await execute_graphql(
        query, {"owner": owner, "repo": repo, "number": issue_number}, token,
        extra_headers=_SUB_ISSUES_HEADER, url=url,
    )
    issue = result.get("data", {}).get("repository", {}).get("issue")
    if not issue:
        raise ValueError(f"Issue #{issue_number} not found in {owner}/{repo}")

    nodes = issue.get("subIssues", {}).get("nodes", [])
    lines = [f"{n['number']}\t{n['state']}\t{n['title']}" for n in nodes]
    return {"exit_code": 0, "stdout": "\n".join(lines), "stderr": ""}


async def _get_parent(
    owner: str, repo: str, issue_number: int, token: str, url: str | None,
) -> dict:
    query = """
    query($owner: String!, $repo: String!, $number: Int!) {
        repository(owner: $owner, name: $repo) {
            issue(number: $number) {
                parent { number title state }
            }
        }
    }
    """
    result = await execute_graphql(
        query, {"owner": owner, "repo": repo, "number": issue_number}, token,
        extra_headers=_SUB_ISSUES_HEADER, url=url,
    )
    issue = result.get("data", {}).get("repository", {}).get("issue")
    if not issue:
        raise ValueError(f"Issue #{issue_number} not found in {owner}/{repo}")

    parent = issue.get("parent")
    if parent:
        stdout = f"{parent['number']}\t{parent['state']}\t{parent['title']}"
    else:
        stdout = "No parent issue"
    return {"exit_code": 0, "stdout": stdout, "stderr": ""}


async def _add_sub_issue(
    owner: str, repo: str, parent_number: int, child_number: int,
    token: str, url: str | None,
) -> dict:
    issue_id = await get_issue_node_id(owner, repo, parent_number, token, url=url)
    sub_issue_id = await get_issue_node_id(owner, repo, child_number, token, url=url)

    mutation = """
    mutation($issueId: ID!, $subIssueId: ID!) {
        addSubIssue(input: {issueId: $issueId, subIssueId: $subIssueId}) {
            issue { number }
            subIssue { number }
        }
    }
    """
    await execute_graphql(
        mutation, {"issueId": issue_id, "subIssueId": sub_issue_id}, token,
        extra_headers=_SUB_ISSUES_HEADER, url=url,
    )
    return {
        "exit_code": 0,
        "stdout": f"Added #{child_number} as sub-issue of #{parent_number}",
        "stderr": "",
    }


async def _remove_sub_issue(
    owner: str, repo: str, parent_number: int, child_number: int,
    token: str, url: str | None,
) -> dict:
    issue_id = await get_issue_node_id(owner, repo, parent_number, token, url=url)
    sub_issue_id = await get_issue_node_id(owner, repo, child_number, token, url=url)

    mutation = """
    mutation($issueId: ID!, $subIssueId: ID!) {
        removeSubIssue(input: {issueId: $issueId, subIssueId: $subIssueId}) {
            issue { number }
            subIssue { number }
        }
    }
    """
    await execute_graphql(
        mutation, {"issueId": issue_id, "subIssueId": sub_issue_id}, token,
        extra_headers=_SUB_ISSUES_HEADER, url=url,
    )
    return {
        "exit_code": 0,
        "stdout": f"Removed #{child_number} from #{parent_number}",
        "stderr": "",
    }


async def _reorder_sub_issue(
    owner: str, repo: str, parent_number: int, child_number: int,
    before_number: int | None, after_number: int | None,
    token: str, url: str | None,
) -> dict:
    issue_id = await get_issue_node_id(owner, repo, parent_number, token, url=url)
    sub_issue_id = await get_issue_node_id(owner, repo, child_number, token, url=url)

    before_id = None
    after_id = None
    if before_number:
        before_id = await get_issue_node_id(owner, repo, before_number, token, url=url)
    if after_number:
        after_id = await get_issue_node_id(owner, repo, after_number, token, url=url)

    mutation = """
    mutation($issueId: ID!, $subIssueId: ID!, $beforeId: ID, $afterId: ID) {
        reprioritizeSubIssue(input: {issueId: $issueId, subIssueId: $subIssueId, beforeId: $beforeId, afterId: $afterId}) {
            issue { number }
        }
    }
    """
    await execute_graphql(mutation, {
        "issueId": issue_id,
        "subIssueId": sub_issue_id,
        "beforeId": before_id,
        "afterId": after_id,
    }, token, extra_headers=_SUB_ISSUES_HEADER, url=url)

    return {"exit_code": 0, "stdout": "Reordered", "stderr": ""}
