import aiohttp


async def execute_graphql(
    query: str,
    variables: dict,
    token: str,
    extra_headers: dict | None = None,
    *,
    url: str | None = None,
) -> dict:
    """Execute a GraphQL query against the GitHub API.

    Args:
        query: GraphQL query or mutation string.
        variables: Variables for the query.
        token: Personal access token.
        extra_headers: Additional headers (e.g. GraphQL-Features).
        url: Override API URL (for testing).

    Returns:
        Response data dict.

    Raises:
        ValueError: If GraphQL returns errors.
    """
    url = url or "https://api.github.com/graphql"

    headers = {
        "Authorization": f"bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "fgap",
    }
    if extra_headers:
        headers.update(extra_headers)

    body = {"query": query}
    if variables:
        body["variables"] = variables

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=body, headers=headers, timeout=timeout) as resp:
            result = await resp.json()
            if "errors" in result:
                raise ValueError(f"GraphQL error: {result['errors']}")
            return result


async def get_repository_id(
    owner: str, repo: str, token: str, *, url: str | None = None,
) -> str:
    """Get repository node ID."""
    query = """
    query($owner: String!, $repo: String!) {
        repository(owner: $owner, name: $repo) {
            id
        }
    }
    """
    result = await execute_graphql(
        query, {"owner": owner, "repo": repo}, token, url=url,
    )
    return result["data"]["repository"]["id"]


async def get_issue_node_id(
    owner: str, repo: str, issue_number: int, token: str,
    *, url: str | None = None,
) -> str:
    """Get issue node ID."""
    query = """
    query($owner: String!, $repo: String!, $number: Int!) {
        repository(owner: $owner, name: $repo) {
            issue(number: $number) {
                id
            }
        }
    }
    """
    result = await execute_graphql(
        query,
        {"owner": owner, "repo": repo, "number": issue_number},
        token,
        extra_headers={"GraphQL-Features": "sub_issues"},
        url=url,
    )
    issue = result.get("data", {}).get("repository", {}).get("issue")
    if not issue:
        raise ValueError(f"Issue #{issue_number} not found in {owner}/{repo}")
    return issue["id"]
