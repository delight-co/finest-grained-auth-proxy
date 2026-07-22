"""Tests for the interactive OAuth2 login command (fgap-oauth-login)."""

import base64
import hashlib
import json
import os
import stat

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from fgap.plugins.http_proxy import login
from fgap.plugins.http_proxy.login import (
    _b64url,
    build_authorize_url,
    exchange_code,
    make_pkce,
    parse_pasted_code,
    run_login,
)


class TestPkce:
    def test_challenge_is_s256_of_verifier(self):
        verifier, challenge = make_pkce()
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).decode().rstrip("=")
        assert challenge == expected
        assert "=" not in verifier

    def test_rfc7636_appendix_b_vector(self):
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
        assert challenge == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


class TestAuthorizeUrl:
    def test_params_present(self):
        url = build_authorize_url(
            {
                "authorize_url": "https://auth.example.com/authorize",
                "redirect_uri": "https://auth.example.com/callback",
                "scope": "a b",
                "extra_authorize_params": {"code": "true"},
            },
            "cid", "st4te", "ch4llenge",
        )
        assert url.startswith("https://auth.example.com/authorize?")
        assert "client_id=cid" in url
        assert "response_type=code" in url
        assert "scope=a+b" in url
        assert "state=st4te" in url
        assert "code_challenge=ch4llenge" in url
        assert "code_challenge_method=S256" in url
        assert "code=true" in url


class TestParsePastedCode:
    def test_plain_code(self):
        assert parse_pasted_code("  abc123  ", "st") == "abc123"

    def test_code_with_matching_state(self):
        assert parse_pasted_code("abc123#st", "st") == "abc123"

    def test_state_mismatch_rejected(self):
        with pytest.raises(ValueError, match="state mismatch"):
            parse_pasted_code("abc123#other", "st")

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            parse_pasted_code("   ", "st")


@pytest.fixture
async def code_exchange_server():
    """Token endpoint that records the exchange request."""
    app = web.Application()
    state = {"calls": []}

    async def handle_token(request: web.Request):
        ctype = request.headers.get("Content-Type", "")
        if ctype.startswith("application/json"):
            body = await request.json()
        else:
            body = dict(await request.post())
        state["calls"].append({
            "content_type": ctype,
            "body": body,
            "user_agent": request.headers.get("User-Agent", ""),
        })
        return web.json_response({
            "access_token": "at",
            "refresh_token": "rt",
            "expires_in": 300,
        })

    app.router.add_post("/token", handle_token)
    async with TestServer(app) as server:
        yield server, state


class TestExchangeCode:
    async def test_json_exchange(self, code_exchange_server):
        server, state = code_exchange_server
        result = await exchange_code(
            token_url=str(server.make_url("/token")),
            client_id="cid",
            code="c0de",
            verifier="v3rifier",
            redirect_uri="https://cb.example.com",
            state="st",
            token_request_format="json",
        )
        assert result["access_token"] == "at"
        call = state["calls"][-1]
        assert call["content_type"].startswith("application/json")
        assert call["body"]["grant_type"] == "authorization_code"
        assert call["body"]["code"] == "c0de"
        assert call["body"]["code_verifier"] == "v3rifier"
        assert call["body"]["redirect_uri"] == "https://cb.example.com"

    async def test_form_exchange(self, code_exchange_server):
        server, state = code_exchange_server
        await exchange_code(
            token_url=str(server.make_url("/token")),
            client_id="cid",
            code="c0de",
            verifier="v",
            redirect_uri="https://cb.example.com",
            state="st",
        )
        call = state["calls"][-1]
        assert not call["content_type"].startswith("application/json")
        assert call["body"]["code"] == "c0de"


class TestRunLogin:
    def test_full_flow_writes_state_file(self, tmp_path, monkeypatch):
        state_dir = tmp_path / "tokens"
        config = {
            "plugins": {"http_proxy": {
                "state_dir": str(state_dir),
                "services": {"svc": {
                    "upstream": "https://api.example.com",
                    "auth": "oauth2",
                    "oauth2": {
                        "token_url": "https://auth.example.com/token",
                        "client_id": "cid",
                        "token_request_format": "json",
                        "login": {
                            "authorize_url":
                                "https://auth.example.com/authorize",
                            "redirect_uri":
                                "https://auth.example.com/callback",
                            "scope": "s1 s2",
                        },
                    },
                }},
            }},
        }
        config_path = tmp_path / "config.json5"
        config_path.write_text(json.dumps(config))
        os.chmod(config_path, 0o600)

        async def fake_exchange(**kwargs):
            assert kwargs["code"] == "pasted-code"
            return {
                "access_token": "at", "refresh_token": "rt",
                "expires_in": 300,
            }

        monkeypatch.setattr(login, "exchange_code", fake_exchange)
        monkeypatch.setattr(
            "builtins.input", lambda prompt: "pasted-code",
        )

        path = run_login(str(config_path), "svc", open_browser=False)

        written = json.load(open(path))
        assert written["access_token"] == "at"
        assert written["refresh_token"] == "rt"
        assert written["expires_at"] > 0

        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(state_dir).st_mode) == 0o700

    def test_unknown_service_exits(self, tmp_path):
        config_path = tmp_path / "config.json5"
        config_path.write_text(json.dumps({"plugins": {"http_proxy": {}}}))
        os.chmod(config_path, 0o600)
        with pytest.raises(SystemExit):
            run_login(str(config_path), "nope", open_browser=False)


class TestExchangeCodeUserAgent:
    async def test_user_agent_sent(self, code_exchange_server):
        """Some token endpoints (Anthropic's) sit behind CDNs that reject
        default library UAs; verify the explicit UA reaches the endpoint."""
        server, state = code_exchange_server
        await exchange_code(
            token_url=str(server.make_url("/token")),
            client_id="cid",
            code="c0de",
            verifier="v3rifier",
            redirect_uri="https://cb.example.com",
            state="st",
        )
        ua = state["calls"][-1].get("user_agent", "")
        assert "claude-cli" in ua
