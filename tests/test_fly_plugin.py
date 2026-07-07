import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from fgap.plugins.fly.commands import mint_command, parse_mint_args
from fgap.plugins.fly.plugin import FlyPlugin

CRED = {"env": {"FLY_API_TOKEN": "FlyV1 fm2_master",
                "FLY_NO_UPDATE_CHECK": "1"}}


# =========================================================================
# mint: pure arg parsing
# =========================================================================


class TestParseMintArgs:
    def test_default_expiry(self):
        assert parse_mint_args(["deploy"]) == ("deploy", "5m")

    def test_explicit_expiry(self):
        assert parse_mint_args(["deploy", "--expiry", "15m"]) == ("deploy", "15m")
        assert parse_mint_args(["deploy", "-x", "1h"]) == ("deploy", "1h")

    def test_unknown_kind(self):
        assert "unknown token kind" in parse_mint_args(["org"])
        assert "unknown token kind" in parse_mint_args([])

    def test_dangling_expiry_flag(self):
        assert "requires a value" in parse_mint_args(["deploy", "--expiry"])

    def test_unknown_argument(self):
        assert "unknown argument" in parse_mint_args(["deploy", "--bogus"])


# =========================================================================
# mint: execution
# =========================================================================


class TestMintCommand:
    async def test_mints_scoped_deploy_token(self):
        calls = []

        async def fake_execute(tool, args, env, **kw):
            calls.append((tool, args, env))
            return {"exit_code": 0, "stdout": "FlyV1 fm2_short\n", "stderr": ""}

        result = await mint_command(["deploy", "--expiry", "10m"], "my-app",
                                    CRED, _execute=fake_execute)
        assert result["exit_code"] == 0
        assert result["stdout"].strip() == "FlyV1 fm2_short"
        tool, args, env = calls[0]
        assert tool == "flyctl"
        # scoped to the resource app, with the requested TTL, using the
        # master credential's env
        assert args == ["tokens", "create", "deploy",
                        "-a", "my-app", "--expiry", "10m"]
        assert env["FLY_API_TOKEN"] == "FlyV1 fm2_master"

    async def test_invalid_args_do_not_execute(self):
        async def fake_execute(*a, **kw):  # pragma: no cover
            raise AssertionError("must not execute")

        result = await mint_command(["org"], "my-app", CRED,
                                    _execute=fake_execute)
        assert result["exit_code"] == 2
        assert "unknown token kind" in result["stderr"]


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
        assert "mint" in plugin.get_commands()

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
