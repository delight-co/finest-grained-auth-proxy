"""Interactive OAuth2 authorization-code login for http_proxy services.

Runs on the *proxy host* (where a browser lives) and seeds the token
state that ``OAuth2TokenManager`` refreshes from. This replaces the
awkward alternative of logging in from inside a headless sandbox and
copying credential files around.

Flow (authorization code + PKCE, RFC 7636):

1. Build the authorization URL from the service's ``oauth2.login``
   config and open it in a browser (printed as well, for remote hosts).
2. The provider shows the authorization code after consent (manual-code
   flow) — paste it back. ``code#state`` pastes are accepted and the
   state is verified.
3. Exchange the code at ``token_url`` (honoring
   ``token_request_format``) and write the token state file that the
   proxy refreshes from, with owner-only permissions.

Config example::

    "anthropic": {
        "upstream": "https://api.anthropic.com",
        "auth": "oauth2",
        "oauth2": {
            "token_url": "https://console.anthropic.com/v1/oauth/token",
            "client_id": "YOUR_OAUTH_CLIENT_ID",
            "token_request_format": "json",
            "login": {
                "authorize_url": "https://claude.ai/oauth/authorize",
                "redirect_uri":
                    "https://console.anthropic.com/oauth/code/callback",
                "scope": "org:create_api_key user:profile user:inference",
                "extra_authorize_params": {"code": "true"}
            }
        }
    }

Usage::

    fgap-oauth-login --config /path/to/config.json5 --service anthropic
"""

import argparse
import asyncio
import base64
import hashlib
import os
import secrets
import sys
import time
import urllib.parse
import webbrowser

import aiohttp

from fgap.core.config import load_config

from .oauth2 import save_token_state

# User-Agent for token-endpoint requests. Some OAuth token endpoints
# sit behind CDNs that fingerprint the client (Cloudflare returned
# error 1010 to a bare urllib/aiohttp default UA against Anthropic's
# platform.claude.com during a 2026-07-22 exchange). Advertising as
# the coding-agent CLI keeps that fingerprint check happy without
# pretending to be a browser.
_LOGIN_USER_AGENT = "claude-cli/2.1.215 (external, cli)"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def make_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) per RFC 7636 S256."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def build_authorize_url(
    login_cfg: dict, client_id: str, state: str, challenge: str,
) -> str:
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": login_cfg["redirect_uri"],
        "scope": login_cfg.get("scope", ""),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    params.update(login_cfg.get("extra_authorize_params", {}))
    return f"{login_cfg['authorize_url']}?{urllib.parse.urlencode(params)}"


def parse_pasted_code(pasted: str, expected_state: str) -> str:
    """Extract the authorization code from a pasted value.

    Providers that show the code on a callback page often present it as
    ``code#state``; verify the state when it's there.
    """
    pasted = pasted.strip()
    if not pasted:
        raise ValueError("empty authorization code")
    if "#" in pasted:
        code, _, returned_state = pasted.partition("#")
        if returned_state and returned_state != expected_state:
            raise ValueError(
                "state mismatch — the pasted code belongs to a different "
                "login attempt; restart the login"
            )
        return code
    return pasted


async def exchange_code(
    *,
    token_url: str,
    client_id: str,
    code: str,
    verifier: str,
    redirect_uri: str,
    state: str,
    token_request_format: str = "form",
) -> dict:
    """Exchange the authorization code for a token pair."""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
        "state": state,
    }
    body_kwargs = (
        {"json": data} if token_request_format == "json" else {"data": data}
    )
    headers = {"User-Agent": _LOGIN_USER_AGENT, "Accept": "application/json"}
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.post(
            token_url, **body_kwargs,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(
                    f"token exchange failed: HTTP {resp.status}: {text}"
                )
            return await resp.json()


def run_login(
    config_path: str, service: str, *, open_browser: bool = True,
) -> str:
    """Run the interactive login and return the state file path."""
    config = load_config(config_path)
    plugin_cfg = config.get("plugins", {}).get("http_proxy", {})
    svc = plugin_cfg.get("services", {}).get(service)
    if not svc:
        raise SystemExit(f"unknown http_proxy service: {service}")
    oauth2_cfg = svc.get("oauth2")
    if not oauth2_cfg:
        raise SystemExit(f"service '{service}' has no oauth2 config")
    login_cfg = oauth2_cfg.get("login")
    if not login_cfg:
        raise SystemExit(
            f"service '{service}' has no oauth2.login config "
            f"(authorize_url / redirect_uri / scope)"
        )

    verifier, challenge = make_pkce()
    # 32 bytes of entropy for the state parameter. RFC 6749 leaves the
    # length undefined, but at least one authorize endpoint (Anthropic's,
    # observed 2026-07-22) rejects shorter values with 'Invalid request
    # format', so match the standard 32-byte width used by CC and other
    # first-party clients rather than the RFC's minimum.
    state = _b64url(secrets.token_bytes(32))
    url = build_authorize_url(
        login_cfg, oauth2_cfg["client_id"], state, challenge,
    )

    print("Open this URL to authorize:")
    print(f"\n  {url}\n")
    if open_browser:
        webbrowser.open(url)

    pasted = input("Paste the authorization code shown after consent: ")
    code = parse_pasted_code(pasted, state)

    result = asyncio.run(exchange_code(
        token_url=oauth2_cfg["token_url"],
        client_id=oauth2_cfg["client_id"],
        code=code,
        verifier=verifier,
        redirect_uri=login_cfg["redirect_uri"],
        state=state,
        token_request_format=oauth2_cfg.get("token_request_format", "form"),
    ))

    expires_at = time.time() + float(result.get("expires_in", 3600))
    path = save_token_state(
        plugin_cfg.get("state_dir", ""),
        service,
        access_token=result["access_token"],
        refresh_token=result.get("refresh_token", ""),
        expires_at=expires_at,
    )
    print(f"Token state written: {path}")
    print(f"Access token expires in ~{int(result.get('expires_in', 3600))}s; "
          f"the proxy refreshes it automatically from here.")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Interactive OAuth2 login that seeds fgap token state",
    )
    parser.add_argument("--config", required=True, help="Path to config file (JSON5)")
    parser.add_argument("--service", required=True, help="http_proxy service name")
    parser.add_argument(
        "--no-browser", action="store_true",
        help="Only print the authorization URL, don't open a browser",
    )
    args = parser.parse_args()
    try:
        run_login(args.config, args.service, open_browser=not args.no_browser)
    except (ValueError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
