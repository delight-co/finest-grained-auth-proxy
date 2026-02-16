import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from fgap.plugins.github.plugin import GitHubPlugin


@pytest.fixture
async def mock_github_api():
    """Mock GitHub API server."""
    app = web.Application()
    state = {"responses": []}

    async def handle_user(request):
        auth = request.headers.get("Authorization", "")
        if state["responses"]:
            return state["responses"].pop(0)
        # Default: valid token
        return web.json_response(
            {"login": "testuser"},
            headers={
                "X-OAuth-Scopes": "repo, read:org",
                "X-RateLimit-Remaining": "4999",
            },
        )

    app.router.add_get("/user", handle_user)
    async with TestServer(app) as server:
        yield server, state


def _api_url(server) -> str:
    return str(server.make_url(""))


class TestGitHubHealthCheck:
    async def test_valid_token(self, mock_github_api):
        server, state = mock_github_api
        plugin = GitHubPlugin()
        config = {"credentials": [
            {"token": "ghp_validtoken123456", "resources": ["*"]},
        ]}
        results = await plugin.health_check(config, _api_url=_api_url(server))

        assert len(results) == 1
        r = results[0]
        assert r["valid"] is True
        assert r["user"] == "testuser"
        assert r["scopes"] == "repo, read:org"
        assert r["rate_limit_remaining"] == "4999"
        assert r["masked_token"] == "ghp_vali***"
        assert r["resources"] == ["*"]

    async def test_invalid_token(self, mock_github_api):
        server, state = mock_github_api
        state["responses"].append(
            web.json_response(
                {"message": "Bad credentials"},
                status=401,
            ),
        )
        plugin = GitHubPlugin()
        config = {"credentials": [
            {"token": "ghp_badtoken1234567", "resources": ["*"]},
        ]}
        results = await plugin.health_check(config, _api_url=_api_url(server))

        assert len(results) == 1
        r = results[0]
        assert r["valid"] is False
        assert "401" in r["error"]
        assert r["masked_token"] == "ghp_badt***"

    async def test_multiple_credentials(self, mock_github_api):
        server, state = mock_github_api
        state["responses"].extend([
            web.json_response(
                {"login": "user1"},
                headers={"X-OAuth-Scopes": "repo", "X-RateLimit-Remaining": "5000"},
            ),
            web.json_response(
                {"login": "user2"},
                headers={"X-OAuth-Scopes": "read:org", "X-RateLimit-Remaining": "4000"},
            ),
        ])
        plugin = GitHubPlugin()
        config = {"credentials": [
            {"token": "ghp_token1_xxxxxxx", "resources": ["acme/*"]},
            {"token": "ghp_token2_xxxxxxx", "resources": ["other/*"]},
        ]}
        results = await plugin.health_check(config, _api_url=_api_url(server))

        assert len(results) == 2
        assert results[0]["user"] == "user1"
        assert results[1]["user"] == "user2"

    async def test_connection_error(self):
        plugin = GitHubPlugin()
        config = {"credentials": [
            {"token": "ghp_token_xxxxxxxxx", "resources": ["*"]},
        ]}
        results = await plugin.health_check(
            config, _api_url="http://127.0.0.1:1",
        )

        assert len(results) == 1
        r = results[0]
        assert r["valid"] is False
        assert "error" in r

    async def test_empty_credentials(self):
        plugin = GitHubPlugin()
        results = await plugin.health_check({"credentials": []})
        assert results == []

    async def test_short_token_fully_masked(self):
        plugin = GitHubPlugin()
        config = {"credentials": [
            {"token": "short", "resources": ["*"]},
        ]}
        results = await plugin.health_check(
            config, _api_url="http://127.0.0.1:1",
        )
        assert results[0]["masked_token"] == "***"
