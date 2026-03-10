import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from fgap.plugins.base import Plugin


class EchoPlugin(Plugin):
    """Test plugin that maps to the real 'echo' binary."""

    @property
    def name(self) -> str:
        return "echo"

    @property
    def tools(self) -> list[str]:
        return ["echo"]

    def select_credential(self, resource: str, config: dict) -> dict | None:
        for cred in config.get("credentials", []):
            for pattern in cred.get("resources", []):
                if _match_resource(pattern, resource):
                    return {"env": {"ECHO_TOKEN": cred["token"]}}
        return None


class FallthroughPlugin(Plugin):
    """Test plugin with a custom command that can fall through."""

    @property
    def name(self) -> str:
        return "ft"

    @property
    def tools(self) -> list[str]:
        return ["printf"]

    def select_credential(self, resource: str, config: dict) -> dict | None:
        for cred in config.get("credentials", []):
            for pattern in cred.get("resources", []):
                if pattern == "*" or pattern == resource:
                    return {"env": {"FT_TOKEN": cred["token"]}}
        return None

    def get_commands(self) -> dict:
        async def handle_custom(args, resource, credential):
            if args and args[0] == "intercept":
                return {"exit_code": 0, "stdout": "intercepted", "stderr": ""}
            return None  # fall through to CLI

        return {"custom": handle_custom}


def _match_resource(pattern: str, resource: str) -> bool:
    if pattern == "*":
        return True
    if pattern.endswith("/*"):
        return resource.split("/")[0] == pattern[:-2]
    return pattern == resource


@pytest.fixture
def echo_plugin():
    return EchoPlugin()


@pytest.fixture
def echo_config():
    return {
        "plugins": {
            "echo": {
                "credentials": [
                    {"token": "tok_specific", "resources": ["acme/repo1"]},
                    {"token": "tok_wildcard", "resources": ["acme/*"]},
                    {"token": "tok_default", "resources": ["*"]},
                ]
            }
        }
    }


class DownloadPlugin(Plugin):
    """Test plugin that provides GH_TOKEN for download tests."""

    @property
    def name(self) -> str:
        return "dl"

    @property
    def tools(self) -> list[str]:
        return ["gh"]

    def select_credential(self, resource: str, config: dict) -> dict | None:
        for cred in config.get("credentials", []):
            for pattern in cred.get("resources", []):
                if _match_resource(pattern, resource):
                    return {
                        "env": {
                            "GH_TOKEN": cred["token"],
                            "GH_HOST": "github.com",
                        }
                    }
        return None


@pytest.fixture
def ft_plugin():
    return FallthroughPlugin()


@pytest.fixture
def ft_config():
    return {
        "plugins": {
            "ft": {
                "credentials": [
                    {"token": "ft_tok", "resources": ["*"]},
                ]
            }
        }
    }


@pytest.fixture
def dl_plugin():
    return DownloadPlugin()


@pytest.fixture
def dl_config():
    return {
        "allow_insecure_download_urls": True,
        "plugins": {
            "dl": {
                "credentials": [
                    {"token": "test_gh_token", "resources": ["*"]},
                ]
            }
        }
    }


# =========================================================================
# gh client mock proxy (shared across test_client_gh_*.py)
# =========================================================================


@pytest.fixture
async def mock_proxy():
    """Mock fgap proxy."""
    app = web.Application()
    state = {
        "responses": [],
        "requests": [],
        "auth_status": None,
        "download_responses": [],
        "download_requests": [],
    }

    async def handle_cli(request):
        data = await request.json()
        state["requests"].append(data)
        if state["responses"]:
            return state["responses"].pop(0)
        return web.json_response({"exit_code": 0, "stdout": "", "stderr": ""})

    async def handle_download(request):
        data = await request.json()
        state["download_requests"].append(data)
        if state["download_responses"]:
            return state["download_responses"].pop(0)
        return web.Response(body=b"asset-bytes", content_type="application/octet-stream")

    async def handle_auth_status(request):
        if state["auth_status"]:
            return state["auth_status"]
        return web.json_response({"plugins": {}})

    app.router.add_post("/cli", handle_cli)
    app.router.add_post("/download", handle_download)
    app.router.add_get("/auth/status", handle_auth_status)
    async with TestServer(app) as server:
        yield server, state
