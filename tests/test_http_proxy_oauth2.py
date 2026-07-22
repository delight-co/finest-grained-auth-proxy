"""Tests for OAuth2 token refresh in the HTTP proxy plugin."""

import json
import os
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from fgap.plugins.http_proxy.oauth2 import OAuth2TokenManager
from fgap.plugins.http_proxy.proxy import make_routes


# =============================================================================
# OAuth2TokenManager unit tests
# =============================================================================


@pytest.fixture
async def mock_token_server():
    """Mock OAuth2 token endpoint."""
    app = web.Application()
    state = {
        "call_count": 0,
        "next_access_token": "new_access_token",
        "next_refresh_token": "new_refresh_token",
        "next_expires_in": 3600,
        "fail": False,
    }

    async def handle_token(request: web.Request):
        state["call_count"] += 1
        if state["fail"]:
            return web.json_response(
                {"error": "invalid_grant"}, status=400,
            )
        data = await request.post()
        state["last_grant_type"] = data.get("grant_type")
        state["last_refresh_token"] = data.get("refresh_token")
        state["last_client_id"] = data.get("client_id")
        return web.json_response({
            "access_token": state["next_access_token"],
            "refresh_token": state["next_refresh_token"],
            "expires_in": state["next_expires_in"],
            "token_type": "bearer",
        })

    app.router.add_post("/token", handle_token)
    async with TestServer(app) as server:
        yield server, state


class TestOAuth2TokenManager:
    async def test_refresh_on_first_use(self, mock_token_server, tmp_path):
        server, state = mock_token_server
        token_url = str(server.make_url("/token"))

        mgr = OAuth2TokenManager(
            service_name="test",
            token_url=token_url,
            client_id="cid",
            client_secret="csec",
            initial_refresh_token="initial_rt",
            state_dir=str(tmp_path),
        )

        token = await mgr.get_valid_token()
        assert token == "new_access_token"
        assert state["call_count"] == 1
        assert state["last_grant_type"] == "refresh_token"
        assert state["last_refresh_token"] == "initial_rt"
        assert state["last_client_id"] == "cid"

    async def test_reuses_token_before_expiry(self, mock_token_server, tmp_path):
        server, state = mock_token_server
        token_url = str(server.make_url("/token"))
        state["next_expires_in"] = 3600

        mgr = OAuth2TokenManager(
            service_name="test",
            token_url=token_url,
            client_id="cid",
            client_secret="csec",
            initial_refresh_token="rt",
            state_dir=str(tmp_path),
        )

        await mgr.get_valid_token()
        await mgr.get_valid_token()
        await mgr.get_valid_token()
        assert state["call_count"] == 1  # Only refreshed once

    async def test_handle_401_forces_refresh(self, mock_token_server, tmp_path):
        server, state = mock_token_server
        token_url = str(server.make_url("/token"))

        mgr = OAuth2TokenManager(
            service_name="test",
            token_url=token_url,
            client_id="cid",
            client_secret="csec",
            initial_refresh_token="rt",
            state_dir=str(tmp_path),
        )

        await mgr.get_valid_token()
        assert state["call_count"] == 1

        state["next_access_token"] = "refreshed_token"
        token = await mgr.handle_401()
        assert token == "refreshed_token"
        assert state["call_count"] == 2

    async def test_persists_state(self, mock_token_server, tmp_path):
        server, state = mock_token_server
        token_url = str(server.make_url("/token"))
        state["next_refresh_token"] = "persisted_rt"

        mgr = OAuth2TokenManager(
            service_name="test",
            token_url=token_url,
            client_id="cid",
            client_secret="csec",
            initial_refresh_token="initial_rt",
            state_dir=str(tmp_path),
        )

        await mgr.get_valid_token()

        # Check state file
        state_file = tmp_path / "test.json"
        assert state_file.exists()
        saved = json.loads(state_file.read_text())
        assert saved["access_token"] == "new_access_token"
        assert saved["refresh_token"] == "persisted_rt"

    async def test_loads_persisted_state(self, mock_token_server, tmp_path):
        server, state = mock_token_server
        token_url = str(server.make_url("/token"))

        # Pre-create state file with valid token
        state_file = tmp_path / "test.json"
        state_file.write_text(json.dumps({
            "access_token": "persisted_at",
            "refresh_token": "persisted_rt",
            "expires_at": time.time() + 3600,
        }))

        mgr = OAuth2TokenManager(
            service_name="test",
            token_url=token_url,
            client_id="cid",
            client_secret="csec",
            initial_refresh_token="initial_rt",
            state_dir=str(tmp_path),
        )

        token = await mgr.get_valid_token()
        assert token == "persisted_at"
        assert state["call_count"] == 0  # No refresh needed

    async def test_uses_persisted_refresh_token(self, mock_token_server, tmp_path):
        server, state = mock_token_server
        token_url = str(server.make_url("/token"))

        # Pre-create state file with expired access but valid refresh
        state_file = tmp_path / "test.json"
        state_file.write_text(json.dumps({
            "access_token": "expired_at",
            "refresh_token": "persisted_rt",
            "expires_at": 0,
        }))

        mgr = OAuth2TokenManager(
            service_name="test",
            token_url=token_url,
            client_id="cid",
            client_secret="csec",
            initial_refresh_token="initial_rt",
            state_dir=str(tmp_path),
        )

        await mgr.get_valid_token()
        assert state["last_refresh_token"] == "persisted_rt"  # Used persisted, not initial

    async def test_refresh_failure_raises(self, mock_token_server, tmp_path):
        server, state = mock_token_server
        token_url = str(server.make_url("/token"))
        state["fail"] = True

        mgr = OAuth2TokenManager(
            service_name="test",
            token_url=token_url,
            client_id="cid",
            client_secret="csec",
            initial_refresh_token="rt",
            state_dir=str(tmp_path),
        )

        with pytest.raises(RuntimeError, match="OAuth2 refresh failed"):
            await mgr.get_valid_token()


# =============================================================================
# Integration: proxy with OAuth2
# =============================================================================


@pytest.fixture
async def oauth2_proxy(mock_token_server, tmp_path):
    """Create proxy with OAuth2 service + mock upstream."""
    token_server, token_state = mock_token_server
    token_url = str(token_server.make_url("/token"))

    # Mock upstream API
    upstream_app = web.Application()
    upstream_state = {"requests": []}

    async def handle_upstream(request: web.Request):
        auth = request.headers.get("Authorization", "")
        upstream_state["requests"].append({
            "auth": auth,
            "path": request.path,
        })
        if auth == "Bearer expired_token":
            return web.json_response({"error": "unauthorized"}, status=401)
        return web.json_response({"ok": True})

    upstream_app.router.add_route("*", "/{path:.*}", handle_upstream)
    async with TestServer(upstream_app) as upstream_server:
        upstream_url = str(upstream_server.make_url(""))

        config = {
            "services": {
                "oauth_svc": {
                    "upstream": upstream_url,
                    "auth": "oauth2",
                    "oauth2": {
                        "token_url": token_url,
                        "client_id": "cid",
                        "client_secret": "csec",
                        "refresh_token": "initial_rt",
                    },
                    "credentials": [
                        {"token": "initial_at", "resources": ["*"]},
                    ],
                },
            },
        }

        routes = make_routes(config, state_dir=str(tmp_path))
        proxy_app = web.Application()
        for method, path, handler in routes:
            proxy_app.router.add_route(method, path, handler)

        async with TestServer(proxy_app) as proxy_server:
            yield proxy_server, token_state, upstream_state


class TestHttpProxyOAuth2Integration:
    async def test_auto_refreshes_on_first_request(self, oauth2_proxy):
        proxy, token_state, upstream_state = oauth2_proxy
        import aiohttp
        async with aiohttp.ClientSession() as session:
            url = str(proxy.make_url("/proxy/oauth_svc/api/data"))
            async with session.get(url) as resp:
                assert resp.status == 200

        # Token was refreshed (initial had no expires_at → expired)
        assert token_state["call_count"] == 1
        # Upstream received the new token
        assert upstream_state["requests"][-1]["auth"] == "Bearer new_access_token"

    async def test_retries_on_401(self, oauth2_proxy):
        proxy, token_state, upstream_state = oauth2_proxy
        import aiohttp

        # First request refreshes (gets new_access_token) and succeeds
        token_state["next_access_token"] = "expired_token"
        async with aiohttp.ClientSession() as session:
            url = str(proxy.make_url("/proxy/oauth_svc/api/data"))
            async with session.get(url) as resp:
                # First request: refresh → expired_token → upstream 401 →
                # retry refresh → expired_token again → still 401
                assert resp.status == 401
        assert token_state["call_count"] == 2  # Initial refresh + retry

        # Now make refresh return a good token
        token_state["next_access_token"] = "good_token"
        async with aiohttp.ClientSession() as session:
            url = str(proxy.make_url("/proxy/oauth_svc/api/data"))
            async with session.get(url) as resp:
                # handle_401 forces refresh → good_token → 200
                assert resp.status == 200
        assert upstream_state["requests"][-1]["auth"] == "Bearer good_token"


# =============================================================================
# Token request format (json vs form) and public clients
# =============================================================================


@pytest.fixture
async def recording_token_server():
    """Token endpoint that records content type and parsed body."""
    app = web.Application()
    state = {"calls": []}

    async def handle_token(request: web.Request):
        ctype = request.headers.get("Content-Type", "")
        if ctype.startswith("application/json"):
            body = await request.json()
        else:
            body = dict(await request.post())
        state["calls"].append({"content_type": ctype, "body": body})
        return web.json_response({
            "access_token": "at",
            "refresh_token": "rt2",
            "expires_in": 3600,
        })

    app.router.add_post("/token", handle_token)
    async with TestServer(app) as server:
        yield server, state


class TestTokenRequestFormat:
    async def test_json_body_without_client_secret(
        self, recording_token_server, tmp_path,
    ):
        server, state = recording_token_server
        mgr = OAuth2TokenManager(
            service_name="jsonfmt",
            token_url=str(server.make_url("/token")),
            client_id="public-client",
            client_secret="",
            initial_refresh_token="rt1",
            state_dir=str(tmp_path),
            token_request_format="json",
        )
        await mgr.get_valid_token()

        call = state["calls"][-1]
        assert call["content_type"].startswith("application/json")
        assert call["body"]["grant_type"] == "refresh_token"
        assert call["body"]["client_id"] == "public-client"
        assert call["body"]["refresh_token"] == "rt1"
        assert "client_secret" not in call["body"]

    async def test_form_body_omits_empty_client_secret(
        self, recording_token_server, tmp_path,
    ):
        server, state = recording_token_server
        mgr = OAuth2TokenManager(
            service_name="formfmt",
            token_url=str(server.make_url("/token")),
            client_id="cid",
            client_secret="",
            initial_refresh_token="rt1",
            state_dir=str(tmp_path),
        )
        await mgr.get_valid_token()

        call = state["calls"][-1]
        assert not call["content_type"].startswith("application/json")
        assert "client_secret" not in call["body"]

    async def test_form_body_keeps_client_secret(
        self, recording_token_server, tmp_path,
    ):
        server, state = recording_token_server
        mgr = OAuth2TokenManager(
            service_name="withsecret",
            token_url=str(server.make_url("/token")),
            client_id="cid",
            client_secret="csec",
            initial_refresh_token="rt1",
            state_dir=str(tmp_path),
        )
        await mgr.get_valid_token()
        assert state["calls"][-1]["body"]["client_secret"] == "csec"

    def test_invalid_format_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            OAuth2TokenManager(
                service_name="bad",
                token_url="https://x/token",
                client_id="cid",
                state_dir=str(tmp_path),
                token_request_format="xml",
            )

    def test_make_routes_tolerates_missing_client_secret(self):
        routes = make_routes({"services": {"svc": {
            "upstream": "https://api.example.com",
            "auth": "oauth2",
            "oauth2": {
                "token_url": "https://x/token",
                "client_id": "cid",
                "token_request_format": "json",
                "refresh_token": "rt",
            },
        }}}, state_dir="/tmp/fgap-test-tokens")
        assert routes
