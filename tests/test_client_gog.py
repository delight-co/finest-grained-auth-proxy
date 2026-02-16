import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from fgap.client.gog import detect_account_from_args, run


# =========================================================================
# Pure logic: account detection
# =========================================================================


class TestDetectAccountFromArgs:
    def test_account_space(self):
        assert detect_account_from_args(["--account", "u@e.com"]) == "u@e.com"

    def test_account_equals(self):
        assert detect_account_from_args(["--account=u@e.com"]) == "u@e.com"

    def test_mixed_args(self):
        assert detect_account_from_args(
            ["calendar", "events", "--account", "u@e.com"]
        ) == "u@e.com"

    def test_none(self):
        assert detect_account_from_args(["calendar", "events"]) is None

    def test_account_at_end(self):
        assert detect_account_from_args(["--account"]) is None


# =========================================================================
# Mock helpers
# =========================================================================


@pytest.fixture
async def mock_proxy():
    """Mock fgap proxy."""
    app = web.Application()
    state = {"responses": [], "requests": []}

    async def handle_cli(request):
        data = await request.json()
        state["requests"].append(data)
        if state["responses"]:
            return state["responses"].pop(0)
        return web.json_response({"exit_code": 0, "stdout": "", "stderr": ""})

    app.router.add_post("/cli", handle_cli)
    async with TestServer(app) as server:
        yield server, state


def _url(server) -> str:
    return str(server.make_url(""))


# =========================================================================
# run(): help display
# =========================================================================


class TestHelp:
    async def test_no_args(self, capsys):
        code = await run([], "http://unused")
        assert code == 0
        assert "fgap-gog" in capsys.readouterr().out

    async def test_help_flag(self, capsys):
        code = await run(["--help"], "http://unused")
        assert code == 0
        assert "COMMANDS" in capsys.readouterr().out

    async def test_h_flag(self, capsys):
        code = await run(["-h"], "http://unused")
        assert code == 0
        assert "calendar" in capsys.readouterr().out


# =========================================================================
# run(): resource detection
# =========================================================================


class TestResourceDetection:
    async def test_from_account_flag(self, mock_proxy):
        server, state = mock_proxy
        await run(["calendar", "events", "--account", "u@e.com"], _url(server))
        assert state["requests"][0]["resource"] == "u@e.com"

    async def test_from_account_equals(self, mock_proxy):
        server, state = mock_proxy
        await run(["calendar", "events", "--account=u@e.com"], _url(server))
        assert state["requests"][0]["resource"] == "u@e.com"

    async def test_from_env(self, mock_proxy, monkeypatch):
        server, state = mock_proxy
        monkeypatch.setenv("GOG_ACCOUNT", "env@e.com")
        await run(["calendar", "events"], _url(server))
        assert state["requests"][0]["resource"] == "env@e.com"

    async def test_account_flag_over_env(self, mock_proxy, monkeypatch):
        server, state = mock_proxy
        monkeypatch.setenv("GOG_ACCOUNT", "env@e.com")
        await run(["calendar", "events", "--account", "flag@e.com"], _url(server))
        assert state["requests"][0]["resource"] == "flag@e.com"

    async def test_default_when_no_account(self, mock_proxy, monkeypatch):
        server, state = mock_proxy
        monkeypatch.delenv("GOG_ACCOUNT", raising=False)
        await run(["calendar", "events"], _url(server))
        assert state["requests"][0]["resource"] == "default"


# =========================================================================
# run(): proxy call and output
# =========================================================================


class TestProxyCallAndOutput:
    async def test_stdout_printed(self, mock_proxy, capsys):
        server, state = mock_proxy
        state["responses"].append(
            web.json_response({"exit_code": 0, "stdout": "events list", "stderr": ""}),
        )
        code = await run(["calendar", "events"], _url(server))
        assert code == 0
        assert "events list" in capsys.readouterr().out

    async def test_stderr_printed(self, mock_proxy, capsys):
        server, state = mock_proxy
        state["responses"].append(
            web.json_response({"exit_code": 0, "stdout": "", "stderr": "info msg"}),
        )
        await run(["calendar", "events"], _url(server))
        assert "info msg" in capsys.readouterr().err

    async def test_nonzero_exit_code(self, mock_proxy, capsys):
        server, state = mock_proxy
        state["responses"].append(
            web.json_response({"exit_code": 1, "stdout": "", "stderr": "auth error"}),
        )
        code = await run(["calendar", "events"], _url(server))
        assert code == 1
        assert "auth error" in capsys.readouterr().err

    async def test_connection_error(self, capsys):
        code = await run(["calendar", "events"], "http://127.0.0.1:1")
        assert code == 1
        assert "Cannot connect" in capsys.readouterr().err

    async def test_proxy_html_error(self, mock_proxy, capsys):
        server, state = mock_proxy
        state["responses"].append(
            web.Response(text="<html>Error</html>", content_type="text/html"),
        )
        code = await run(["calendar", "events"], _url(server))
        assert code == 1
        assert "HTML" in capsys.readouterr().err


# =========================================================================
# run(): full flow
# =========================================================================


class TestFullFlow:
    async def test_args_passed_through(self, mock_proxy):
        server, state = mock_proxy
        await run(["sheets", "get", "abc123", "Tab!A1:D10", "--json"], _url(server))
        req = state["requests"][0]
        assert req["tool"] == "gog"
        assert req["args"] == ["sheets", "get", "abc123", "Tab!A1:D10", "--json"]

    async def test_account_not_stripped(self, mock_proxy):
        server, state = mock_proxy
        await run(
            ["calendar", "events", "--account", "u@e.com"],
            _url(server),
        )
        req = state["requests"][0]
        assert "--account" in req["args"]
        assert "u@e.com" in req["args"]
