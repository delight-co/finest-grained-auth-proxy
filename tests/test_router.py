import pytest
from aiohttp.test_utils import TestClient, TestServer

from fgap.core.router import create_routes


@pytest.fixture
async def echo_client(echo_plugin, echo_config):
    app = create_routes(echo_config, {"echo": echo_plugin})
    async with TestClient(TestServer(app)) as client:
        yield client


@pytest.fixture
async def ft_client(ft_plugin, ft_config):
    app = create_routes(ft_config, {"ft": ft_plugin})
    async with TestClient(TestServer(app)) as client:
        yield client


class TestCliEndpoint:
    async def test_successful_call(self, echo_client):
        resp = await echo_client.post("/cli", json={
            "tool": "echo",
            "args": ["hello"],
            "resource": "acme/repo1",
        })
        assert resp.status == 200
        data = await resp.json()
        assert data["exit_code"] == 0
        assert "hello" in data["stdout"]

    async def test_missing_tool_returns_400(self, echo_client):
        resp = await echo_client.post("/cli", json={
            "args": ["hello"],
            "resource": "acme/repo1",
        })
        assert resp.status == 400

    async def test_missing_resource_returns_400(self, echo_client):
        resp = await echo_client.post("/cli", json={
            "tool": "echo",
            "args": ["hello"],
        })
        assert resp.status == 400

    async def test_unknown_tool_returns_400(self, echo_client):
        resp = await echo_client.post("/cli", json={
            "tool": "unknown",
            "args": [],
            "resource": "acme/repo1",
        })
        assert resp.status == 400

    async def test_no_credential_returns_403(self, echo_plugin):
        config = {"plugins": {"echo": {"credentials": [
            {"token": "t", "resources": ["specific/only"]},
        ]}}}
        app = create_routes(config, {"echo": echo_plugin})
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/cli", json={
                "tool": "echo",
                "args": ["hello"],
                "resource": "other/repo",
            })
            assert resp.status == 403

    async def test_credential_selection_uses_first_match(self, echo_client):
        resp = await echo_client.post("/cli", json={
            "tool": "echo",
            "args": ["specific"],
            "resource": "acme/repo1",
        })
        assert resp.status == 200

    async def test_wildcard_credential(self, echo_client):
        resp = await echo_client.post("/cli", json={
            "tool": "echo",
            "args": ["wild"],
            "resource": "other/repo",
        })
        assert resp.status == 200
        data = await resp.json()
        assert data["exit_code"] == 0


class TestCommandFallthrough:
    async def test_command_intercepted(self, ft_client):
        resp = await ft_client.post("/cli", json={
            "tool": "printf",
            "args": ["custom", "intercept"],
            "resource": "any",
        })
        assert resp.status == 200
        data = await resp.json()
        assert data["stdout"] == "intercepted"
        assert data["exit_code"] == 0

    async def test_command_falls_through_to_cli(self, ft_client):
        resp = await ft_client.post("/cli", json={
            "tool": "printf",
            "args": ["custom", "passthrough"],
            "resource": "any",
        })
        assert resp.status == 200
        data = await resp.json()
        # Falls through: printf "custom" "passthrough" → outputs "custom"
        assert data["exit_code"] == 0

    async def test_no_matching_command_goes_to_cli(self, ft_client):
        resp = await ft_client.post("/cli", json={
            "tool": "printf",
            "args": ["%s\\n", "direct"],
            "resource": "any",
        })
        assert resp.status == 200
        data = await resp.json()
        assert "direct" in data["stdout"]


class TestHealthEndpoint:
    async def test_health_returns_ok(self, echo_client):
        resp = await echo_client.get("/health")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"
        assert "echo" in data["plugins"]
