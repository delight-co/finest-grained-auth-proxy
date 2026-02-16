import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from fgap.core.router import create_routes
from fgap.plugins.github import GitHubPlugin
from fgap.plugins.github.commands.issue import (
    _handle_comment_edit,
    _handle_edit,
    _parse_edit_args,
    _partial_replace,
    execute,
)


# =========================================================================
# Pure logic tests
# =========================================================================


class TestParseEditArgs:
    def test_basic(self):
        pos, old, new, ra = _parse_edit_args(["42", "--old", "x", "--new", "y"])
        assert pos == ["42"]
        assert old == "x"
        assert new == "y"
        assert ra is False

    def test_replace_all(self):
        _, _, _, ra = _parse_edit_args(["1", "--old", "a", "--new", "b", "--replace-all"])
        assert ra is True

    def test_old_missing_value(self):
        with pytest.raises(ValueError, match="--old requires"):
            _parse_edit_args(["1", "--old"])

    def test_new_missing_value(self):
        with pytest.raises(ValueError, match="--new requires"):
            _parse_edit_args(["1", "--new"])

    def test_multiword_values(self):
        _, old, new, _ = _parse_edit_args(["1", "--old", "hello world", "--new", "goodbye world"])
        assert old == "hello world"
        assert new == "goodbye world"


class TestPartialReplace:
    def test_single_match(self):
        assert _partial_replace("hello old world", "old", "new", False) == "hello new world"

    def test_not_found(self):
        with pytest.raises(ValueError, match="not found"):
            _partial_replace("hello world", "missing", "x", False)

    def test_multiple_matches_without_replace_all(self):
        with pytest.raises(ValueError, match="found 2 times"):
            _partial_replace("ab ab", "ab", "cd", False)

    def test_replace_all(self):
        assert _partial_replace("ab ab ab", "ab", "cd", True) == "cd cd cd"

    def test_empty_body(self):
        with pytest.raises(ValueError, match="not found"):
            _partial_replace("", "x", "y", False)


class TestExecuteFallthrough:
    async def test_empty_args(self):
        assert await execute([], "owner/repo", {"env": {"GH_TOKEN": "t"}}) is None

    async def test_unknown_subcommand(self):
        assert await execute(["list"], "owner/repo", {"env": {"GH_TOKEN": "t"}}) is None

    async def test_edit_without_old_new(self):
        assert await execute(["edit", "42"], "owner/repo", {"env": {"GH_TOKEN": "t"}}) is None

    async def test_comment_without_old_new(self):
        result = await execute(
            ["comment", "edit", "123"], "owner/repo", {"env": {"GH_TOKEN": "t"}},
        )
        assert result is None


# =========================================================================
# Handler tests with mock GitHub API
# =========================================================================


@pytest.fixture
async def mock_github_api():
    """Mock GitHub REST API for issues and comments."""
    app = web.Application()
    state = {
        "issues": {},
        "comments": {},
        "requests": [],
    }

    async def handle_issue(request):
        number = request.match_info["number"]
        state["requests"].append({
            "method": request.method,
            "path": request.path,
        })
        if request.method == "GET":
            data = state["issues"].get(number, {"body": ""})
            return web.json_response(data)
        if request.method == "PATCH":
            data = await request.json()
            state["issues"][number] = data
            return web.json_response(data)
        return web.Response(status=405)

    async def handle_comment(request):
        comment_id = request.match_info["comment_id"]
        state["requests"].append({
            "method": request.method,
            "path": request.path,
        })
        if request.method == "GET":
            data = state["comments"].get(comment_id, {"body": ""})
            return web.json_response(data)
        if request.method == "PATCH":
            data = await request.json()
            state["comments"][comment_id] = data
            return web.json_response(data)
        return web.Response(status=405)

    app.router.add_route("*", "/repos/{owner}/{repo}/issues/comments/{comment_id}", handle_comment)
    app.router.add_route("*", "/repos/{owner}/{repo}/issues/{number}", handle_issue)

    async with TestServer(app) as server:
        yield server, state


class TestHandleEdit:
    async def test_replaces_body(self, mock_github_api):
        server, state = mock_github_api
        state["issues"]["42"] = {"body": "hello old world"}
        api_url = str(server.make_url(""))

        result = await _handle_edit(
            ["42", "--old", "old", "--new", "new"], "owner", "repo", "tok",
            api_url=api_url,
        )
        assert result["exit_code"] == 0
        assert state["issues"]["42"]["body"] == "hello new world"

    async def test_replace_all(self, mock_github_api):
        server, state = mock_github_api
        state["issues"]["1"] = {"body": "aaa bbb aaa"}
        api_url = str(server.make_url(""))

        result = await _handle_edit(
            ["1", "--old", "aaa", "--new", "ccc", "--replace-all"],
            "owner", "repo", "tok", api_url=api_url,
        )
        assert result["exit_code"] == 0
        assert state["issues"]["1"]["body"] == "ccc bbb ccc"

    async def test_not_found_returns_error(self, mock_github_api):
        server, state = mock_github_api
        state["issues"]["1"] = {"body": "hello world"}
        api_url = str(server.make_url(""))

        result = await _handle_edit(
            ["1", "--old", "missing", "--new", "x"], "owner", "repo", "tok",
            api_url=api_url,
        )
        assert result["exit_code"] == 1
        assert "not found" in result["stderr"]

    async def test_missing_issue_number(self, mock_github_api):
        server, _ = mock_github_api
        result = await _handle_edit(
            ["--old", "x", "--new", "y"], "owner", "repo", "tok",
            api_url=str(server.make_url("")),
        )
        assert result["exit_code"] == 1
        assert "issue number required" in result["stderr"]

    async def test_invalid_issue_number(self, mock_github_api):
        server, _ = mock_github_api
        result = await _handle_edit(
            ["abc", "--old", "x", "--new", "y"], "owner", "repo", "tok",
            api_url=str(server.make_url("")),
        )
        assert result["exit_code"] == 1
        assert "Invalid issue number" in result["stderr"]

    async def test_null_body_treated_as_empty(self, mock_github_api):
        server, state = mock_github_api
        state["issues"]["1"] = {"body": None}
        api_url = str(server.make_url(""))

        result = await _handle_edit(
            ["1", "--old", "x", "--new", "y"], "owner", "repo", "tok",
            api_url=api_url,
        )
        assert result["exit_code"] == 1
        assert "not found" in result["stderr"]


class TestHandleCommentEdit:
    async def test_replaces_body(self, mock_github_api):
        server, state = mock_github_api
        state["comments"]["999"] = {"body": "fix typo plz"}
        api_url = str(server.make_url(""))

        result = await _handle_comment_edit(
            ["999", "--old", "plz", "--new", "please"], "owner", "repo", "tok",
            api_url=api_url,
        )
        assert result["exit_code"] == 0
        assert state["comments"]["999"]["body"] == "fix typo please"

    async def test_missing_comment_id(self, mock_github_api):
        server, _ = mock_github_api
        result = await _handle_comment_edit(
            ["--old", "x", "--new", "y"], "owner", "repo", "tok",
            api_url=str(server.make_url("")),
        )
        assert result["exit_code"] == 1
        assert "comment ID required" in result["stderr"]

    async def test_rest_calls_correct_endpoints(self, mock_github_api):
        server, state = mock_github_api
        state["comments"]["123"] = {"body": "old text"}
        api_url = str(server.make_url(""))

        await _handle_comment_edit(
            ["123", "--old", "old", "--new", "new"], "own", "rep", "tok",
            api_url=api_url,
        )
        assert state["requests"][0]["method"] == "GET"
        assert "/own/rep/issues/comments/123" in state["requests"][0]["path"]
        assert state["requests"][1]["method"] == "PATCH"


# =========================================================================
# Integration: via /cli endpoint
# =========================================================================


class TestIssueCommandIntegration:
    async def test_edit_via_cli_endpoint(self, mock_github_api):
        server, state = mock_github_api
        state["issues"]["7"] = {"body": "before change"}

        plugin = GitHubPlugin()
        config = {
            "plugins": {
                "github": {
                    "credentials": [{"token": "tok", "resources": ["*"]}],
                    "_api_url": str(server.make_url("")),
                }
            }
        }

        # Patch _API_URL for the integration test
        import fgap.plugins.github.commands.issue as issue_mod
        original_url = issue_mod._API_URL
        issue_mod._API_URL = str(server.make_url(""))
        try:
            app = create_routes(config, {"github": plugin})
            async with TestClient(TestServer(app)) as client:
                resp = await client.post("/cli", json={
                    "tool": "gh",
                    "args": ["issue", "edit", "7", "--old", "before", "--new", "after"],
                    "resource": "owner/repo",
                })
                assert resp.status == 200
                data = await resp.json()
                assert data["exit_code"] == 0
                assert state["issues"]["7"]["body"] == "after change"
        finally:
            issue_mod._API_URL = original_url

    async def test_fallthrough_to_cli(self):
        """issue list (no --old/--new) falls through to gh subprocess."""
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
                "args": ["issue", "list"],
                "resource": "owner/repo",
            })
            assert resp.status == 200
            data = await resp.json()
            # Falls through to `gh issue list` subprocess.
            # gh is likely not configured here, so it will fail,
            # but the important thing is it didn't return None (which
            # would have been caught by the router as an error).
            assert "exit_code" in data
