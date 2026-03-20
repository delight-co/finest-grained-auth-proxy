"""Generic HTTP forward proxy plugin.

Proxies HTTP requests to upstream APIs with credential injection.
No CLI binary needed — sandbox uses curl directly.

Config example::

    "http_proxy": {
        "services": {
            "freee": {
                "upstream": "https://api.freee.co.jp",
                "auth": "bearer",
                "credentials": [
                    {"token": "access_token_xxx", "resources": ["*"]}
                ]
            }
        }
    }

Sandbox usage::

    curl $FGAP_PROXY_URL/proxy/freee/api/1/deals?company_id=XXX
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
