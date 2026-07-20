"""Tests for the generic HTTP forward proxy plugin."""

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from fgap.plugins.http_proxy.proxy import make_routes, _select_token


# =============================================================================
# Token selection
# =============================================================================


class TestSelectToken:
    def test_wildcard(self):
        config = {"credentials": [{"token": "tok_1", "resources": ["*"]}]}
        assert _select_token("default", config) == "tok_1"

    def test_first_match_wins(self):
        config = {"credentials": [
            {"token": "tok_specific", "resources": ["workspace-a"]},
            {"token": "tok_default", "resources": ["*"]},
        ]}
        assert _select_token("workspace-a", config) == "tok_specific"

    def test_no_match(self):
        config = {"credentials": [
            {"token": "tok_1", "resources": ["workspace-a"]},
        ]}
        assert _select_token("workspace-b", config) is None

    def test_empty_credentials(self):
        assert _select_token("default", {"credentials": []}) is None

    def test_no_credentials_key(self):
        assert _select_token("default", {}) is None


# =============================================================================
# Route generation
# =============================================================================


class TestMakeRoutes:
    def test_no_services_returns_empty(self):
        assert make_routes({}) == []
        assert make_routes({"services": {}}) == []

    def test_creates_routes_for_all_methods(self):
        config = {"services": {"test": {"upstream": "https://example.com"}}}
        routes = make_routes(config)
        methods = {r[0] for r in routes}
        assert methods == {"GET", "POST", "PUT", "PATCH", "DELETE"}


# =============================================================================
# HTTP proxy integration
# =============================================================================


@pytest.fixture
async def mock_upstream():
    """Mock upstream API server."""
    app = web.Application()
    state = {"requests": []}

    async def handle(request: web.Request):
        body = await request.read()
        state["requests"].append({
            "method": request.method,
            "path": request.path,
            "query": request.query_string,
            "headers": dict(request.headers),
            "body": body.decode() if body else "",
        })
        return web.json_response({"ok": True, "method": request.method})

    app.router.add_route("*", "/{path:.*}", handle)
    async with TestServer(app) as server:
        yield server, state


@pytest.fixture
async def proxy_app(mock_upstream):
    """Create fgap app with http_proxy pointing to mock upstream."""
    server, state = mock_upstream
    upstream_url = str(server.make_url(""))

    config = {
        "services": {
            "testapi": {
                "upstream": upstream_url,
                "auth": "bearer",
                "credentials": [
                    {"token": "test_token_123", "resources": ["*"]},
                ],
            },
            "testapi_basic": {
                "upstream": upstream_url,
                "auth": "basic",
                "credentials": [
                    {"token": "user:pass", "resources": ["*"]},
                ],
            },
            "testapi_headers": {
                "upstream": upstream_url,
                "auth": "bearer",
                "extra_headers": {"X-Custom": "value"},
                "credentials": [
                    {"token": "tok", "resources": ["*"]},
                ],
            },
            "testapi_nocred": {
                "upstream": upstream_url,
                "auth": "bearer",
                "credentials": [],
            },
            "testapi_header": {
                "upstream": upstream_url,
                "auth": "header",
                "header_name": "x-api-key",
                "credentials": [
                    {"token": "secret_key_abc", "resources": ["*"]},
                ],
            },
        },
    }

    routes = make_routes(config)
    app = web.Application()
    for method, path, handler in routes:
        app.router.add_route(method, path, handler)

    async with TestServer(app) as proxy_server:
        yield proxy_server, state


class TestHttpProxy:
    async def test_get_forwarded(self, proxy_app):
        proxy, state = proxy_app
        import aiohttp
        async with aiohttp.ClientSession() as session:
            url = str(proxy.make_url("/proxy/testapi/api/v1/items"))
            async with session.get(url) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["ok"] is True
                assert data["method"] == "GET"

        req = state["requests"][-1]
        assert req["path"] == "/api/v1/items"
        assert "Bearer test_token_123" in req["headers"].get("Authorization", "")

    async def test_post_forwarded(self, proxy_app):
        proxy, state = proxy_app
        import aiohttp
        async with aiohttp.ClientSession() as session:
            url = str(proxy.make_url("/proxy/testapi/api/v1/items"))
            async with session.post(url, json={"name": "test"}) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["method"] == "POST"

        req = state["requests"][-1]
        assert req["method"] == "POST"
        assert '"name"' in req["body"]

    async def test_query_string_forwarded(self, proxy_app):
        proxy, state = proxy_app
        import aiohttp
        async with aiohttp.ClientSession() as session:
            url = str(proxy.make_url("/proxy/testapi/api/v1/items?page=2&limit=10"))
            async with session.get(url) as resp:
                assert resp.status == 200

        req = state["requests"][-1]
        assert "page=2" in req["query"]
        assert "limit=10" in req["query"]

    async def test_unknown_service_returns_404(self, proxy_app):
        proxy, state = proxy_app
        import aiohttp
        async with aiohttp.ClientSession() as session:
            url = str(proxy.make_url("/proxy/unknown/path"))
            async with session.get(url) as resp:
                assert resp.status == 404

    async def test_no_credential_returns_403(self, proxy_app):
        proxy, state = proxy_app
        import aiohttp
        async with aiohttp.ClientSession() as session:
            url = str(proxy.make_url("/proxy/testapi_nocred/path"))
            async with session.get(url) as resp:
                assert resp.status == 403

    async def test_basic_auth(self, proxy_app):
        proxy, state = proxy_app
        import aiohttp
        async with aiohttp.ClientSession() as session:
            url = str(proxy.make_url("/proxy/testapi_basic/path"))
            async with session.get(url) as resp:
                assert resp.status == 200

        req = state["requests"][-1]
        assert req["headers"]["Authorization"].startswith("Basic ")

    async def test_extra_headers_injected(self, proxy_app):
        proxy, state = proxy_app
        import aiohttp
        async with aiohttp.ClientSession() as session:
            url = str(proxy.make_url("/proxy/testapi_headers/path"))
            async with session.get(url) as resp:
                assert resp.status == 200

        req = state["requests"][-1]
        assert req["headers"].get("X-Custom") == "value"

    async def test_resource_param_stripped(self, proxy_app):
        proxy, state = proxy_app
        import aiohttp
        async with aiohttp.ClientSession() as session:
            url = str(proxy.make_url("/proxy/testapi/path?_resource=ws1&page=1"))
            async with session.get(url) as resp:
                assert resp.status == 200

        req = state["requests"][-1]
        assert "_resource" not in req["query"]
        assert "page=1" in req["query"]

    async def test_header_auth_injects_named_header(self, proxy_app):
        proxy, state = proxy_app
        import aiohttp
        async with aiohttp.ClientSession() as session:
            url = str(proxy.make_url("/proxy/testapi_header/path"))
            async with session.get(url) as resp:
                assert resp.status == 200

        req = state["requests"][-1]
        assert req["headers"].get("x-api-key") == "secret_key_abc"
        # Authorization must not be set — header auth leaves it alone
        assert "Authorization" not in req["headers"]

    async def test_mcp_request_headers_forwarded(self, proxy_app):
        proxy, state = proxy_app
        import aiohttp
        async with aiohttp.ClientSession() as session:
            url = str(proxy.make_url("/proxy/testapi_header/mcp"))
            async with session.post(url, json={"jsonrpc": "2.0", "id": 1}, headers={
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-03-26",
                "Mcp-Session-Id": "abc-123",
                "Origin": "http://client.local",
            }) as resp:
                assert resp.status == 200

        req = state["requests"][-1]
        # All four MCP-relevant headers must reach the upstream verbatim.
        assert req["headers"].get("Accept") == "application/json, text/event-stream"
        assert req["headers"].get("MCP-Protocol-Version") == "2025-03-26"
        assert req["headers"].get("Mcp-Session-Id") == "abc-123"
        assert req["headers"].get("Origin") == "http://client.local"


@pytest.fixture
async def echoing_response_upstream():
    """Upstream that echoes MCP-shaped response headers back to caller."""
    app = web.Application()

    async def handle(request: web.Request):
        return web.Response(
            body=b'{"jsonrpc":"2.0","id":1,"result":{}}',
            headers={
                "Content-Type": "application/json",
                "Mcp-Session-Id": "server-issued-sid",
                "MCP-Protocol-Version": "2025-03-26",
                "X-Should-Not-Leak": "secret",
            },
        )

    app.router.add_route("*", "/{path:.*}", handle)
    async with TestServer(app) as server:
        yield server


class TestResponseHeaderPassthrough:
    async def test_mcp_response_headers_forwarded(self, echoing_response_upstream):
        upstream_url = str(echoing_response_upstream.make_url(""))
        routes = make_routes({"services": {
            "echo": {
                "upstream": upstream_url,
                "auth": "header",
                "header_name": "x-api-key",
                "credentials": [{"token": "k", "resources": ["*"]}],
            },
        }})
        app = web.Application()
        for method, path, handler in routes:
            app.router.add_route(method, path, handler)
        async with TestServer(app) as proxy_server:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                url = str(proxy_server.make_url("/proxy/echo/mcp"))
                async with session.post(url, json={}) as resp:
                    assert resp.status == 200
                    # MCP session/protocol headers reach the client.
                    assert resp.headers.get("Mcp-Session-Id") == "server-issued-sid"
                    assert resp.headers.get("MCP-Protocol-Version") == "2025-03-26"
                    # Allowlist stays closed for anything else.
                    assert "X-Should-Not-Leak" not in resp.headers


class TestStartupValidation:
    def test_unknown_auth_mode_raises_at_startup(self):
        with pytest.raises(ValueError, match="unknown auth mode"):
            make_routes({"services": {"svc": {
                "upstream": "https://example.invalid",
                "auth": "sigv4",  # not supported
                "credentials": [{"token": "t", "resources": ["*"]}],
            }}})

    def test_header_auth_without_header_name_raises_at_startup(self):
        with pytest.raises(ValueError, match="header_name"):
            make_routes({"services": {"svc": {
                "upstream": "https://example.invalid",
                "auth": "header",
                "credentials": [{"token": "t", "resources": ["*"]}],
            }}})

    def test_header_auth_with_header_name_ok(self):
        # Just checking make_routes returns without raising.
        routes = make_routes({"services": {"svc": {
            "upstream": "https://example.invalid",
            "auth": "header",
            "header_name": "x-api-key",
            "credentials": [{"token": "t", "resources": ["*"]}],
        }}})
        assert routes  # non-empty
