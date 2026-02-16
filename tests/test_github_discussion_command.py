import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from fgap.core.router import create_routes
from fgap.plugins.github import GitHubPlugin
from fgap.plugins.github.commands.discussion import (
    _parse_add_comment_args,
    _parse_comment_body,
    _parse_create_args,
    _parse_edit_args,
    execute,
)

CRED = {"env": {"GH_TOKEN": "test-token"}}


# =========================================================================
# Pure logic tests
# =========================================================================


class TestParseCreateArgs:
    def test_all_present(self):
        t, b, c = _parse_create_args(["--title", "T", "--body", "B", "--category", "C"])
        assert (t, b, c) == ("T", "B", "C")

    def test_short_flags(self):
        t, b, c = _parse_create_args(["-t", "T", "-b", "B", "-c", "C"])
        assert (t, b, c) == ("T", "B", "C")

    def test_missing_title(self):
        with pytest.raises(ValueError, match="--title"):
            _parse_create_args(["--body", "B", "--category", "C"])

    def test_missing_body(self):
        with pytest.raises(ValueError, match="--body"):
            _parse_create_args(["--title", "T", "--category", "C"])

    def test_missing_category(self):
        with pytest.raises(ValueError, match="--category"):
            _parse_create_args(["--title", "T", "--body", "B"])


class TestParseEditArgs:
    def test_title_only(self):
        t, b = _parse_edit_args(["--title", "new title"])
        assert t == "new title"
        assert b is None

    def test_body_only(self):
        t, b = _parse_edit_args(["--body", "new body"])
        assert t is None
        assert b == "new body"

    def test_both(self):
        t, b = _parse_edit_args(["--title", "T", "--body", "B"])
        assert (t, b) == ("T", "B")

    def test_neither_raises(self):
        with pytest.raises(ValueError, match="--title or --body"):
            _parse_edit_args([])


class TestParseCommentBody:
    def test_basic(self):
        assert _parse_comment_body(["--body", "hello"]) == "hello"

    def test_short_flag(self):
        assert _parse_comment_body(["-b", "hello"]) == "hello"

    def test_missing(self):
        with pytest.raises(ValueError, match="--body"):
            _parse_comment_body([])


class TestParseAddCommentArgs:
    def test_body_only(self):
        b, r = _parse_add_comment_args(["--body", "hello"])
        assert b == "hello"
        assert r is None

    def test_with_reply_to(self):
        b, r = _parse_add_comment_args(["--body", "hello", "--reply-to", "DC_123"])
        assert b == "hello"
        assert r == "DC_123"


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

    async def test_view_missing_number(self):
        result = await execute(["view"], "o/r", CRED)
        assert result["exit_code"] == 1
        assert "number required" in result["stderr"]

    async def test_close_missing_number(self):
        result = await execute(["close"], "o/r", CRED)
        assert result["exit_code"] == 1

    async def test_edit_missing_number(self):
        result = await execute(["edit"], "o/r", CRED)
        assert result["exit_code"] == 1

    async def test_comment_empty(self):
        result = await execute(["comment"], "o/r", CRED)
        assert result["exit_code"] == 1

    async def test_answer_missing_id(self):
        result = await execute(["answer"], "o/r", CRED)
        assert result["exit_code"] == 1

    async def test_poll_empty(self):
        result = await execute(["poll"], "o/r", CRED)
        assert result["exit_code"] == 1


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


class TestListDiscussions:
    async def test_returns_formatted_list(self, mock_graphql):
        server, state = mock_graphql
        state["responses"].append({"data": {"repository": {"discussions": {"nodes": [
            {
                "number": 1, "title": "First",
                "author": {"login": "alice"}, "createdAt": "2026-01-01",
                "category": {"name": "General"}, "comments": {"totalCount": 3},
            },
            {
                "number": 2, "title": "Second",
                "author": None, "createdAt": "2026-01-02",
                "category": None, "comments": {"totalCount": 0},
            },
        ]}}}})

        result = await execute(["list"], "owner/repo", CRED, url=_url(server))
        assert result["exit_code"] == 0
        assert "#1" in result["stdout"]
        assert "alice" in result["stdout"]
        assert "ghost" in result["stdout"]


class TestViewDiscussion:
    async def test_returns_details(self, mock_graphql):
        server, state = mock_graphql
        state["responses"].append({"data": {"repository": {"discussion": {
            "number": 5, "title": "My Discussion", "body": "The body",
            "author": {"login": "bob"}, "createdAt": "2026-01-01",
            "category": {"name": "Ideas"}, "url": "https://github.com/o/r/discussions/5",
            "comments": {"nodes": [
                {"id": "DC_1", "author": {"login": "carol"}, "body": "Nice!", "createdAt": "2026-01-02"},
            ]},
        }}}})

        result = await execute(["view", "5"], "owner/repo", CRED, url=_url(server))
        assert result["exit_code"] == 0
        assert "My Discussion" in result["stdout"]
        assert "The body" in result["stdout"]
        assert "carol" in result["stdout"]

    async def test_not_found(self, mock_graphql):
        server, state = mock_graphql
        state["responses"].append({"data": {"repository": {"discussion": None}}})

        result = await execute(["view", "999"], "owner/repo", CRED, url=_url(server))
        assert result["exit_code"] == 1
        assert "not found" in result["stderr"]


class TestCreateDiscussion:
    async def test_creates_discussion(self, mock_graphql):
        server, state = mock_graphql
        # 1st call: get_repository_id
        state["responses"].append({"data": {"repository": {"id": "R_123"}}})
        # 2nd call: get_discussion_category_id
        state["responses"].append({"data": {"repository": {"discussionCategories": {"nodes": [
            {"id": "DC_1", "name": "General", "slug": "general"},
            {"id": "DC_2", "name": "Ideas", "slug": "ideas"},
        ]}}}})
        # 3rd call: createDiscussion
        state["responses"].append({"data": {"createDiscussion": {"discussion": {
            "number": 10, "url": "https://github.com/o/r/discussions/10",
        }}}})

        result = await execute(
            ["create", "--title", "New", "--body", "Content", "--category", "Ideas"],
            "owner/repo", CRED, url=_url(server),
        )
        assert result["exit_code"] == 0
        assert "discussions/10" in result["stdout"]

        # Verify the mutation variables
        create_req = state["requests"][2]
        assert create_req["variables"]["repositoryId"] == "R_123"
        assert create_req["variables"]["categoryId"] == "DC_2"

    async def test_missing_args(self):
        result = await execute(["create"], "o/r", CRED)
        assert result["exit_code"] == 1
        assert "--title" in result["stderr"]

    async def test_category_not_found(self, mock_graphql):
        server, state = mock_graphql
        state["responses"].append({"data": {"repository": {"id": "R_123"}}})
        state["responses"].append({"data": {"repository": {"discussionCategories": {"nodes": [
            {"id": "DC_1", "name": "General", "slug": "general"},
        ]}}}})

        result = await execute(
            ["create", "--title", "T", "--body", "B", "--category", "NonExistent"],
            "owner/repo", CRED, url=_url(server),
        )
        assert result["exit_code"] == 1
        assert "not found" in result["stderr"].lower()


class TestUpdateDiscussion:
    async def test_updates(self, mock_graphql):
        server, state = mock_graphql
        # get_discussion_node_id
        state["responses"].append({"data": {"repository": {"discussion": {"id": "D_1"}}}})
        # updateDiscussion
        state["responses"].append({"data": {"updateDiscussion": {"discussion": {
            "number": 3, "url": "https://github.com/o/r/discussions/3",
        }}}})

        result = await execute(
            ["edit", "3", "--title", "Updated"], "owner/repo", CRED, url=_url(server),
        )
        assert result["exit_code"] == 0
        assert state["requests"][1]["variables"]["title"] == "Updated"


class TestCloseDiscussion:
    async def test_closes(self, mock_graphql):
        server, state = mock_graphql
        state["responses"].append({"data": {"repository": {"discussion": {"id": "D_1"}}}})
        state["responses"].append({"data": {"closeDiscussion": {"discussion": {
            "number": 3, "url": "https://github.com/o/r/discussions/3",
        }}}})

        result = await execute(["close", "3"], "owner/repo", CRED, url=_url(server))
        assert result["exit_code"] == 0
        assert "Closed" in result["stderr"]


class TestDeleteDiscussion:
    async def test_deletes(self, mock_graphql):
        server, state = mock_graphql
        state["responses"].append({"data": {"repository": {"discussion": {"id": "D_1"}}}})
        state["responses"].append({"data": {"deleteDiscussion": {"discussion": {"number": 3}}}})

        result = await execute(["delete", "3"], "owner/repo", CRED, url=_url(server))
        assert result["exit_code"] == 0
        assert "Deleted" in result["stderr"]


class TestAddComment:
    async def test_adds_comment(self, mock_graphql):
        server, state = mock_graphql
        state["responses"].append({"data": {"repository": {"discussion": {"id": "D_1"}}}})
        state["responses"].append({"data": {"addDiscussionComment": {"comment": {
            "id": "DC_new", "url": "https://github.com/o/r/discussions/1#comment-new",
        }}}})

        result = await execute(
            ["comment", "1", "--body", "Hello!"], "owner/repo", CRED, url=_url(server),
        )
        assert result["exit_code"] == 0
        assert "comment-new" in result["stdout"]

    async def test_with_reply_to(self, mock_graphql):
        server, state = mock_graphql
        state["responses"].append({"data": {"repository": {"discussion": {"id": "D_1"}}}})
        state["responses"].append({"data": {"addDiscussionComment": {"comment": {
            "id": "DC_reply", "url": "https://github.com/o/r/discussions/1#reply",
        }}}})

        result = await execute(
            ["comment", "1", "--body", "Reply", "--reply-to", "DC_parent"],
            "owner/repo", CRED, url=_url(server),
        )
        assert result["exit_code"] == 0
        assert state["requests"][1]["variables"]["replyToId"] == "DC_parent"


class TestEditComment:
    async def test_edits(self, mock_graphql):
        server, state = mock_graphql
        state["responses"].append({"data": {"updateDiscussionComment": {"comment": {
            "id": "DC_1", "url": "https://github.com/o/r/discussions/1#comment-1",
        }}}})

        result = await execute(
            ["comment", "edit", "DC_1", "--body", "Updated"],
            "owner/repo", CRED, url=_url(server),
        )
        assert result["exit_code"] == 0
        assert state["requests"][0]["variables"]["body"] == "Updated"


class TestDeleteComment:
    async def test_deletes(self, mock_graphql):
        server, state = mock_graphql
        state["responses"].append({"data": {"deleteDiscussionComment": {"comment": {"id": "DC_1"}}}})

        result = await execute(
            ["comment", "delete", "DC_1"], "owner/repo", CRED, url=_url(server),
        )
        assert result["exit_code"] == 0
        assert "Deleted" in result["stderr"]


class TestMarkAnswer:
    async def test_marks(self, mock_graphql):
        server, state = mock_graphql
        state["responses"].append({"data": {"markDiscussionCommentAsAnswer": {"discussion": {
            "number": 5, "url": "https://github.com/o/r/discussions/5",
        }}}})

        result = await execute(["answer", "DC_1"], "owner/repo", CRED, url=_url(server))
        assert result["exit_code"] == 0
        assert "Marked as answer" in result["stderr"]


class TestUnmarkAnswer:
    async def test_unmarks(self, mock_graphql):
        server, state = mock_graphql
        state["responses"].append({"data": {"unmarkDiscussionCommentAsAnswer": {"discussion": {
            "number": 5, "url": "https://github.com/o/r/discussions/5",
        }}}})

        result = await execute(["unanswer", "DC_1"], "owner/repo", CRED, url=_url(server))
        assert result["exit_code"] == 0
        assert "Unmarked" in result["stderr"]


class TestPollVote:
    async def test_votes(self, mock_graphql):
        server, state = mock_graphql
        state["responses"].append({"data": {"addDiscussionPollVote": {"pollOption": {
            "id": "PO_1", "option": "Yes", "totalVoteCount": 42,
        }}}})

        result = await execute(["poll", "vote", "PO_1"], "owner/repo", CRED, url=_url(server))
        assert result["exit_code"] == 0
        assert "Yes" in result["stdout"]
        assert "42" in result["stdout"]

    async def test_unknown_poll_subcommand(self):
        result = await execute(["poll", "unknown"], "o/r", CRED)
        assert result["exit_code"] == 1
        assert "Unknown poll" in result["stderr"]


# =========================================================================
# Integration: via /cli endpoint
# =========================================================================


class TestDiscussionIntegration:
    async def test_list_via_cli_endpoint(self, mock_graphql):
        server, state = mock_graphql
        state["responses"].append({"data": {"repository": {"discussions": {"nodes": [
            {
                "number": 1, "title": "Test",
                "author": {"login": "alice"}, "createdAt": "2026-01-01",
                "category": {"name": "General"}, "comments": {"totalCount": 0},
            },
        ]}}}})

        plugin = GitHubPlugin()
        config = {
            "plugins": {
                "github": {
                    "credentials": [{"token": "tok", "resources": ["*"]}],
                }
            }
        }

        import fgap.plugins.github.commands.discussion as disc_mod
        original = disc_mod._GRAPHQL_URL
        disc_mod._GRAPHQL_URL = _url(server)
        try:
            app = create_routes(config, {"github": plugin})
            async with TestClient(TestServer(app)) as client:
                resp = await client.post("/cli", json={
                    "tool": "gh",
                    "args": ["discussion", "list"],
                    "resource": "owner/repo",
                })
                assert resp.status == 200
                data = await resp.json()
                assert data["exit_code"] == 0
                assert "#1" in data["stdout"]
        finally:
            disc_mod._GRAPHQL_URL = original
