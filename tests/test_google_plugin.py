import pytest
from aiohttp.test_utils import TestClient, TestServer

from fgap.core.router import create_routes
from fgap.plugins.google.plugin import GooglePlugin


@pytest.fixture
def google_plugin():
    return GooglePlugin()


@pytest.fixture
def google_config():
    return {
        "plugins": {
            "google": {
                "credentials": [
                    {
                        "keyring_password": "test-pw",
                        "account": "user@example.com",
                        "resources": ["user@example.com"],
                    },
                    {
                        "keyring_password": "default-pw",
                        "resources": ["*"],
                    },
                ]
            }
        }
    }


@pytest.fixture
async def google_client(google_plugin, google_config):
    app = create_routes(google_config, {"google": google_plugin})
    async with TestClient(TestServer(app)) as client:
        yield client


class TestGooglePluginProperties:
    def test_name(self, google_plugin):
        assert google_plugin.name == "google"

    def test_tools(self, google_plugin):
        assert google_plugin.tools == ["gog"]

    def test_no_custom_commands(self, google_plugin):
        assert google_plugin.get_commands() == {}

    def test_no_custom_routes(self, google_plugin, google_config):
        cfg = google_config["plugins"]["google"]
        assert google_plugin.get_routes(cfg) == []


class TestGooglePluginRouting:
    async def test_routes_gog_tool(self, google_client):
        resp = await google_client.post("/cli", json={
            "tool": "gog",
            "args": ["calendar", "events"],
            "resource": "default",
        })
        assert resp.status == 200
        data = await resp.json()
        # gog binary not installed in test env, but routing works
        assert "exit_code" in data

    async def test_credential_selection(self, google_plugin, google_config):
        cfg = google_config["plugins"]["google"]
        result = google_plugin.select_credential("user@example.com", cfg)
        assert result["env"]["GOG_KEYRING_PASSWORD"] == "test-pw"
        assert result["env"]["GOG_ACCOUNT"] == "user@example.com"

    async def test_wildcard_credential(self, google_plugin, google_config):
        cfg = google_config["plugins"]["google"]
        result = google_plugin.select_credential("other@example.com", cfg)
        assert result["env"]["GOG_KEYRING_PASSWORD"] == "default-pw"
        assert "GOG_ACCOUNT" not in result["env"]

    async def test_no_credential_returns_403(self, google_plugin):
        config = {"plugins": {"google": {"credentials": [
            {"keyring_password": "pw", "resources": ["specific@only.com"]},
        ]}}}
        app = create_routes(config, {"google": google_plugin})
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/cli", json={
                "tool": "gog",
                "args": ["calendar", "events"],
                "resource": "other@example.com",
            })
            assert resp.status == 403

    async def test_health_is_lightweight(self, google_client):
        resp = await google_client.get("/health")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"
        assert "plugins" not in data

    async def test_auth_status_includes_google(self, google_client):
        resp = await google_client.get("/auth/status")
        assert resp.status == 200
        data = await resp.json()
        assert "plugins" in data
        assert "google" in data["plugins"]
