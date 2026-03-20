import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from fgap.plugins.notion.plugin import NotionPlugin


@pytest.fixture
async def mock_notion_api():
    """Mock Notion API server."""
    app = web.Application()
    state = {"responses": []}

    async def handle_me(request):
        auth = request.headers.get("Authorization", "")
        if state["responses"]:
            return state["responses"].pop(0)
        if not auth.startswith("Bearer "):
            return web.json_response(
                {"object": "error", "message": "Unauthorized"},
                status=401,
            )
        return web.json_response({"type": "bot", "name": "Test Bot"})

    app.router.add_get("/v1/users/me", handle_me)
    async with TestServer(app) as server:
        yield server, state


def _api_url(server) -> str:
    return str(server.make_url(""))


class TestNotionHealthCheck:
    async def test_valid_token(self, mock_notion_api):
        server, state = mock_notion_api
        plugin = NotionPlugin()
        config = {"credentials": [
            {"token": "ntn_validtoken", "resources": ["*"]},
        ]}
        results = await plugin.health_check(config, _api_url=_api_url(server))
        assert len(results) == 1
        assert results[0]["valid"] is True
        assert results[0]["bot_name"] == "Test Bot"

    async def test_invalid_token(self, mock_notion_api):
        server, state = mock_notion_api
        state["responses"].append(
            web.json_response(
                {"object": "error", "message": "Unauthorized"},
                status=401,
            ),
        )
        plugin = NotionPlugin()
        config = {"credentials": [
            {"token": "ntn_invalid", "resources": ["*"]},
        ]}
        results = await plugin.health_check(config, _api_url=_api_url(server))
        assert len(results) == 1
        assert results[0]["valid"] is False

    async def test_empty_credentials(self):
        plugin = NotionPlugin()
        results = await plugin.health_check({"credentials": []})
        assert results == []

    async def test_masked_token(self, mock_notion_api):
        server, state = mock_notion_api
        plugin = NotionPlugin()
        config = {"credentials": [
            {"token": "ntn_abcdefghijk", "resources": ["*"]},
        ]}
        results = await plugin.health_check(config, _api_url=_api_url(server))
        assert "ntn_abcdefghijk" not in results[0].get("masked_token", "")
