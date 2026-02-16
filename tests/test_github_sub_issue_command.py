import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from fgap.core.router import create_routes
from fgap.plugins.github import GitHubPlugin
from fgap.plugins.github.commands.sub_issue import (
    _parse_reorder_args,
    execute,
)

CRED = {"env": {"GH_TOKEN": "test-token"}}


# =========================================================================
# Pure logic tests
# =========================================================================


class TestParseReorderArgs:
    def test_before(self):
        b, a = _parse_reorder_args(["--before", "5"])
        assert b == 5
        assert a is None

    def test_after(self):
        b, a = _parse_reorder_args(["--after", "10"])
        assert b is None
        assert a == 10

    def test_both(self):
        b, a = _parse_reorder_args(["--before", "5", "--after", "10"])
        assert b == 5
        assert a == 10

    def test_empty(self):
        b, a = _parse_reorder_args([])
        assert b is None
        assert a is None


# =========================================================================
# Execute routing tests
# =========================================================================


class TestExecuteRouting:
    async def test_empty_args(self):
        result = await execute([], "o/r", CRED)
        assert result["exit_code"] == 1
        assert "subcommand required" in result["stderr"]

    async def test_unknown_subcommand(self):
        result = await execute(["nope"], "o/r", CRED)
        assert result["exit_code"] == 1
        assert "Unknown" in result["stderr"]

    async def test_list_missing_number(self):
        result = await execute(["list"], "o/r", CRED)
        assert result["exit_code"] == 1
        assert "number required" in result["stderr"]

    async def test_parent_missing_number(self):
        result = await execute(["parent"], "o/r", CRED)
        assert result["exit_code"] == 1

    async def test_add_missing_args(self):
        result = await execute(["add", "1"], "o/r", CRED)
        assert result["exit_code"] == 1
        assert "parent and child" in result["stderr"]

    async def test_remove_missing_args(self):
        result = await execute(["remove"], "o/r", CRED)
        assert result["exit_code"] == 1

    async def test_reorder_missing_position(self):
        result = await execute(["reorder", "1", "2"], "o/r", CRED)
        assert result["exit_code"] == 1
        assert "--before or --after" in result["stderr"]


# =========================================================================
# Handler tests with mock GraphQL server
# =========================================================================


@pytest.fixture
async def mock_graphql():
    """Mock GraphQL server that returns queued responses."""
    app = web.Application()
    state = {"responses": [], "requests": []}

    async def handle(request):
        data = await request.json()
        state["requests"].append(data)
        if not state["responses"]:
            return web.json_response({"data": {}})
        return web.json_response(state["responses"].pop(0))

    app.router.add_post("/graphql", handle)
    async with TestServer(app) as server:
        yield server, state


def _url(server) -> str:
    return str(server.make_url("/graphql"))


class TestListSubIssues:
    async def test_returns_formatted_list(self, mock_graphql):
        server, state = mock_graphql
        state["responses"].append({"data": {"repository": {"issue": {"subIssues": {"nodes": [
            {"number": 10, "title": "Child A", "state": "OPEN"},
            {"number": 11, "title": "Child B", "state": "CLOSED"},
        ]}}}}})

        result = await execute(["list", "5"], "owner/repo", CRED, url=_url(server))
        assert result["exit_code"] == 0
        assert "10" in result["stdout"]
        assert "Child A" in result["stdout"]
        assert "CLOSED" in result["stdout"]

    async def test_issue_not_found(self, mock_graphql):
        server, state = mock_graphql
        state["responses"].append({"data": {"repository": {"issue": None}}})

        result = await execute(["list", "999"], "owner/repo", CRED, url=_url(server))
        assert result["exit_code"] == 1
        assert "not found" in result["stderr"]

    async def test_empty_list(self, mock_graphql):
        server, state = mock_graphql
        state["responses"].append({"data": {"repository": {"issue": {"subIssues": {"nodes": []}}}}})

        result = await execute(["list", "1"], "owner/repo", CRED, url=_url(server))
        assert result["exit_code"] == 0
        assert result["stdout"] == ""


class TestGetParent:
    async def test_has_parent(self, mock_graphql):
        server, state = mock_graphql
        state["responses"].append({"data": {"repository": {"issue": {
            "parent": {"number": 1, "title": "Parent Issue", "state": "OPEN"},
        }}}})

        result = await execute(["parent", "5"], "owner/repo", CRED, url=_url(server))
        assert result["exit_code"] == 0
        assert "Parent Issue" in result["stdout"]

    async def test_no_parent(self, mock_graphql):
        server, state = mock_graphql
        state["responses"].append({"data": {"repository": {"issue": {"parent": None}}}})

        result = await execute(["parent", "5"], "owner/repo", CRED, url=_url(server))
        assert result["exit_code"] == 0
        assert "No parent" in result["stdout"]

    async def test_issue_not_found(self, mock_graphql):
        server, state = mock_graphql
        state["responses"].append({"data": {"repository": {"issue": None}}})

        result = await execute(["parent", "999"], "owner/repo", CRED, url=_url(server))
        assert result["exit_code"] == 1
        assert "not found" in result["stderr"]


class TestAddSubIssue:
    async def test_adds(self, mock_graphql):
        server, state = mock_graphql
        # get_issue_node_id for parent
        state["responses"].append({"data": {"repository": {"issue": {"id": "I_parent"}}}})
        # get_issue_node_id for child
        state["responses"].append({"data": {"repository": {"issue": {"id": "I_child"}}}})
        # addSubIssue mutation
        state["responses"].append({"data": {"addSubIssue": {
            "issue": {"number": 1}, "subIssue": {"number": 2},
        }}})

        result = await execute(["add", "1", "2"], "owner/repo", CRED, url=_url(server))
        assert result["exit_code"] == 0
        assert "#2" in result["stdout"]
        assert "#1" in result["stdout"]

        # Verify mutation variables
        mutation_req = state["requests"][2]
        assert mutation_req["variables"]["issueId"] == "I_parent"
        assert mutation_req["variables"]["subIssueId"] == "I_child"


class TestRemoveSubIssue:
    async def test_removes(self, mock_graphql):
        server, state = mock_graphql
        state["responses"].append({"data": {"repository": {"issue": {"id": "I_parent"}}}})
        state["responses"].append({"data": {"repository": {"issue": {"id": "I_child"}}}})
        state["responses"].append({"data": {"removeSubIssue": {
            "issue": {"number": 1}, "subIssue": {"number": 2},
        }}})

        result = await execute(["remove", "1", "2"], "owner/repo", CRED, url=_url(server))
        assert result["exit_code"] == 0
        assert "Removed" in result["stdout"]


class TestReorderSubIssue:
    async def test_reorder_with_before(self, mock_graphql):
        server, state = mock_graphql
        # parent
        state["responses"].append({"data": {"repository": {"issue": {"id": "I_parent"}}}})
        # child
        state["responses"].append({"data": {"repository": {"issue": {"id": "I_child"}}}})
        # before target
        state["responses"].append({"data": {"repository": {"issue": {"id": "I_before"}}}})
        # mutation
        state["responses"].append({"data": {"reprioritizeSubIssue": {"issue": {"number": 1}}}})

        result = await execute(
            ["reorder", "1", "2", "--before", "3"], "owner/repo", CRED, url=_url(server),
        )
        assert result["exit_code"] == 0
        assert "Reordered" in result["stdout"]

        mutation_req = state["requests"][3]
        assert mutation_req["variables"]["beforeId"] == "I_before"
        assert mutation_req["variables"]["afterId"] is None

    async def test_reorder_with_after(self, mock_graphql):
        server, state = mock_graphql
        state["responses"].append({"data": {"repository": {"issue": {"id": "I_parent"}}}})
        state["responses"].append({"data": {"repository": {"issue": {"id": "I_child"}}}})
        state["responses"].append({"data": {"repository": {"issue": {"id": "I_after"}}}})
        state["responses"].append({"data": {"reprioritizeSubIssue": {"issue": {"number": 1}}}})

        result = await execute(
            ["reorder", "1", "2", "--after", "3"], "owner/repo", CRED, url=_url(server),
        )
        assert result["exit_code"] == 0

        mutation_req = state["requests"][3]
        assert mutation_req["variables"]["afterId"] == "I_after"
        assert mutation_req["variables"]["beforeId"] is None


# =========================================================================
# Integration: via /cli endpoint
# =========================================================================


class TestSubIssueIntegration:
    async def test_list_via_cli_endpoint(self, mock_graphql):
        server, state = mock_graphql
        state["responses"].append({"data": {"repository": {"issue": {"subIssues": {"nodes": [
            {"number": 10, "title": "Child", "state": "OPEN"},
        ]}}}}})

        plugin = GitHubPlugin()
        config = {
            "plugins": {
                "github": {
                    "credentials": [{"token": "tok", "resources": ["*"]}],
                }
            }
        }

        import fgap.plugins.github.commands.sub_issue as si_mod
        original = si_mod._GRAPHQL_URL
        si_mod._GRAPHQL_URL = _url(server)
        try:
            app = create_routes(config, {"github": plugin})
            async with TestClient(TestServer(app)) as client:
                resp = await client.post("/cli", json={
                    "tool": "gh",
                    "args": ["sub-issue", "list", "5"],
                    "resource": "owner/repo",
                })
                assert resp.status == 200
                data = await resp.json()
                assert data["exit_code"] == 0
                assert "Child" in data["stdout"]
        finally:
            si_mod._GRAPHQL_URL = original
