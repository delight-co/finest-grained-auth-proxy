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
