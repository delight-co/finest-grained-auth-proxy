"""HTTP forward proxy with credential injection.

Generalizes the git smart HTTP proxy pattern: receives HTTP requests,
injects Authorization header, forwards to upstream.

Supports two auth modes:
- "bearer": static token from credentials array
- "oauth2": automatic token refresh via OAuth2TokenManager
"""

import logging
import urllib.parse

import aiohttp
import httpx
from aiohttp import web

from fgap.core.http import get_h2_client, get_session
from fgap.plugins.base import match_resource

logger = logging.getLogger(__name__)

# Headers to forward from client request to upstream. MCP-Protocol-Version,
# Mcp-Session-Id, and Origin are required by the MCP Streamable HTTP
# transport (spec 2025-03-26); they are harmless for non-MCP upstreams.
_FORWARDED_REQUEST_HEADERS = (
    "Content-Type", "Accept", "Accept-Encoding", "Accept-Language",
    "MCP-Protocol-Version", "Mcp-Session-Id", "Origin",
)

# Headers to forward from upstream response to client. Mcp-Session-Id
# and MCP-Protocol-Version are how a stateful MCP server hands out a
# session on initialize and echoes the negotiated version.
_FORWARDED_RESPONSE_HEADERS = (
    "Content-Type", "Content-Length", "Cache-Control",
    "X-Request-Id",
    "Mcp-Session-Id", "MCP-Protocol-Version",
)

# Response headers that must not be copied onto a streamed relay:
# aiohttp recomputes framing for chunked responses, and a stale
# Content-Length from the upstream would corrupt the stream.
_STREAMING_SKIP_RESPONSE_HEADERS = frozenset({"Content-Length"})

_SUPPORTED_AUTH = frozenset({"bearer", "basic", "header", "oauth2"})


def _oauth_refresh_error(service: str, err: Exception) -> web.Response:
    """Actionable error for a token refresh the proxy can no longer do.

    The message is the runbook: agent-side clients surface API error
    text verbatim, so it tells the operator exactly what to run on the
    proxy host. The body follows the common ``{"type": "error", ...}``
    shape so anthropic-style clients render it.
    """
    message = (
        f"fgap: OAuth2 token refresh failed for service '{service}' "
        f"({err}). The proxy-side token state is stale or revoked — "
        f"re-seed it or run 'fgap-oauth-login --service {service}' "
        f"on the proxy host."
    )
    logger.error(message)
    return web.json_response(
        {"type": "error",
         "error": {"type": "api_error", "message": message}},
        status=502,
    )


def _select_token(resource: str, service_config: dict) -> str | None:
    """Select token for a service resource. First-match-wins."""
    for cred in service_config.get("credentials", []):
        for pattern in cred.get("resources", []):
            if match_resource(pattern, resource):
                return cred.get("token")
    return None


def make_routes(
    config: dict, *, state_dir: str = "",
) -> list[tuple[str, str, callable]]:
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

    # Fail fast on service misconfiguration: an unknown auth mode or a
    # header-auth service missing its header_name would only surface at
    # request time otherwise, and by then the credential is unusable.
    for name, svc in services.items():
        auth = svc.get("auth", "bearer")
        if auth not in _SUPPORTED_AUTH:
            raise ValueError(
                f"http_proxy service '{name}': unknown auth mode "
                f"'{auth}' (supported: {', '.join(sorted(_SUPPORTED_AUTH))})"
            )
        if auth == "header" and not svc.get("header_name"):
            raise ValueError(
                f"http_proxy service '{name}': auth 'header' requires "
                f"'header_name' (name of the header to inject the token as)"
            )
        if not isinstance(svc.get("forward_request_headers", []), list):
            raise ValueError(
                f"http_proxy service '{name}': 'forward_request_headers' "
                f"must be a list of header names"
            )
        if not isinstance(svc.get("append_headers", {}), dict):
            raise ValueError(
                f"http_proxy service '{name}': 'append_headers' must be a "
                f"mapping of header name to value"
            )

    # Initialize OAuth2 token managers for services with auth=oauth2
    token_managers = {}
    for name, svc in services.items():
        if svc.get("auth") == "oauth2" and "oauth2" in svc:
            from .oauth2 import OAuth2TokenManager

            oauth2_cfg = svc["oauth2"]
            # Use initial access_token from credentials if available
            initial_access = ""
            if svc.get("credentials"):
                initial_access = svc["credentials"][0].get("token", "")

            kwargs = {
                "service_name": name,
                "initial_access_token": initial_access,
            }

            # Delegated refresh: external API manages tokens centrally
            if "refresh_url" in oauth2_cfg:
                kwargs["refresh_url"] = oauth2_cfg["refresh_url"]
                kwargs["employee_id"] = oauth2_cfg["employee_id"]
                kwargs["provider"] = oauth2_cfg["provider"]
                # Auth token for the refresh API (e.g. JWT)
                if "refresh_api_token" in oauth2_cfg:
                    kwargs["refresh_api_token"] = oauth2_cfg[
                        "refresh_api_token"
                    ]
                elif config.get("internal_api_token"):
                    kwargs["refresh_api_token"] = config[
                        "internal_api_token"
                    ]
            else:
                # Direct refresh: POST to token endpoint. client_secret is
                # optional — public OAuth2 clients (PKCE) don't have one.
                kwargs["token_url"] = oauth2_cfg["token_url"]
                kwargs["client_id"] = oauth2_cfg["client_id"]
                kwargs["client_secret"] = oauth2_cfg.get("client_secret", "")
                kwargs["initial_refresh_token"] = oauth2_cfg.get(
                    "refresh_token", "",
                )
                kwargs["token_request_format"] = oauth2_cfg.get(
                    "token_request_format", "form",
                )

            if state_dir:
                kwargs["state_dir"] = state_dir
            token_managers[name] = OAuth2TokenManager(**kwargs)

    async def handle_proxy(request: web.Request) -> web.Response:
        service = request.match_info["service"]
        path = request.match_info.get("path", "")

        service_config = services.get(service)
        if not service_config:
            raise web.HTTPNotFound(
                text=f"Unknown proxy service: {service}"
            )

        upstream = service_config["upstream"].rstrip("/")
        auth_type = service_config.get("auth", "bearer")
        extra_headers = service_config.get("extra_headers", {})
        header_name = service_config.get("header_name")
        forward_extra = tuple(service_config.get("forward_request_headers", ()))
        append_headers = service_config.get("append_headers", {})
        streaming = bool(service_config.get("streaming"))
        stream_idle = float(service_config.get("stream_idle_timeout", 300))

        # Get token based on auth type
        if auth_type == "oauth2" and service in token_managers:
            try:
                token = await token_managers[service].get_valid_token()
            except (RuntimeError, aiohttp.ClientError, OSError) as e:
                return _oauth_refresh_error(service, e)
            effective_auth = "bearer"  # OAuth2 always uses Bearer
        else:
            resource = request.query.get("_resource", "default")
            token = _select_token(resource, service_config)
            effective_auth = auth_type

        if not token:
            raise web.HTTPForbidden(
                text=f"No credential for proxy service: {service}"
            )

        resp = await _proxy_request(
            request, upstream, path, token, effective_auth, extra_headers,
            header_name=header_name,
            forward_request_headers=forward_extra,
            append_headers=append_headers,
            streaming=streaming,
            stream_idle=stream_idle,
        )

        # Auto-retry on 401 for OAuth2 services. A streamed relay never
        # reaches here with 401 (error responses are JSON, so they take
        # the buffered path).
        if resp.status == 401 and service in token_managers:
            logger.info("Got 401 from %s, refreshing token", service)
            try:
                token = await token_managers[service].handle_401()
            except (RuntimeError, aiohttp.ClientError, OSError) as e:
                return _oauth_refresh_error(service, e)
            resp = await _proxy_request(
                request, upstream, path, token, "bearer", extra_headers,
                forward_request_headers=forward_extra,
                append_headers=append_headers,
                streaming=streaming,
                stream_idle=stream_idle,
            )

        return resp

    async def handle_service_head(request: web.Request) -> web.Response:
        """Answer HEAD on the service base URL.

        Some clients preflight their configured base URL with a bare
        HEAD (Claude Code does at startup); acknowledge configured
        services instead of 404ing.
        """
        if request.match_info["service"] not in services:
            raise web.HTTPNotFound()
        return web.Response()

    # Single route pattern handles all services and HTTP methods
    pattern = "/proxy/{service}/{path:.*}"
    return [
        ("HEAD", "/proxy/{service}", handle_service_head),
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
    *,
    header_name: str | None = None,
    forward_request_headers: tuple[str, ...] = (),
    append_headers: dict | None = None,
    streaming: bool = False,
    stream_idle: float = 300,
) -> web.StreamResponse:
    upstream_url = f"{upstream}/{path}"
    if request.query_string:
        # Strip internal _resource param before forwarding
        params = urllib.parse.parse_qs(
            request.query_string, keep_blank_values=True,
        )
        params.pop("_resource", None)
        filtered_qs = urllib.parse.urlencode(params, doseq=True)
        if filtered_qs:
            upstream_url += f"?{filtered_qs}"

    # Build the credential header. "header" mode injects the token under
    # a caller-chosen header name (e.g. "x-api-key"), leaving Authorization
    # untouched; bearer/basic keep the existing Authorization behavior.
    headers: dict[str, str] = {"User-Agent": "fgap"}
    if auth_type == "header":
        # Startup validation guarantees header_name is present here.
        headers[header_name] = token  # type: ignore[index]
    elif auth_type == "basic":
        import base64
        headers["Authorization"] = (
            f"Basic {base64.b64encode(token.encode()).decode()}"
        )
    else:
        headers["Authorization"] = f"Bearer {token}"

    headers.update(extra_headers)

    for h in (*_FORWARDED_REQUEST_HEADERS, *forward_request_headers):
        if h in request.headers:
            headers[h] = request.headers[h]

    # Merge-inject after client forwarding: comma-append to the client's
    # value (HTTP list semantics) instead of replacing it, so an
    # operator-pinned value (e.g. a required protocol flag) survives
    # alongside whatever the client sent.
    for name, value in (append_headers or {}).items():
        existing = headers.get(name)
        if not existing:
            headers[name] = value
        elif value not in (v.strip() for v in existing.split(",")):
            headers[name] = f"{existing},{value}"

    body = await request.read() if request.can_read_body else None

    if streaming:
        return await _proxy_request_h2(
            request, upstream_url, headers, body, stream_idle,
        )

    session = get_session()
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()
    try:
        async with session.request(
            request.method, upstream_url,
            headers=headers, data=body,
        ) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if content_type.startswith("text/event-stream"):
                # Relay chunk by chunk; the full stream is consumed
                # before we leave this context.
                return await _relay_stream(request, resp)
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


async def _proxy_request_h2(
    request: web.Request,
    upstream_url: str,
    headers: dict,
    body: bytes | None,
    stream_idle: float,
) -> web.StreamResponse:
    """Forward via the shared HTTP/2-capable client (httpx).

    Streaming services take this path for two reasons: some upstream
    edges only pass SSE through unbuffered on HTTP/2 (httpx negotiates
    h2 via ALPN and falls back to HTTP/1.1 otherwise), and long-lived
    requests must not be capped by a total timeout — only connect and
    idle read time are guarded here.
    """
    timeout = httpx.Timeout(connect=30, read=stream_idle, write=30, pool=30)
    client = get_h2_client()
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(http2=True, timeout=None)
    try:
        async with client.stream(
            request.method, upstream_url,
            headers=headers, content=body, timeout=timeout,
        ) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if content_type.startswith("text/event-stream"):
                return await _relay_stream_httpx(request, resp)
            response_body = await resp.aread()
            response_headers = {}
            for h in _FORWARDED_RESPONSE_HEADERS:
                value = resp.headers.get(h)
                # httpx decodes content-encoding transparently, so the
                # upstream Content-Length no longer matches the body.
                if value is not None and h != "Content-Length":
                    response_headers[h] = value
            return web.Response(
                body=response_body,
                status=resp.status_code,
                headers=response_headers,
            )
    finally:
        if own_client:
            await client.aclose()


async def _relay_stream_httpx(
    request: web.Request, resp: httpx.Response,
) -> web.StreamResponse:
    """Relay a text/event-stream httpx response without buffering."""
    headers = {}
    for h in _FORWARDED_RESPONSE_HEADERS:
        value = resp.headers.get(h)
        if value is not None and h not in _STREAMING_SKIP_RESPONSE_HEADERS:
            headers[h] = value
    headers.setdefault("Cache-Control", "no-cache")
    headers["X-Accel-Buffering"] = "no"

    stream = web.StreamResponse(status=resp.status_code, headers=headers)
    await stream.prepare(request)
    try:
        async for chunk in resp.aiter_bytes():
            await stream.write(chunk)
        await stream.write_eof()
    except ConnectionResetError:
        # The downstream client hung up mid-stream — a consumer
        # aborting an SSE response is normal, not an error. Returning
        # here closes the upstream response, which stops the stream at
        # its source.
        logger.info("Streaming client disconnected, upstream closed")
    return stream


async def _relay_stream(
    request: web.Request, resp: aiohttp.ClientResponse,
) -> web.StreamResponse:
    """Relay a text/event-stream upstream response without buffering."""
    headers = {}
    for h in _FORWARDED_RESPONSE_HEADERS:
        if h in resp.headers and h not in _STREAMING_SKIP_RESPONSE_HEADERS:
            headers[h] = resp.headers[h]
    headers.setdefault("Cache-Control", "no-cache")
    # Tell buffering intermediaries (nginx etc.) to pass events through.
    headers["X-Accel-Buffering"] = "no"

    stream = web.StreamResponse(status=resp.status, headers=headers)
    await stream.prepare(request)
    try:
        async for chunk in resp.content.iter_any():
            await stream.write(chunk)
        await stream.write_eof()
    except ConnectionResetError:
        # The downstream client hung up mid-stream — a consumer
        # aborting an SSE response is normal, not an error. Returning
        # here closes the upstream response, which stops the stream at
        # its source.
        logger.info("Streaming client disconnected, upstream closed")
    return stream
