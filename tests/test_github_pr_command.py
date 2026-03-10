from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from fgap.core.router import create_routes
from fgap.plugins.github import GitHubPlugin
from fgap.plugins.github.commands.pr import (
    _get_thread_id_for_comment,
    _handle_edit,
    _handle_review_thread,
    _thread_has_comment,
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

    async def test_comment_without_old_new(self):
        result = await execute(
            ["comment", "edit", "123"], "owner/repo", {"env": {"GH_TOKEN": "t"}},
        )
        assert result is None

    async def test_review_thread_no_args_shows_help(self):
        result = await execute(
            ["review-thread"], "owner/repo", {"env": {"GH_TOKEN": "t"}},
        )
        assert result["exit_code"] == 0
        assert "resolve" in result["stdout"]
        assert "unresolve" in result["stdout"]

    async def test_review_thread_help_flag(self):
        result = await execute(
            ["review-thread", "--help"], "owner/repo", {"env": {"GH_TOKEN": "t"}},
        )
        assert result["exit_code"] == 0
        assert "resolve" in result["stdout"]

    async def test_review_thread_missing_comment_id(self):
        result = await execute(
            ["review-thread", "resolve"], "owner/repo", {"env": {"GH_TOKEN": "t"}},
        )
        assert result["exit_code"] == 1
        assert "Usage" in result["stderr"]


class TestHelp:
    @patch("fgap.plugins.github.commands.issue.execute_cli", new_callable=AsyncMock)
    async def test_pr_help_shows_review_thread(self, mock_cli):
        mock_cli.return_value = {"exit_code": 0, "stdout": "gh pr help\n", "stderr": ""}
        result = await execute(["--help"], "owner/repo", {"env": {"GH_TOKEN": "t"}})
        assert result["exit_code"] == 0
        assert "gh pr help" in result["stdout"]
        assert "review-thread" in result["stdout"]

    @patch("fgap.plugins.github.commands.issue.execute_cli", new_callable=AsyncMock)
    async def test_pr_edit_help(self, mock_cli):
        mock_cli.return_value = {"exit_code": 0, "stdout": "gh pr edit help\n", "stderr": ""}
        result = await execute(["edit", "--help"], "owner/repo", {"env": {"GH_TOKEN": "t"}})
        assert result["exit_code"] == 0
        assert "gh pr edit help" in result["stdout"]
        assert "--old" in result["stdout"]
        assert "--new" in result["stdout"]
        mock_cli.assert_called_once_with("gh", ["pr", "edit", "--help"], {}, timeout=10)

    @patch("fgap.plugins.github.commands.issue.execute_cli", new_callable=AsyncMock)
    async def test_pr_comment_help_shows_edit(self, mock_cli):
        mock_cli.return_value = {"exit_code": 0, "stdout": "gh pr comment help\n", "stderr": ""}
        result = await execute(
            ["comment", "--help"], "owner/repo", {"env": {"GH_TOKEN": "t"}},
        )
        assert result["exit_code"] == 0
        assert "gh pr comment help" in result["stdout"]
        assert "edit" in result["stdout"]

    async def test_pr_comment_edit_help(self):
        result = await execute(
            ["comment", "edit", "--help"], "owner/repo", {"env": {"GH_TOKEN": "t"}},
        )
        assert result["exit_code"] == 0
        assert "--old" in result["stdout"]
        assert "comment-id" in result["stdout"]

    async def test_review_thread_help(self):
        result = await execute(
            ["review-thread", "--help"], "owner/repo", {"env": {"GH_TOKEN": "t"}},
        )
        assert result["exit_code"] == 0
        assert "resolve" in result["stdout"]
        assert "unresolve" in result["stdout"]
        assert "PRRC_" in result["stdout"]


# =========================================================================
# Handler tests with mock GitHub API
# =========================================================================


@pytest.fixture
async def mock_github_api():
    """Mock GitHub REST API for pull requests."""
    app = web.Application()
    state = {
        "pulls": {},
        "comments": {},
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


class TestHandleCommentEdit:
    """PR comment edit reuses issue._handle_comment_edit (same API endpoint)."""

    async def test_replaces_body(self, mock_github_api):
        from fgap.plugins.github.commands.issue import _handle_comment_edit

        server, state = mock_github_api
        state["comments"]["999"] = {"body": "fix typo plz"}
        api_url = str(server.make_url(""))

        result = await _handle_comment_edit(
            ["999", "--old", "plz", "--new", "please"], "owner", "repo", "tok",
            api_url=api_url,
        )
        assert result["exit_code"] == 0
        assert state["comments"]["999"]["body"] == "fix typo please"

    async def test_routing_via_execute(self, mock_github_api):
        """Verify `pr comment edit` routes to comment edit handler."""
        server, state = mock_github_api
        state["comments"]["456"] = {"body": "old text"}

        import fgap.plugins.github.commands.issue as issue_mod
        original_url = issue_mod._API_URL
        issue_mod._API_URL = str(server.make_url(""))
        try:
            result = await execute(
                ["comment", "edit", "456", "--old", "old", "--new", "new"],
                "owner/repo", {"env": {"GH_TOKEN": "tok"}},
            )
            assert result is not None
            assert result["exit_code"] == 0
            assert state["comments"]["456"]["body"] == "new text"
        finally:
            issue_mod._API_URL = original_url


# =========================================================================
# Review thread tests
# =========================================================================


def _make_graphql_handler(state):
    """Create a GraphQL handler that supports pagination."""

    def _paginate(items, cursor, page_size):
        start = int(cursor) if cursor else 0
        end = start + page_size if page_size else len(items)
        page = items[start:end]
        has_next = end < len(items)
        end_cursor = str(end) if has_next else None
        return page, has_next, end_cursor

    async def handle_graphql(request):
        data = await request.json()
        query = data.get("query", "")
        variables = data.get("variables", {})
        state["requests"].append({"query": query, "variables": variables})

        # Comment → thread lookup (PullRequestReviewComment query)
        if "PullRequestReviewComment" in query:
            node_id = variables.get("id", "")
            all_threads = list(state["threads"].values())
            # Check if the comment exists
            found = any(
                c["id"] == node_id
                for t in all_threads
                for c in t["comments"]
            )
            if not found:
                return web.json_response({"data": {"node": None}})

            threads_cursor = variables.get("threadsCursor")
            page_size = state.get("threads_page_size")
            page, has_next, end_cursor = _paginate(
                all_threads, threads_cursor, page_size,
            )

            comments_page_size = state.get("comments_page_size")
            nodes = []
            for t in page:
                c_page, c_has_next, _ = _paginate(
                    t["comments"], None, comments_page_size,
                )
                nodes.append({
                    "id": t["id"],
                    "comments": {
                        "pageInfo": {"hasNextPage": c_has_next},
                        "nodes": c_page,
                    },
                })

            return web.json_response({
                "data": {
                    "node": {
                        "pullRequest": {
                            "reviewThreads": {
                                "pageInfo": {
                                    "hasNextPage": has_next,
                                    "endCursor": end_cursor,
                                },
                                "nodes": nodes,
                            },
                        },
                    },
                },
            })

        # Thread comment pagination (PullRequestReviewThread query)
        if "PullRequestReviewThread" in query:
            thread_id = variables.get("threadId", "")
            thread = state["threads"].get(thread_id)
            if not thread:
                return web.json_response({"data": {"node": None}})

            cursor = variables.get("cursor")
            page_size = state.get("comments_page_size")
            page, has_next, end_cursor = _paginate(
                thread["comments"], cursor, page_size,
            )
            return web.json_response({
                "data": {
                    "node": {
                        "comments": {
                            "pageInfo": {
                                "hasNextPage": has_next,
                                "endCursor": end_cursor,
                            },
                            "nodes": page,
                        },
                    },
                },
            })

        if "unresolveReviewThread" in query:
            thread_id = variables.get("threadId", "")
            if thread_id in state["threads"]:
                state["threads"][thread_id]["isResolved"] = False
                return web.json_response({
                    "data": {
                        "unresolveReviewThread": {
                            "thread": {"isResolved": False},
                        },
                    },
                })

        if "resolveReviewThread" in query:
            thread_id = variables.get("threadId", "")
            if thread_id in state["threads"]:
                state["threads"][thread_id]["isResolved"] = True
                return web.json_response({
                    "data": {
                        "resolveReviewThread": {
                            "thread": {"isResolved": True},
                        },
                    },
                })

        return web.json_response({"errors": [{"message": "unexpected query"}]})

    return handle_graphql


@pytest.fixture
async def mock_graphql_api():
    """Mock GitHub GraphQL API for review thread operations."""
    app = web.Application()
    state = {
        "threads": {
            "PRRT_thread1": {
                "id": "PRRT_thread1",
                "isResolved": False,
                "comments": [
                    {"id": "PRRC_comment1"},
                    {"id": "PRRC_comment2"},
                ],
            },
            "PRRT_thread2": {
                "id": "PRRT_thread2",
                "isResolved": True,
                "comments": [
                    {"id": "PRRC_comment3"},
                ],
            },
        },
        "requests": [],
    }

    app.router.add_post("/graphql", _make_graphql_handler(state))

    async with TestServer(app) as server:
        yield server, state


class TestGetThreadIdForComment:
    async def test_finds_thread_for_comment(self, mock_graphql_api):
        server, _ = mock_graphql_api
        url = str(server.make_url("/graphql"))

        thread_id = await _get_thread_id_for_comment("PRRC_comment1", "tok", url=url)
        assert thread_id == "PRRT_thread1"

    async def test_finds_thread_for_second_comment(self, mock_graphql_api):
        server, _ = mock_graphql_api
        url = str(server.make_url("/graphql"))

        thread_id = await _get_thread_id_for_comment("PRRC_comment3", "tok", url=url)
        assert thread_id == "PRRT_thread2"

    async def test_unknown_comment_raises(self, mock_graphql_api):
        server, _ = mock_graphql_api
        url = str(server.make_url("/graphql"))

        with pytest.raises(ValueError, match="not found"):
            await _get_thread_id_for_comment("PRRC_unknown", "tok", url=url)


class TestHandleReviewThread:
    async def test_resolve(self, mock_graphql_api):
        server, state = mock_graphql_api
        url = str(server.make_url("/graphql"))

        result = await _handle_review_thread("resolve", "PRRC_comment1", "tok", url=url)
        assert result["exit_code"] == 0
        assert "Resolved" in result["stderr"]
        assert state["threads"]["PRRT_thread1"]["isResolved"] is True

    async def test_unresolve(self, mock_graphql_api):
        server, state = mock_graphql_api
        url = str(server.make_url("/graphql"))

        result = await _handle_review_thread("unresolve", "PRRC_comment3", "tok", url=url)
        assert result["exit_code"] == 0
        assert "Unresolved" in result["stderr"]
        assert state["threads"]["PRRT_thread2"]["isResolved"] is False

    async def test_unknown_comment_returns_error(self, mock_graphql_api):
        server, _ = mock_graphql_api
        url = str(server.make_url("/graphql"))

        result = await _handle_review_thread("resolve", "PRRC_unknown", "tok", url=url)
        assert result["exit_code"] == 1
        assert "not found" in result["stderr"]


# =========================================================================
# Pagination tests
# =========================================================================


@pytest.fixture
async def mock_graphql_api_paginated():
    """Mock GraphQL API with pagination (1 thread per page, 1 comment per page)."""
    app = web.Application()
    state = {
        "threads": {
            "PRRT_t1": {
                "id": "PRRT_t1",
                "isResolved": False,
                "comments": [{"id": "PRRC_c1"}],
            },
            "PRRT_t2": {
                "id": "PRRT_t2",
                "isResolved": False,
                "comments": [{"id": "PRRC_c2"}, {"id": "PRRC_c3"}],
            },
            "PRRT_t3": {
                "id": "PRRT_t3",
                "isResolved": False,
                "comments": [{"id": "PRRC_c4"}],
            },
        },
        "threads_page_size": 1,
        "comments_page_size": 1,
        "requests": [],
    }

    app.router.add_post("/graphql", _make_graphql_handler(state))

    async with TestServer(app) as server:
        yield server, state


class TestThreadPagination:
    async def test_finds_comment_on_second_thread_page(
        self, mock_graphql_api_paginated,
    ):
        """Thread is on the 2nd page (threads_page_size=1)."""
        server, _ = mock_graphql_api_paginated
        url = str(server.make_url("/graphql"))

        thread_id = await _get_thread_id_for_comment("PRRC_c2", "tok", url=url)
        assert thread_id == "PRRT_t2"

    async def test_finds_comment_on_last_thread_page(
        self, mock_graphql_api_paginated,
    ):
        """Thread is on the 3rd (last) page."""
        server, _ = mock_graphql_api_paginated
        url = str(server.make_url("/graphql"))

        thread_id = await _get_thread_id_for_comment("PRRC_c4", "tok", url=url)
        assert thread_id == "PRRT_t3"

    async def test_finds_comment_via_comment_pagination(
        self, mock_graphql_api_paginated,
    ):
        """Comment is on the 2nd page of its thread (comments_page_size=1)."""
        server, _ = mock_graphql_api_paginated
        url = str(server.make_url("/graphql"))

        thread_id = await _get_thread_id_for_comment("PRRC_c3", "tok", url=url)
        assert thread_id == "PRRT_t2"

    async def test_unknown_comment_with_pagination(
        self, mock_graphql_api_paginated,
    ):
        server, _ = mock_graphql_api_paginated
        url = str(server.make_url("/graphql"))

        with pytest.raises(ValueError, match="not found"):
            await _get_thread_id_for_comment("PRRC_unknown", "tok", url=url)


class TestThreadHasComment:
    async def test_finds_comment_on_second_page(
        self, mock_graphql_api_paginated,
    ):
        """_thread_has_comment paginates through comments."""
        server, _ = mock_graphql_api_paginated
        url = str(server.make_url("/graphql"))

        found = await _thread_has_comment("PRRT_t2", "PRRC_c3", "tok", url=url)
        assert found is True

    async def test_comment_not_in_thread(
        self, mock_graphql_api_paginated,
    ):
        server, _ = mock_graphql_api_paginated
        url = str(server.make_url("/graphql"))

        found = await _thread_has_comment("PRRT_t1", "PRRC_c3", "tok", url=url)
        assert found is False


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
