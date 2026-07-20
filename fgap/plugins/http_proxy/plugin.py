"""Generic HTTP forward proxy plugin.

Proxies HTTP requests to upstream APIs with credential injection.
No CLI binary needed — sandbox uses curl directly.

Auth modes:

- ``bearer``: ``Authorization: Bearer <token>``
- ``basic``: ``Authorization: Basic <base64>``
- ``header``: inject the token under a caller-chosen header name
  (``header_name`` required) — for APIs like ``x-api-key`` that put the
  credential outside ``Authorization``. This is what makes the plugin
  usable in front of MCP servers that authenticate that way.
- ``oauth2``: automatic refresh via OAuth2TokenManager.

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
        return make_routes(config)

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
