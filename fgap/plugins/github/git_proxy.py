import asyncio
import base64
import logging

import aiohttp
from aiohttp import web

from fgap.core.http import get_session

logger = logging.getLogger(__name__)

# User-Agent must pass through: GitHub's LFS batch endpoint rejects
# requests that present a plain git UA (routing-level 403), so the
# git-lfs client has to be allowed to identify itself. The hardcoded
# git UA below remains the fallback for clients that send none.
_FORWARDED_HEADERS = ("Content-Type", "Accept", "User-Agent")
_RESPONSE_HEADERS = ("Content-Type", "Cache-Control")


def make_routes(select_credential_fn, resolve_env_fn, config):
    """Create git smart HTTP proxy routes.

    Returns list of (method, path, handler) tuples.
    """
    github_base = config.get("_github_base_url", "https://github.com")

    # Optional cap on concurrent POST transfers (pack up/downloads). Both
    # directions stream, so per-transfer memory is bounded, but many
    # simultaneous pack transfers still add up — a cap turns a clone storm
    # into queueing instead of memory pressure. 0 (default) = unlimited.
    max_transfers = int(config.get("git_max_concurrent_transfers", 0) or 0)
    transfer_gate = (asyncio.Semaphore(max_transfers)
                     if max_transfers > 0 else None)

    # Pack transfers are long-lived streams: cloning a large repository
    # legitimately runs for minutes, so the shared session's `total`
    # timeout (default 30s) must not apply — it fires mid-body and
    # truncates any transfer that needs longer than that. Bound only
    # connection setup and inter-read stalls instead: a transfer that
    # keeps moving data is never cut off, a dead upstream still gets
    # reaped after `git_transfer_idle_timeout` seconds of silence.
    idle_timeout = float(config.get("git_transfer_idle_timeout", 60) or 60)
    transfer_timeout = aiohttp.ClientTimeout(
        total=None, sock_connect=30, sock_read=idle_timeout,
    )

    async def handle_git(request: web.Request) -> web.Response:
        owner = request.match_info["owner"]
        repo = request.match_info["repo"]
        path = request.match_info.get("path", "")
        resource = f"{owner}/{repo}"

        credential = select_credential_fn(resource, config)
        if not credential:
            raise web.HTTPForbidden(text=f"No credential for git on {resource}")

        env = await resolve_env_fn(credential)
        if not env:
            raise web.HTTPForbidden(text=f"No credential for git on {resource}")

        if request.method == "POST" and transfer_gate is not None:
            async with transfer_gate:
                return await _proxy_to_github(
                    request, owner, repo, path, env["GH_TOKEN"], github_base,
                    transfer_timeout,
                )
        return await _proxy_to_github(
            request, owner, repo, path, env["GH_TOKEN"], github_base,
            transfer_timeout,
        )

    return [
        ("GET", "/git/{owner}/{repo}.git/{path:.*}", handle_git),
        ("POST", "/git/{owner}/{repo}.git/{path:.*}", handle_git),
    ]


async def _proxy_to_github(request, owner, repo, path, token, github_base,
                           transfer_timeout):
    github_url = f"{github_base}/{owner}/{repo}.git/{path}"
    if request.query_string:
        github_url += f"?{request.query_string}"

    credentials_b64 = base64.b64encode(
        f"x-access-token:{token}".encode()
    ).decode()

    headers = {
        "Authorization": f"Basic {credentials_b64}",
        "User-Agent": "git/2.40.0",
    }
    for h in _FORWARDED_HEADERS:
        if h in request.headers:
            headers[h] = request.headers[h]

    # Relay the request body as a stream instead of buffering it: a push's
    # pack (or a client retrying with a large http.postBuffer) would
    # otherwise sit in proxy memory in full. aiohttp sends an async
    # iterable as a chunked upload, which git smart HTTP accepts.
    body = request.content if request.method == "POST" else None

    session = get_session()
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()
    try:
        async with session.request(
            request.method, github_url,
            headers=headers, data=body, timeout=transfer_timeout,
        ) as resp:
            # Stream the upstream response through instead of buffering it:
            # pack data for a large repository can be hundreds of MB, which
            # OOM-kills the proxy if held in memory.
            out = web.StreamResponse(status=resp.status)
            for h in _RESPONSE_HEADERS:
                if h in resp.headers:
                    out.headers[h] = resp.headers[h]
            await out.prepare(request)
            try:
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    await out.write(chunk)
                await out.write_eof()
            except (ConnectionResetError,
                    aiohttp.ClientConnectionResetError):
                # the client hung up mid-stream — e.g. git-lfs aborts as
                # soon as it sees an error status without draining the
                # body. Their call, not our error; stay quiet.
                pass
            except asyncio.TimeoutError:
                # Upstream stalled mid-body. The status line is already
                # sent, so no error can be conveyed; close the connection
                # without the chunked terminator so the client sees a
                # truncated transfer rather than a complete one.
                logger.warning(
                    "git proxy: upstream stalled mid-transfer on %s %s/%s",
                    request.method, owner, repo,
                )
                if request.transport is not None:
                    request.transport.close()
            return out
    except asyncio.TimeoutError:
        # Upstream never started responding within the timeout — report
        # it instead of surfacing as an opaque connection error.
        logger.warning(
            "git proxy: upstream timed out before responding on %s %s/%s",
            request.method, owner, repo,
        )
        raise web.HTTPGatewayTimeout(
            text=f"upstream git transfer timed out for {owner}/{repo}",
        )
    finally:
        if own_session:
            await session.close()
