import base64
import logging

import aiohttp
from aiohttp import web

logger = logging.getLogger(__name__)

_FORWARDED_HEADERS = ("Content-Type", "Accept", "Content-Encoding")
_RESPONSE_HEADERS = ("Content-Type", "Cache-Control")


def make_routes(select_credential_fn, config):
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

        token = credential["env"]["GH_TOKEN"]
        return await _proxy_to_github(
            request, owner, repo, path, token, github_base,
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
    timeout = aiohttp.ClientTimeout(total=60)

    async with aiohttp.ClientSession() as session:
        async with session.request(
            request.method, github_url,
            headers=headers, data=body, timeout=timeout,
        ) as resp:
            response_body = await resp.read()
            response_headers = {}
            for h in _RESPONSE_HEADERS:
                if h in resp.headers:
                    response_headers[h] = resp.headers[h]
            return web.Response(
                body=response_body,
                status=resp.status,
                headers=response_headers,
            )
