import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from fgap.plugins.fly.commands import credential_command, parse_credential_args
from fgap.plugins.fly.plugin import FlyPlugin

CRED = {"env": {"FLY_API_TOKEN": "FlyV1 fm2_app_scoped",
                "FLY_NO_UPDATE_CHECK": "1"}}


# =========================================================================
# credential handout (Fly's API refuses token-minted sub-tokens, so the
# stored app-scoped token itself is handed out; the handout is the
# audited event)
# =========================================================================


class TestParseCredentialArgs:
    def test_no_args_is_valid(self):
        assert parse_credential_args([]) is None

    def test_rejects_arguments(self):
        assert "takes no arguments" in parse_credential_args(["deploy"])


class TestCredentialCommand:
    async def test_hands_out_the_configured_token(self):
        result = await credential_command([], "my-app", CRED)
        assert result["exit_code"] == 0
        assert result["stdout"].strip() == "FlyV1 fm2_app_scoped"

    async def test_rejects_arguments(self):
        result = await credential_command(["deploy"], "my-app", CRED)
        assert result["exit_code"] == 2
        assert "takes no arguments" in result["stderr"]

    async def test_missing_token_fails_loudly(self):
        result = await credential_command([], "my-app", {"env": {}})
        assert result["exit_code"] == 1
        assert "no token configured" in result["stderr"]


# =========================================================================
# plugin surface + health
# =========================================================================


@pytest.fixture
async def mock_fly_api():
    """Mock Fly GraphQL endpoint."""
    app = web.Application()
    state = {"responses": []}

    async def handle_graphql(request):
        if state["responses"]:
            return state["responses"].pop(0)
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return web.json_response({"errors": [{"message": "unauthorized"}]},
                                     status=401)
        return web.json_response(
            {"data": {"viewer": {"email": "dev@example.com"}}})

    app.router.add_post("/graphql", handle_graphql)
    async with TestServer(app) as server:
        yield server, state


class TestFlyPlugin:
    def test_surface(self):
        plugin = FlyPlugin()
        assert plugin.name == "fly"
        assert plugin.tools == ["fly", "flyctl"]
        assert "credential" in plugin.get_commands()

    async def test_health_valid_token(self, mock_fly_api):
        server, _state = mock_fly_api
        plugin = FlyPlugin()
        config = {"credentials": [
            {"token": "FlyV1 fm2_ok", "resources": ["*"]},
        ]}
        results = await plugin.health_check(
            config, _api_url=str(server.make_url("/graphql")))
        assert len(results) == 1
        assert results[0]["valid"] is True
        assert results[0]["email"] == "dev@example.com"

    async def test_health_invalid_token(self, mock_fly_api):
        server, state = mock_fly_api
        state["responses"].append(
            web.json_response({"errors": [{"message": "unauthorized"}]},
                              status=401))
        plugin = FlyPlugin()
        config = {"credentials": [
            {"token": "FlyV1 fm2_bad", "resources": ["*"]},
        ]}
        results = await plugin.health_check(
            config, _api_url=str(server.make_url("/graphql")))
        assert results[0]["valid"] is False
        assert "401" in results[0]["error"]

    async def test_health_graphql_error_means_invalid(self, mock_fly_api):
        # the Fly GraphQL API reports bad auth as HTTP 200 + errors array
        server, state = mock_fly_api
        state["responses"].append(
            web.json_response({"data": None,
                               "errors": [{"message": "Unauthorized"}]}))
        plugin = FlyPlugin()
        config = {"credentials": [
            {"token": "FlyV1 fm2_fake", "resources": ["*"]},
        ]}
        results = await plugin.health_check(
            config, _api_url=str(server.make_url("/graphql")))
        assert results[0]["valid"] is False
        assert results[0]["error"] == "Unauthorized"

    async def test_health_scoped_token_without_viewer(self, mock_fly_api):
        # org/deploy macaroons may not expose viewer fields: a 200 with a
        # null viewer still counts as valid, with an empty email
        server, state = mock_fly_api
        state["responses"].append(
            web.json_response({"data": {"viewer": None}}))
        plugin = FlyPlugin()
        config = {"credentials": [
            {"token": "FlyV1 fm2_scoped", "resources": ["my-app"]},
        ]}
        results = await plugin.health_check(
            config, _api_url=str(server.make_url("/graphql")))
        assert results[0]["valid"] is True
        assert results[0]["email"] == ""
