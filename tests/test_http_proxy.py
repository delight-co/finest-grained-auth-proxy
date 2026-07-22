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


# =============================================================================
# Streaming (SSE) relay
# =============================================================================


@pytest.fixture
async def sse_upstream():
    """Mock upstream that streams SSE events, gated by an asyncio.Event."""
    import asyncio

    app = web.Application()
    state = {"release": asyncio.Event(), "requests": []}

    async def handle_events(request: web.Request):
        state["requests"].append({"headers": dict(request.headers)})
        resp = web.StreamResponse(
            status=200,
            headers={"Content-Type": "text/event-stream"},
        )
        await resp.prepare(request)
        await resp.write(b"event: one\ndata: {}\n\n")
        await state["release"].wait()
        await resp.write(b"event: two\ndata: {}\n\n")
        await resp.write_eof()
        return resp

    async def handle_json(request: web.Request):
        return web.json_response({"ok": True})

    app.router.add_post("/events", handle_events)
    app.router.add_get("/json", handle_json)
    async with TestServer(app) as server:
        yield server, state


@pytest.fixture
async def sse_proxy(sse_upstream):
    """fgap http_proxy in front of the SSE upstream, streaming enabled."""
    server, state = sse_upstream
    config = {
        "services": {
            "llm": {
                "upstream": str(server.make_url("")),
                "auth": "bearer",
                "streaming": True,
                "credentials": [{"token": "tok", "resources": ["*"]}],
            },
        },
    }
    routes = make_routes(config)
    app = web.Application()
    for method, path, handler in routes:
        app.router.add_route(method, path, handler)
    async with TestServer(app) as proxy_server:
        yield proxy_server, state


class TestStreamingRelay:
    async def test_sse_relayed_incrementally(self, sse_proxy):
        """First event must arrive while the upstream is still open —
        a buffering implementation would block until the stream ends."""
        import asyncio

        import aiohttp

        proxy, state = sse_proxy
        url = str(proxy.make_url("/proxy/llm/events"))
        async with aiohttp.ClientSession() as session:
            async with session.post(url) as resp:
                assert resp.status == 200
                ctype = resp.headers.get("Content-Type", "")
                assert ctype.startswith("text/event-stream")
                assert resp.headers.get("X-Accel-Buffering") == "no"
                assert "Content-Length" not in resp.headers

                first = await asyncio.wait_for(
                    resp.content.readany(), timeout=5,
                )
                assert b"event: one" in first
                assert b"event: two" not in first

                state["release"].set()
                rest = await asyncio.wait_for(resp.read(), timeout=5)
                assert b"event: two" in rest

    async def test_non_sse_response_stays_buffered(self, sse_proxy):
        import aiohttp

        proxy, state = sse_proxy
        url = str(proxy.make_url("/proxy/llm/json"))
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["ok"] is True


# =============================================================================
# Per-service header controls
# =============================================================================


@pytest.fixture
async def header_proxy(mock_upstream):
    """Proxy with forward_request_headers / append_headers configured."""
    server, state = mock_upstream
    upstream_url = str(server.make_url(""))
    config = {
        "services": {
            "plain": {
                "upstream": upstream_url,
                "auth": "bearer",
                "credentials": [{"token": "tok", "resources": ["*"]}],
            },
            "tuned": {
                "upstream": upstream_url,
                "auth": "bearer",
                "forward_request_headers": [
                    "anthropic-version", "anthropic-beta",
                ],
                "append_headers": {"anthropic-beta": "oauth-2025-04-20"},
                "credentials": [{"token": "tok", "resources": ["*"]}],
            },
        },
    }
    routes = make_routes(config)
    app = web.Application()
    for method, path, handler in routes:
        app.router.add_route(method, path, handler)
    async with TestServer(app) as proxy_server:
        yield proxy_server, state


class TestHeaderControls:
    async def _get(self, proxy, service, headers):
        import aiohttp

        async with aiohttp.ClientSession() as session:
            url = str(proxy.make_url(f"/proxy/{service}/x"))
            async with session.get(url, headers=headers) as resp:
                assert resp.status == 200

    async def test_extra_forward_header(self, header_proxy):
        proxy, state = header_proxy
        await self._get(proxy, "tuned", {"anthropic-version": "2023-06-01"})
        req = state["requests"][-1]
        assert req["headers"].get("anthropic-version") == "2023-06-01"

    async def test_unlisted_header_not_forwarded(self, header_proxy):
        proxy, state = header_proxy
        await self._get(proxy, "plain", {"anthropic-version": "2023-06-01"})
        req = state["requests"][-1]
        assert "anthropic-version" not in req["headers"]

    async def test_append_header_set_when_absent(self, header_proxy):
        proxy, state = header_proxy
        await self._get(proxy, "tuned", {})
        req = state["requests"][-1]
        assert req["headers"].get("anthropic-beta") == "oauth-2025-04-20"

    async def test_append_header_merged_with_client_value(self, header_proxy):
        proxy, state = header_proxy
        await self._get(proxy, "tuned", {"anthropic-beta": "context-1m"})
        req = state["requests"][-1]
        assert req["headers"].get("anthropic-beta") == (
            "context-1m,oauth-2025-04-20"
        )

    async def test_append_header_not_duplicated(self, header_proxy):
        proxy, state = header_proxy
        await self._get(
            proxy, "tuned", {"anthropic-beta": "oauth-2025-04-20"},
        )
        req = state["requests"][-1]
        assert req["headers"].get("anthropic-beta") == "oauth-2025-04-20"

    async def test_config_validation_rejects_bad_types(self):
        with pytest.raises(ValueError):
            make_routes({"services": {"bad": {
                "upstream": "https://x", "forward_request_headers": "nope",
            }}})
        with pytest.raises(ValueError):
            make_routes({"services": {"bad": {
                "upstream": "https://x", "append_headers": ["nope"],
            }}})

    async def test_client_abort_mid_stream_is_not_an_error(
        self, sse_proxy, caplog,
    ):
        """A client hanging up mid-stream must not raise through the
        handler (consumer aborts are routine for SSE)."""
        import asyncio
        import logging

        import aiohttp

        proxy, state = sse_proxy
        url = str(proxy.make_url("/proxy/llm/events"))
        with caplog.at_level(logging.ERROR):
            session = aiohttp.ClientSession()
            resp = await session.post(url)
            first = await asyncio.wait_for(resp.content.readany(), timeout=5)
            assert b"event: one" in first
            # Abort without reading the rest, then let the relay notice.
            await session.close()
            state["release"].set()
            await asyncio.sleep(0.1)

        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert errors == []
