"""Generic HTTP forward proxy plugin.

Proxies HTTP requests to upstream APIs with credential injection.
No CLI binary needed — sandbox uses curl directly.

MCP support (scope): the request/response header allowlists include
the headers required by the Model Context Protocol Streamable HTTP
transport (spec 2025-03-26), so this proxy can sit in front of MCP
servers that answer request-response in JSON — ``initialize``,
``tools/list``, and kick-and-poll style tools that return a small JSON
body.

Streaming (SSE) support: when the upstream answers with
``Content-Type: text/event-stream``, the response is relayed chunk by
chunk instead of buffered — this covers the SSE side of MCP Streamable
HTTP and streaming LLM APIs behind the proxy. Streamed relays drop the
upstream ``Content-Length``, default ``Cache-Control: no-cache``, and
set ``X-Accel-Buffering: no`` for buffering intermediaries. Streaming upstreams
(SSE MCP servers, LLM APIs) should set ``"streaming": true``: the
service is then forwarded through an HTTP/2-capable client (some edges
only pass SSE through unbuffered on h2; ALPN falls back to HTTP/1.1),
with a per-request timeout that has no total limit — only a 30s
connect limit and an idle-read guard (``stream_idle_timeout``, default
300s), so long LLM calls are not capped.

Per-service header controls:

- ``forward_request_headers``: extra client request headers to forward
  upstream, on top of the built-in allowlist (e.g.
  ``anthropic-version`` for the Anthropic API).
- ``extra_headers``: headers set before client forwarding — a client
  header with the same name wins.
- ``append_headers``: headers merged after client forwarding with HTTP
  list semantics — comma-appended if the client sent the header,
  set otherwise. Use this to pin protocol flags (e.g. a required
  ``anthropic-beta`` value) without clobbering the client's own list.

Auth modes:

- ``bearer``: ``Authorization: Bearer <token>``
- ``basic``: ``Authorization: Basic <base64>``
- ``header``: inject the token under a caller-chosen header name
  (``header_name`` required) — for APIs like ``x-api-key`` that put the
  credential outside ``Authorization``. This is what makes the plugin
  usable in front of MCP servers that authenticate that way.
- ``oauth2``: automatic refresh via OAuth2TokenManager. ``client_secret``
  is optional (public/PKCE clients don't have one), and
  ``token_request_format: "json"`` switches the refresh POST from
  form-encoded (RFC 6749 §6, the default) to a JSON body for token
  endpoints that require it. Token state persists under ``state_dir``
  (top-level plugin config, default ``/var/lib/fgap/tokens``).

Config example::

    "http_proxy": {
        "services": {
            "freee": {
                "upstream": "https://api.freee.co.jp",
                "auth": "bearer",
                "credentials": [
                    {"token": "access_token_xxx", "resources": ["*"]}
                ]
            },
            "some_mcp": {
                "upstream": "https://mcp.example.com",
                "auth": "header",
                "header_name": "x-api-key",
                "credentials": [
                    {"token": "sk_xxx", "resources": ["*"]}
                ]
            },
            "anthropic": {
                // Streaming LLM API behind the proxy: a Claude Code
                // container runs with ANTHROPIC_BASE_URL pointed at
                // {proxy}/proxy/anthropic and a dummy client token,
                // so the real credential never enters the sandbox.
                "upstream": "https://api.anthropic.com",
                "auth": "oauth2",
                "streaming": true,
                "forward_request_headers": [
                    "anthropic-version", "anthropic-beta"
                ],
                "append_headers": {
                    "anthropic-beta": "oauth-2025-04-20"
                },
                "oauth2": {
                    "token_url":
                        "https://console.anthropic.com/v1/oauth/token",
                    "client_id": "YOUR_OAUTH_CLIENT_ID",
                    "token_request_format": "json",
                    "refresh_token": "seeded_refresh_token"
                }
            }
        }
    }

Sandbox usage::

    curl $FGAP_PROXY_URL/proxy/freee/api/1/deals?company_id=XXX
    curl -X POST $FGAP_PROXY_URL/proxy/some_mcp/mcp \\
      -H 'Content-Type: application/json' \\
      -H 'Accept: application/json, text/event-stream' \\
      -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
"""

import logging

from fgap.plugins.base import Plugin

from .proxy import make_routes

logger = logging.getLogger(__name__)


class HttpProxyPlugin(Plugin):
    """Generic HTTP forward proxy with credential injection."""

    @property
    def name(self) -> str:
        return "http_proxy"

    @property
    def tools(self) -> list[str]:
        # No CLI tools — this plugin only provides HTTP routes
        return []

    def select_credential(self, resource: str, config: dict) -> dict | None:
        # Not used for HTTP proxy (credentials are handled in route handlers)
        return None

    def get_routes(self, config: dict) -> list[tuple[str, str, callable]]:
        return make_routes(config, state_dir=config.get("state_dir", ""))

    async def health_check(self, config: dict) -> list[dict]:
        """Report configured services (no active health check)."""
        results = []
        for service_name, service_config in config.get("services", {}).items():
            results.append({
                "service": service_name,
                "upstream": service_config.get("upstream", ""),
                "auth": service_config.get("auth", "bearer"),
                "has_credentials": len(
                    service_config.get("credentials", [])
                ) > 0,
            })
        return results
