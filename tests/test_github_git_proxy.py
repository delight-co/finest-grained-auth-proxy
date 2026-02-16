import base64

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from fgap.core.router import create_routes
from fgap.plugins.github import GitHubPlugin


@pytest.fixture
async def mock_github_git_server():
    """Mock GitHub git smart HTTP server."""
    app = web.Application()
    received = []

    async def handle(request):
        body = await request.read()
        received.append({
            "method": request.method,
            "path": request.path,
            "query": request.query_string,
            "headers": dict(request.headers),
            "body": body,
        })

        if "info/refs" in request.path:
            return web.Response(
                body=b"001e# service=git-upload-pack\n",
                headers={"Content-Type": "application/x-git-upload-pack-advertisement"},
            )
        if "git-upload-pack" in request.path:
            return web.Response(
                body=b"PACK-DATA",
                headers={"Content-Type": "application/x-git-upload-pack-result"},
            )
        if "git-receive-pack" in request.path:
            return web.Response(
                body=b"PUSH-OK",
                headers={"Content-Type": "application/x-git-receive-pack-result"},
            )
        return web.Response(status=404)

    app.router.add_route("*", "/{path:.*}", handle)

    async with TestServer(app) as server:
        yield server, received


@pytest.fixture
async def git_proxy_client(mock_github_git_server):
    server, _ = mock_github_git_server
    plugin = GitHubPlugin()
    config = {
        "plugins": {
            "github": {
                "credentials": [
                    {"token": "test_pat_xxx", "resources": ["*"]},
                ],
                "_github_base_url": str(server.make_url("")),
            }
        }
    }
    app = create_routes(config, {"github": plugin})
    async with TestClient(TestServer(app)) as client:
        yield client


class TestGitProxy:
    async def test_info_refs_forwarded(self, git_proxy_client, mock_github_git_server):
        _, received = mock_github_git_server
        resp = await git_proxy_client.get(
            "/git/owner/repo.git/info/refs?service=git-upload-pack",
        )
        assert resp.status == 200
        body = await resp.read()
        assert b"service=git-upload-pack" in body

    async def test_auth_header_injected(self, git_proxy_client, mock_github_git_server):
        _, received = mock_github_git_server
        await git_proxy_client.get("/git/owner/repo.git/info/refs")
        expected = base64.b64encode(b"x-access-token:test_pat_xxx").decode()
        assert received[0]["headers"]["Authorization"] == f"Basic {expected}"

    async def test_upload_pack_body_forwarded(self, git_proxy_client, mock_github_git_server):
        _, received = mock_github_git_server
        resp = await git_proxy_client.post(
            "/git/owner/repo.git/git-upload-pack",
            data=b"want-line",
            headers={"Content-Type": "application/x-git-upload-pack-request"},
        )
        assert resp.status == 200
        assert await resp.read() == b"PACK-DATA"
        assert received[0]["body"] == b"want-line"
        assert received[0]["headers"]["Content-Type"] == "application/x-git-upload-pack-request"

    async def test_receive_pack(self, git_proxy_client):
        resp = await git_proxy_client.post(
            "/git/owner/repo.git/git-receive-pack",
            data=b"push-data",
            headers={"Content-Type": "application/x-git-receive-pack-request"},
        )
        assert resp.status == 200
        assert await resp.read() == b"PUSH-OK"

    async def test_query_string_forwarded(self, git_proxy_client, mock_github_git_server):
        _, received = mock_github_git_server
        await git_proxy_client.get(
            "/git/owner/repo.git/info/refs?service=git-upload-pack",
        )
        assert received[0]["query"] == "service=git-upload-pack"

    async def test_no_credential_returns_403(self):
        plugin = GitHubPlugin()
        config = {
            "plugins": {
                "github": {
                    "credentials": [
                        {"token": "t", "resources": ["specific/only"]},
                    ],
                }
            }
        }
        app = create_routes(config, {"github": plugin})
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/git/other/repo.git/info/refs")
            assert resp.status == 403

    async def test_content_type_response_forwarded(self, git_proxy_client):
        resp = await git_proxy_client.get("/git/owner/repo.git/info/refs")
        assert resp.headers.get("Content-Type") == "application/x-git-upload-pack-advertisement"
