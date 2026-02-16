import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from fgap.client.base import ProxyClient


# =========================================================================
# Mock proxy server
# =========================================================================


@pytest.fixture
async def mock_proxy():
    """Mock fgap proxy that records requests and returns queued responses."""
    app = web.Application()
    state = {"responses": [], "requests": []}

    async def handle_cli(request):
        data = await request.json()
        state["requests"].append(data)
        if not state["responses"]:
            return web.json_response(
                {"exit_code": 0, "stdout": "", "stderr": ""},
            )
        return state["responses"].pop(0)

    app.router.add_post("/cli", handle_cli)
    async with TestServer(app) as server:
        yield server, state


def _client(server) -> ProxyClient:
    return ProxyClient(str(server.make_url("")))


# =========================================================================
# Successful calls
# =========================================================================


class TestCallCli:
    async def test_sends_correct_request(self, mock_proxy):
        server, state = mock_proxy
        client = _client(server)

        await client.call_cli("gh", ["issue", "list"], "owner/repo")

        assert len(state["requests"]) == 1
        req = state["requests"][0]
        assert req["tool"] == "gh"
        assert req["args"] == ["issue", "list"]
        assert req["resource"] == "owner/repo"

    async def test_returns_result(self, mock_proxy):
        server, state = mock_proxy
        state["responses"].append(
            web.json_response({"exit_code": 0, "stdout": "output", "stderr": "info"}),
        )
        client = _client(server)

        result = await client.call_cli("gh", ["pr", "view", "1"], "o/r")

        assert result == {"exit_code": 0, "stdout": "output", "stderr": "info"}

    async def test_nonzero_exit_code(self, mock_proxy):
        server, state = mock_proxy
        state["responses"].append(
            web.json_response({"exit_code": 1, "stdout": "", "stderr": "not found"}),
        )
        client = _client(server)

        result = await client.call_cli("gh", ["issue", "view", "999"], "o/r")

        assert result["exit_code"] == 1
        assert result["stderr"] == "not found"

    async def test_missing_stdout_stderr_default_to_empty(self, mock_proxy):
        server, state = mock_proxy
        state["responses"].append(
            web.json_response({"exit_code": 0}),
        )
        client = _client(server)

        result = await client.call_cli("gh", ["issue", "list"], "o/r")

        assert result["stdout"] == ""
        assert result["stderr"] == ""

    async def test_trailing_slash_in_proxy_url(self, mock_proxy):
        server, _ = mock_proxy
        client = ProxyClient(str(server.make_url("")) + "/")

        result = await client.call_cli("gh", ["issue", "list"], "o/r")

        assert result["exit_code"] == 0


# =========================================================================
# Error handling
# =========================================================================


class TestErrorHandling:
    async def test_html_response_raises_value_error(self, mock_proxy):
        server, state = mock_proxy
        state["responses"].append(
            web.Response(
                text="<html><body>Error</body></html>",
                content_type="text/html",
            ),
        )
        client = _client(server)

        with pytest.raises(ValueError, match="HTML"):
            await client.call_cli("gh", ["issue", "list"], "o/r")

    async def test_400_raises_value_error(self, mock_proxy):
        server, state = mock_proxy
        state["responses"].append(
            web.Response(text="Missing 'tool' field", status=400),
        )
        client = _client(server)

        with pytest.raises(ValueError, match="400"):
            await client.call_cli("gh", [], "o/r")

    async def test_403_raises_value_error(self, mock_proxy):
        server, state = mock_proxy
        state["responses"].append(
            web.Response(text="No credential for gh on o/r", status=403),
        )
        client = _client(server)

        with pytest.raises(ValueError, match="403"):
            await client.call_cli("gh", ["issue", "list"], "o/r")

    async def test_500_raises_value_error(self, mock_proxy):
        server, state = mock_proxy
        state["responses"].append(
            web.Response(text="Internal Server Error", status=500),
        )
        client = _client(server)

        with pytest.raises(ValueError, match="500"):
            await client.call_cli("gh", ["issue", "list"], "o/r")

    async def test_missing_exit_code_raises_value_error(self, mock_proxy):
        server, state = mock_proxy
        state["responses"].append(
            web.json_response({"stdout": "no exit_code"}),
        )
        client = _client(server)

        with pytest.raises(ValueError, match="exit_code"):
            await client.call_cli("gh", ["issue", "list"], "o/r")

    async def test_connection_refused_raises_connection_error(self):
        client = ProxyClient("http://127.0.0.1:1")

        with pytest.raises(ConnectionError, match="Cannot connect"):
            await client.call_cli("gh", ["issue", "list"], "o/r")

    async def test_timeout(self):
        """Proxy that never responds triggers timeout."""
        app = web.Application()

        async def slow_handler(request):
            import asyncio
            await asyncio.sleep(10)
            return web.json_response({"exit_code": 0})

        app.router.add_post("/cli", slow_handler)
        async with TestServer(app) as server:
            client = ProxyClient(str(server.make_url("")), timeout=1)

            with pytest.raises(TimeoutError):
                await client.call_cli("gh", ["issue", "list"], "o/r")
