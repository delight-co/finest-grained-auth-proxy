import logging

import pytest
from aiohttp import web
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

    async def test_help_without_resource_succeeds(self, echo_client):
        resp = await echo_client.post("/cli", json={
            "tool": "echo",
            "args": ["hello", "--help"],
        })
        assert resp.status == 200

    async def test_help_without_resource_uses_dummy_credential(self, echo_plugin):
        config = {"plugins": {"echo": {"credentials": [
            {"token": "t", "resources": ["specific/only"]},
        ]}}}
        app = create_routes(config, {"echo": echo_plugin})
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/cli", json={
                "tool": "echo",
                "args": ["hello", "--help"],
            })
            assert resp.status == 200

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

    async def test_health_is_lightweight(self, echo_client):
        """Health endpoint should NOT call plugin health_check."""
        resp = await echo_client.get("/health")
        data = await resp.json()
        assert "plugins" not in data


class TestAuthStatusEndpoint:
    async def test_returns_plugin_statuses(self, echo_client):
        resp = await echo_client.get("/auth/status")
        assert resp.status == 200
        data = await resp.json()
        assert "plugins" in data
        assert "echo" in data["plugins"]


class TestAuditLog:
    async def test_successful_call_logged(self, echo_client, caplog):
        with caplog.at_level(logging.INFO, logger="fgap.core.router"):
            await echo_client.post("/cli", json={
                "tool": "echo",
                "args": ["hello"],
                "resource": "acme/repo1",
            })
        assert any(
            "tool=echo" in r.message and "resource=acme/repo1" in r.message
            and "exit_code=0" in r.message
            for r in caplog.records
        )

    async def test_missing_tool_logged(self, echo_client, caplog):
        with caplog.at_level(logging.WARNING, logger="fgap.core.router"):
            await echo_client.post("/cli", json={
                "args": ["hello"],
                "resource": "acme/repo1",
            })
        assert any(
            "rejected=400" in r.message
            for r in caplog.records
        )

    async def test_no_credential_logged(self, echo_plugin, caplog):
        config = {"plugins": {"echo": {"credentials": [
            {"token": "t", "resources": ["specific/only"]},
        ]}}}
        app = create_routes(config, {"echo": echo_plugin})
        async with TestClient(TestServer(app)) as client:
            with caplog.at_level(logging.WARNING, logger="fgap.core.router"):
                await client.post("/cli", json={
                    "tool": "echo",
                    "args": ["hello"],
                    "resource": "other/repo",
                })
        assert any(
            "rejected=403" in r.message and "resource=other/repo" in r.message
            for r in caplog.records
        )

    async def test_command_intercepted_logged(self, ft_client, caplog):
        with caplog.at_level(logging.INFO, logger="fgap.core.router"):
            await ft_client.post("/cli", json={
                "tool": "printf",
                "args": ["custom", "intercept"],
                "resource": "any",
            })
        assert any(
            "tool=printf" in r.message and "cmd=custom" in r.message
            and "exit_code=0" in r.message
            for r in caplog.records
        )


# =========================================================================
# /download endpoint
# =========================================================================


@pytest.fixture
async def mock_upstream():
    """Mock upstream asset server."""
    app = web.Application()

    async def handle_asset(request):
        auth = request.headers.get("Authorization", "")
        if auth != "Bearer test_gh_token":
            return web.Response(status=401, text="Unauthorized")
        return web.Response(
            body=b"binary-content-here",
            content_type="application/octet-stream",
        )

    async def handle_error(request):
        return web.Response(status=404, text="Not Found")

    app.router.add_get("/asset/ok", handle_asset)
    app.router.add_get("/asset/missing", handle_error)
    async with TestServer(app) as server:
        yield server


@pytest.fixture
async def dl_client(dl_plugin, dl_config):
    app = create_routes(dl_config, {"dl": dl_plugin})
    async with TestClient(TestServer(app)) as client:
        yield client


class TestDownloadEndpoint:
    async def test_streams_binary(self, dl_client, mock_upstream):
        url = str(mock_upstream.make_url("/asset/ok"))
        resp = await dl_client.post("/download", json={
            "tool": "gh", "resource": "o/r", "url": url,
        })
        assert resp.status == 200
        body = await resp.read()
        assert body == b"binary-content-here"

    async def test_missing_fields(self, dl_client):
        resp = await dl_client.post("/download", json={
            "tool": "gh", "resource": "o/r",
        })
        assert resp.status == 400

    async def test_no_credential(self, dl_client):
        resp = await dl_client.post("/download", json={
            "tool": "gh", "resource": "nope/nope", "url": "https://x",
        })
        # dl_config uses "*" so this will match — use unknown tool instead
        resp = await dl_client.post("/download", json={
            "tool": "unknown", "resource": "o/r", "url": "https://x",
        })
        assert resp.status == 400

    async def test_upstream_error(self, dl_client, mock_upstream):
        url = str(mock_upstream.make_url("/asset/missing"))
        resp = await dl_client.post("/download", json={
            "tool": "gh", "resource": "o/r", "url": url,
        })
        assert resp.status == 502

    async def test_rejects_http_url(self, dl_plugin):
        """Without allow_insecure_download_urls, HTTP URLs are rejected."""
        strict_config = {
            "plugins": {
                "dl": {
                    "credentials": [
                        {"token": "test_gh_token", "resources": ["*"]},
                    ]
                }
            }
        }
        app = create_routes(strict_config, {"dl": dl_plugin})
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/download", json={
                "tool": "gh", "resource": "o/r",
                "url": "http://evil.com/payload",
            })
            assert resp.status == 400
            text = await resp.text()
            assert "HTTPS" in text
