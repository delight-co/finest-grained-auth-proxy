import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from fgap.core.router import create_routes
from fgap.plugins.github import GitHubPlugin
from fgap.plugins.github.commands.pr import (
    _handle_edit,
    execute,
)


# =========================================================================
# Fallthrough tests
# =========================================================================


class TestExecuteFallthrough:
    async def test_empty_args(self):
        assert await execute([], "owner/repo", {"env": {"GH_TOKEN": "t"}}) is None

    async def test_unknown_subcommand(self):
        assert await execute(["list"], "owner/repo", {"env": {"GH_TOKEN": "t"}}) is None

    async def test_edit_without_old_new(self):
        assert await execute(["edit", "42"], "owner/repo", {"env": {"GH_TOKEN": "t"}}) is None


# =========================================================================
# Handler tests with mock GitHub API
# =========================================================================


@pytest.fixture
async def mock_github_api():
    """Mock GitHub REST API for pull requests."""
    app = web.Application()
    state = {
        "pulls": {},
        "requests": [],
    }

    async def handle_pr(request):
        number = request.match_info["number"]
        state["requests"].append({
            "method": request.method,
            "path": request.path,
        })
        if request.method == "GET":
            data = state["pulls"].get(number, {"body": ""})
            return web.json_response(data)
        if request.method == "PATCH":
            data = await request.json()
            state["pulls"][number] = data
            return web.json_response(data)
        return web.Response(status=405)

    app.router.add_route("*", "/repos/{owner}/{repo}/pulls/{number}", handle_pr)

    async with TestServer(app) as server:
        yield server, state


class TestHandleEdit:
    async def test_replaces_body(self, mock_github_api):
        server, state = mock_github_api
        state["pulls"]["42"] = {"body": "hello old world"}
        api_url = str(server.make_url(""))

        result = await _handle_edit(
            ["42", "--old", "old", "--new", "new"], "owner", "repo", "tok",
            api_url=api_url,
        )
        assert result["exit_code"] == 0
        assert state["pulls"]["42"]["body"] == "hello new world"

    async def test_replace_all(self, mock_github_api):
        server, state = mock_github_api
        state["pulls"]["1"] = {"body": "aaa bbb aaa"}
        api_url = str(server.make_url(""))

        result = await _handle_edit(
            ["1", "--old", "aaa", "--new", "ccc", "--replace-all"],
            "owner", "repo", "tok", api_url=api_url,
        )
        assert result["exit_code"] == 0
        assert state["pulls"]["1"]["body"] == "ccc bbb ccc"

    async def test_not_found_returns_error(self, mock_github_api):
        server, state = mock_github_api
        state["pulls"]["1"] = {"body": "hello world"}
        api_url = str(server.make_url(""))

        result = await _handle_edit(
            ["1", "--old", "missing", "--new", "x"], "owner", "repo", "tok",
            api_url=api_url,
        )
        assert result["exit_code"] == 1
        assert "not found" in result["stderr"]

    async def test_missing_pr_number(self, mock_github_api):
        server, _ = mock_github_api
        result = await _handle_edit(
            ["--old", "x", "--new", "y"], "owner", "repo", "tok",
            api_url=str(server.make_url("")),
        )
        assert result["exit_code"] == 1
        assert "PR number required" in result["stderr"]

    async def test_invalid_pr_number(self, mock_github_api):
        server, _ = mock_github_api
        result = await _handle_edit(
            ["abc", "--old", "x", "--new", "y"], "owner", "repo", "tok",
            api_url=str(server.make_url("")),
        )
        assert result["exit_code"] == 1
        assert "Invalid PR number" in result["stderr"]

    async def test_title_included_in_patch(self, mock_github_api):
        server, state = mock_github_api
        state["pulls"]["42"] = {"body": "hello old world"}
        api_url = str(server.make_url(""))

        result = await _handle_edit(
            ["42", "--old", "old", "--new", "new", "--title", "New Title"],
            "owner", "repo", "tok", api_url=api_url,
        )
        assert result["exit_code"] == 0
        assert state["pulls"]["42"]["body"] == "hello new world"
        assert state["pulls"]["42"]["title"] == "New Title"

    async def test_title_omitted_when_not_specified(self, mock_github_api):
        server, state = mock_github_api
        state["pulls"]["42"] = {"body": "hello old world"}
        api_url = str(server.make_url(""))

        result = await _handle_edit(
            ["42", "--old", "old", "--new", "new"], "owner", "repo", "tok",
            api_url=api_url,
        )
        assert result["exit_code"] == 0
        assert "title" not in state["pulls"]["42"]

    async def test_null_body_treated_as_empty(self, mock_github_api):
        server, state = mock_github_api
        state["pulls"]["1"] = {"body": None}
        api_url = str(server.make_url(""))

        result = await _handle_edit(
            ["1", "--old", "x", "--new", "y"], "owner", "repo", "tok",
            api_url=api_url,
        )
        assert result["exit_code"] == 1
        assert "not found" in result["stderr"]

    async def test_rest_calls_correct_endpoints(self, mock_github_api):
        server, state = mock_github_api
        state["pulls"]["7"] = {"body": "old text"}
        api_url = str(server.make_url(""))

        await _handle_edit(
            ["7", "--old", "old", "--new", "new"], "own", "rep", "tok",
            api_url=api_url,
        )
        assert state["requests"][0]["method"] == "GET"
        assert "/own/rep/pulls/7" in state["requests"][0]["path"]
        assert state["requests"][1]["method"] == "PATCH"


# =========================================================================
# Integration: via /cli endpoint
# =========================================================================


class TestPrCommandIntegration:
    async def test_edit_via_cli_endpoint(self, mock_github_api):
        server, state = mock_github_api
        state["pulls"]["7"] = {"body": "before change"}

        plugin = GitHubPlugin()
        config = {
            "plugins": {
                "github": {
                    "credentials": [{"token": "tok", "resources": ["*"]}],
                    "_api_url": str(server.make_url("")),
                }
            }
        }

        import fgap.plugins.github.commands.pr as pr_mod
        original_url = pr_mod._API_URL
        pr_mod._API_URL = str(server.make_url(""))
        try:
            app = create_routes(config, {"github": plugin})
            async with TestClient(TestServer(app)) as client:
                resp = await client.post("/cli", json={
                    "tool": "gh",
                    "args": ["pr", "edit", "7", "--old", "before", "--new", "after"],
                    "resource": "owner/repo",
                })
                assert resp.status == 200
                data = await resp.json()
                assert data["exit_code"] == 0
                assert state["pulls"]["7"]["body"] == "after change"
        finally:
            pr_mod._API_URL = original_url

    async def test_fallthrough_to_cli(self):
        """pr list (no --old/--new) falls through to gh subprocess."""
        plugin = GitHubPlugin()
        config = {
            "plugins": {
                "github": {
                    "credentials": [{"token": "tok", "resources": ["*"]}],
                }
            }
        }
        app = create_routes(config, {"github": plugin})
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/cli", json={
                "tool": "gh",
                "args": ["pr", "list"],
                "resource": "owner/repo",
            })
            assert resp.status == 200
            data = await resp.json()
            assert "exit_code" in data
