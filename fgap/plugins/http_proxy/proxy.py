"""HTTP forward proxy with credential injection.

Generalizes the git smart HTTP proxy pattern: receives HTTP requests,
injects Authorization header, forwards to upstream.
"""

import logging

import aiohttp
from aiohttp import web

from fgap.core.http import get_session
from fgap.plugins.base import match_resource

logger = logging.getLogger(__name__)

# Headers to forward from client request to upstream
_FORWARDED_REQUEST_HEADERS = (
    "Content-Type", "Accept", "Accept-Encoding", "Accept-Language",
)

# Headers to forward from upstream response to client
_FORWARDED_RESPONSE_HEADERS = (
    "Content-Type", "Content-Length", "Cache-Control",
    "X-Request-Id",
)


def _select_token(resource: str, service_config: dict) -> str | None:
    """Select token for a service resource. First-match-wins."""
    for cred in service_config.get("credentials", []):
        for pattern in cred.get("resources", []):
            if match_resource(pattern, resource):
                return cred.get("token")
    return None


def make_routes(config: dict) -> list[tuple[str, str, callable]]:
    """Create HTTP proxy routes for all configured services.

    For each service, creates a catch-all route:
        {method} /proxy/{service}/{path:.*}
    that forwards to:
        {method} {upstream}/{path}
    with Authorization header injected.
    """
    services = config.get("services", {})
    if not services:
        return []

    async def handle_proxy(request: web.Request) -> web.Response:
        service = request.match_info["service"]
        path = request.match_info.get("path", "")

        service_config = services.get(service)
        if not service_config:
            raise web.HTTPNotFound(
                text=f"Unknown proxy service: {service}"
            )

        # Resource defaults to "default" — services can use resource
        # patterns for multi-tenant credential selection
        resource = request.query.get("_resource", "default")

        token = _select_token(resource, service_config)
        if not token:
            raise web.HTTPForbidden(
                text=f"No credential for proxy service: {service}"
            )

        upstream = service_config["upstream"].rstrip("/")
        auth_type = service_config.get("auth", "bearer")
        extra_headers = service_config.get("extra_headers", {})

        return await _proxy_request(
            request, upstream, path, token, auth_type, extra_headers,
        )

    # Single route pattern handles all services and HTTP methods
    pattern = "/proxy/{service}/{path:.*}"
    return [
        ("GET", pattern, handle_proxy),
        ("POST", pattern, handle_proxy),
        ("PUT", pattern, handle_proxy),
        ("PATCH", pattern, handle_proxy),
        ("DELETE", pattern, handle_proxy),
    ]


async def _proxy_request(
    request: web.Request,
    upstream: str,
    path: str,
    token: str,
    auth_type: str,
    extra_headers: dict,
) -> web.Response:
    upstream_url = f"{upstream}/{path}"
    if request.query_string:
        # Strip internal _resource param before forwarding
        import urllib.parse
        params = urllib.parse.parse_qs(request.query_string, keep_blank_values=True)
        params.pop("_resource", None)
        filtered_qs = urllib.parse.urlencode(params, doseq=True)
        if filtered_qs:
            upstream_url += f"?{filtered_qs}"

    # Build auth header
    if auth_type == "bearer":
        auth_header = f"Bearer {token}"
    elif auth_type == "basic":
        import base64
        auth_header = f"Basic {base64.b64encode(token.encode()).decode()}"
    else:
        auth_header = f"Bearer {token}"

    headers = {
        "Authorization": auth_header,
        "User-Agent": "fgap",
    }
    headers.update(extra_headers)

    for h in _FORWARDED_REQUEST_HEADERS:
        if h in request.headers:
            headers[h] = request.headers[h]

    body = await request.read() if request.can_read_body else None

    session = get_session()
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()
    try:
        async with session.request(
            request.method, upstream_url,
            headers=headers, data=body,
        ) as resp:
            response_body = await resp.read()
            response_headers = {}
            for h in _FORWARDED_RESPONSE_HEADERS:
                if h in resp.headers:
                    response_headers[h] = resp.headers[h]
            return web.Response(
                body=response_body,
                status=resp.status,
                headers=response_headers,
            )
    finally:
        if own_session:
            await session.close()
