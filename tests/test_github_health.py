import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from fgap.plugins.github.plugin import GitHubPlugin


@pytest.fixture(scope="module")
def private_pem():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


@pytest.fixture
async def mock_github_api():
    """Mock GitHub API server."""
    app = web.Application()
    state = {"responses": [], "app_responses": [], "app_auth": []}

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

    async def handle_app(request):
        state["app_auth"].append(request.headers.get("Authorization", ""))
        if state["app_responses"]:
            return state["app_responses"].pop(0)
        # Default: the App the JWT authenticates as
        return web.json_response({
            "name": "My App",
            "slug": "my-app",
            "permissions": {"contents": "write", "checks": "read"},
        })

    app.router.add_get("/user", handle_user)
    app.router.add_get("/app", handle_app)
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


class TestGitHubAppHealthCheck:
    def _app_cred(self, private_pem, **extra) -> dict:
        return {"app_id": 123456, "installation_id": 654321,
                "private_key": private_pem, "resources": ["myorg/*"],
                **extra}

    async def test_app_credential_valid(self, mock_github_api, private_pem):
        server, state = mock_github_api
        plugin = GitHubPlugin()
        config = {"credentials": [self._app_cred(private_pem)]}
        results = await plugin.health_check(config, _api_url=_api_url(server))

        assert len(results) == 1
        r = results[0]
        assert r["valid"] is True
        assert r["app"] == "My App"
        assert r["slug"] == "my-app"
        assert r["permissions"] == {"contents": "write", "checks": "read"}
        assert r["app_id"] == 123456
        assert r["installation_id"] == 654321
        assert r["resources"] == ["myorg/*"]
        # identified as an App, not rendered as a broken empty token
        assert "masked_token" not in r
        # the probe authenticated with the App JWT
        assert state["app_auth"] and state["app_auth"][0].startswith("Bearer ey")

    async def test_app_credential_api_error(self, mock_github_api,
                                            private_pem):
        server, state = mock_github_api
        state["app_responses"].append(
            web.json_response({"message": "Integration not found"},
                              status=404),
        )
        plugin = GitHubPlugin()
        config = {"credentials": [self._app_cred(private_pem)]}
        results = await plugin.health_check(config, _api_url=_api_url(server))

        r = results[0]
        assert r["valid"] is False
        assert "404" in r["error"]
        assert r["app_id"] == 123456

    async def test_app_credential_unreadable_key(self):
        plugin = GitHubPlugin()
        config = {"credentials": [
            {"app_id": 123456, "installation_id": 654321,
             "private_key_path": "/nonexistent/key.pem",
             "resources": ["myorg/*"]},
        ]}
        results = await plugin.health_check(
            config, _api_url="http://127.0.0.1:1",
        )
        r = results[0]
        assert r["valid"] is False
        assert "error" in r

    async def test_mixed_token_and_app(self, mock_github_api, private_pem):
        server, state = mock_github_api
        plugin = GitHubPlugin()
        config = {"credentials": [
            self._app_cred(private_pem),
            {"token": "ghp_token1_xxxxxxx", "resources": ["*"]},
        ]}
        results = await plugin.health_check(config, _api_url=_api_url(server))

        assert len(results) == 2
        assert results[0]["valid"] is True
        assert results[0]["slug"] == "my-app"
        assert results[1]["valid"] is True
        assert results[1]["user"] == "testuser"
        assert results[1]["masked_token"] == "ghp_toke***"
