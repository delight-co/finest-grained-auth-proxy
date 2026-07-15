"""S3-compatible object storage proxy plugin.

Proxies S3 API requests to an upstream S3-compatible endpoint with
SigV4 re-signing. No CLI wrapper needed — the sandbox uses a stock S3
client (aws cli, rclone, boto3) with dummy credentials.

Config example::

    "s3": {
        "services": {
            "media": {
                "endpoint": "https://ACCOUNT_ID.r2.cloudflarestorage.com",
                "region": "auto",
                "access_key_id": "...",
                "secret_access_key": "...",
                "buckets": ["my-bucket"],
                "deny": ["delete"],
                "immutable_puts": true
            }
        }
    }

Sandbox usage::

    aws s3 cp video.mp4 s3://my-bucket/path/video.mp4 --profile media
"""

import logging

from fgap.core.masking import mask_value
from fgap.plugins.base import Plugin

from .proxy import make_routes

logger = logging.getLogger(__name__)


class S3Plugin(Plugin):
    """S3-compatible storage proxy with SigV4 re-signing."""

    @property
    def name(self) -> str:
        return "s3"

    @property
    def tools(self) -> list[str]:
        # No CLI tools — this plugin only provides HTTP routes.
        return []

    def select_credential(self, resource: str, config: dict) -> dict | None:
        # Not used: credentials are handled in the route handlers.
        return None

    def get_routes(self, config: dict) -> list[tuple[str, str, callable]]:
        return make_routes(config)

    async def health_check(self, config: dict) -> list[dict]:
        """Report configured services (no active health check)."""
        results = []
        for service_name, service_config in config.get("services", {}).items():
            results.append({
                "service": service_name,
                "endpoint": service_config.get("endpoint", ""),
                "access_key_id": mask_value(
                    service_config.get("access_key_id", ""),
                ),
                "buckets": service_config.get("buckets"),
                "deny": service_config.get("deny", []),
                "immutable_puts": bool(service_config.get("immutable_puts")),
            })
        return results
