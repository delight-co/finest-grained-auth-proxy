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

        return await _proxy_to_github(
            request, owner, repo, path, env["GH_TOKEN"], github_base,
        )

    return [
        ("GET", "/git/{owner}/{repo}.git/{path:.*}", handle_git),
        ("POST", "/git/{owner}/{repo}.git/{path:.*}", handle_git),
    ]


async def _proxy_to_github(request, owner, repo, path, token, github_base):
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

    body = await request.read() if request.method == "POST" else None

    session = get_session()
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()
    try:
        async with session.request(
            request.method, github_url,
            headers=headers, data=body,
        ) as resp:
            # Stream the upstream response through instead of buffering it:
            # pack data for a large repository can be hundreds of MB, which
            # OOM-kills the proxy if held in memory.
            out = web.StreamResponse(status=resp.status)
            for h in _RESPONSE_HEADERS:
                if h in resp.headers:
                    out.headers[h] = resp.headers[h]
            await out.prepare(request)
            async for chunk in resp.content.iter_chunked(64 * 1024):
                await out.write(chunk)
            await out.write_eof()
            return out
    finally:
        if own_session:
            await session.close()
