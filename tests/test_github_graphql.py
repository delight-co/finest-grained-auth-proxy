import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from fgap.plugins.github.graphql import (
    execute_graphql,
    get_issue_node_id,
    get_repository_id,
)


@pytest.fixture
async def mock_graphql_server():
    """Mock GitHub GraphQL API."""
    app = web.Application()
    received = []

    async def handle(request):
        data = await request.json()
        received.append({"data": data, "headers": dict(request.headers)})

        query = data.get("query", "")
        variables = data.get("variables", {})

        if "error" in query:
            return web.json_response({"errors": [{"message": "test error"}]})

        if "issue" in query and "number" in str(variables):
            if variables.get("number") == 999:
                return web.json_response(
                    {"data": {"repository": {"issue": None}}},
                )
            return web.json_response(
                {"data": {"repository": {"issue": {"id": "I_abc"}}}},
            )

        if "repository" in query:
            return web.json_response(
                {"data": {"repository": {"id": "R_xyz"}}},
            )

        return web.json_response({"data": {"viewer": {"login": "testuser"}}})

    app.router.add_post("/graphql", handle)

    async with TestServer(app) as server:
        yield server, received


class TestExecuteGraphql:
    async def test_successful_query(self, mock_graphql_server):
        server, _ = mock_graphql_server
        url = str(server.make_url("/graphql"))
        result = await execute_graphql(
            "query { viewer { login } }", {}, "tok", url=url,
        )
        assert result["data"]["viewer"]["login"] == "testuser"

    async def test_auth_header_sent(self, mock_graphql_server):
        server, received = mock_graphql_server
        url = str(server.make_url("/graphql"))
        await execute_graphql("query { viewer { login } }", {}, "secret", url=url)
        assert received[0]["headers"]["Authorization"] == "bearer secret"

    async def test_extra_headers_sent(self, mock_graphql_server):
        server, received = mock_graphql_server
        url = str(server.make_url("/graphql"))
        await execute_graphql(
            "query { viewer { login } }", {}, "tok",
            extra_headers={"GraphQL-Features": "sub_issues"},
            url=url,
        )
        assert received[0]["headers"]["GraphQL-Features"] == "sub_issues"

    async def test_variables_sent(self, mock_graphql_server):
        server, received = mock_graphql_server
        url = str(server.make_url("/graphql"))
        await execute_graphql(
            "query($n: Int!) { f(n: $n) }", {"n": 42}, "tok", url=url,
        )
        assert received[0]["data"]["variables"] == {"n": 42}

    async def test_graphql_error_raises(self, mock_graphql_server):
        server, _ = mock_graphql_server
        url = str(server.make_url("/graphql"))
        with pytest.raises(ValueError, match="GraphQL error"):
            await execute_graphql("query { error }", {}, "tok", url=url)


class TestGetRepositoryId:
    async def test_returns_id(self, mock_graphql_server):
        server, _ = mock_graphql_server
        url = str(server.make_url("/graphql"))
        assert await get_repository_id("owner", "repo", "tok", url=url) == "R_xyz"


class TestGetIssueNodeId:
    async def test_returns_id(self, mock_graphql_server):
        server, _ = mock_graphql_server
        url = str(server.make_url("/graphql"))
        result = await get_issue_node_id("owner", "repo", 1, "tok", url=url)
        assert result == "I_abc"

    async def test_not_found_raises(self, mock_graphql_server):
        server, _ = mock_graphql_server
        url = str(server.make_url("/graphql"))
        with pytest.raises(ValueError, match="not found"):
            await get_issue_node_id("owner", "repo", 999, "tok", url=url)

    async def test_sends_sub_issues_header(self, mock_graphql_server):
        server, received = mock_graphql_server
        url = str(server.make_url("/graphql"))
        await get_issue_node_id("owner", "repo", 1, "tok", url=url)
        assert received[0]["headers"]["GraphQL-Features"] == "sub_issues"
