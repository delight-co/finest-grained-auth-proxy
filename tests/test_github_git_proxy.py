import asyncio
import base64

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from fgap.core.router import create_routes
from fgap.plugins.github import GitHubPlugin


@pytest.fixture
async def mock_github_git_server():
    """Mock GitHub git smart HTTP server."""
    # default client_max_size (1MB) would 413 the streamed-body test
    app = web.Application(client_max_size=32 * 1024 * 1024)
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


class TestRequestBodyStreaming:
    async def test_post_body_relayed_without_buffering(
        self, git_proxy_client, mock_github_git_server,
    ):
        # the proxy must not hold the whole body in memory (issue #100):
        # it relays the request stream, which reaches upstream chunked
        _, received = mock_github_git_server
        blob = b"x" * (8 * 1024 * 1024)
        resp = await git_proxy_client.post(
            "/git/owner/repo.git/git-receive-pack",
            data=blob,
            headers={"Content-Type": "application/x-git-receive-pack-request"},
        )
        assert resp.status == 200
        assert received[0]["body"] == blob
        assert received[0]["headers"].get("Transfer-Encoding") == "chunked"
        assert "Content-Length" not in received[0]["headers"]


class TestConcurrentTransferCap:
    async def test_cap_bounds_concurrent_posts(self):
        peak = 0
        current = 0

        async def handle(request):
            nonlocal peak, current
            current += 1
            peak = max(peak, current)
            await asyncio.sleep(0.05)
            current -= 1
            await request.read()
            return web.Response(body=b"OK")

        upstream = web.Application()
        upstream.router.add_route("*", "/{path:.*}", handle)
        async with TestServer(upstream) as server:
            plugin = GitHubPlugin()
            config = {
                "plugins": {
                    "github": {
                        "credentials": [
                            {"token": "t", "resources": ["*"]},
                        ],
                        "_github_base_url": str(server.make_url("")),
                        "git_max_concurrent_transfers": 1,
                    }
                }
            }
            app = create_routes(config, {"github": plugin})
            async with TestClient(TestServer(app)) as client:
                results = await asyncio.gather(*(
                    client.post("/git/owner/repo.git/git-upload-pack",
                                data=b"want")
                    for _ in range(3)
                ))
                assert all(r.status == 200 for r in results)
                assert peak == 1  # transfers were serialized by the cap

    async def test_get_not_gated(self):
        # info/refs (GET) stays outside the cap: it is tiny and gating it
        # would let queued packs starve ref advertisement
        peak = 0
        current = 0

        async def handle(request):
            nonlocal peak, current
            current += 1
            peak = max(peak, current)
            await asyncio.sleep(0.05)
            current -= 1
            return web.Response(body=b"refs")

        upstream = web.Application()
        upstream.router.add_route("*", "/{path:.*}", handle)
        async with TestServer(upstream) as server:
            plugin = GitHubPlugin()
            config = {
                "plugins": {
                    "github": {
                        "credentials": [
                            {"token": "t", "resources": ["*"]},
                        ],
                        "_github_base_url": str(server.make_url("")),
                        "git_max_concurrent_transfers": 1,
                    }
                }
            }
            app = create_routes(config, {"github": plugin})
            async with TestClient(TestServer(app)) as client:
                await asyncio.gather(*(
                    client.get("/git/owner/repo.git/info/refs")
                    for _ in range(3)
                ))
                assert peak > 1  # GETs ran concurrently


class TestTransferTimeout:
    @staticmethod
    def _make_config(server, **github_extra):
        return {
            # a total timeout this small would truncate the slow streams
            # below if it applied to git transfers
            "timeouts": {"http": 0.2},
            "plugins": {
                "github": {
                    "credentials": [
                        {"token": "t", "resources": ["*"]},
                    ],
                    "_github_base_url": str(server.make_url("")),
                    **github_extra,
                }
            },
        }

    async def test_slow_transfer_outlives_session_total_timeout(self):
        # a clone of a large repository streams for longer than the shared
        # session's `total` timeout; the pack must arrive whole regardless
        chunk = b"x" * 1024

        async def handle(request):
            resp = web.StreamResponse()
            resp.headers["Content-Type"] = "application/x-git-upload-pack-result"
            await resp.prepare(request)
            for _ in range(10):  # ~0.5s total, > timeouts.http
                await resp.write(chunk)
                await asyncio.sleep(0.05)
            await resp.write_eof()
            return resp

        upstream = web.Application()
        upstream.router.add_route("*", "/{path:.*}", handle)
        async with TestServer(upstream) as server:
            app = create_routes(self._make_config(server), {"github": GitHubPlugin()})
            async with TestClient(TestServer(app)) as client:
                resp = await client.post(
                    "/git/owner/repo.git/git-upload-pack", data=b"want",
                )
                assert resp.status == 200
                assert await resp.read() == chunk * 10

    async def test_unresponsive_upstream_returns_504(self):
        async def handle(request):
            await asyncio.sleep(0.5)  # > git_transfer_idle_timeout
            return web.Response(body=b"too late")

        upstream = web.Application()
        upstream.router.add_route("*", "/{path:.*}", handle)
        async with TestServer(upstream) as server:
            config = self._make_config(server, git_transfer_idle_timeout=0.1)
            app = create_routes(config, {"github": GitHubPlugin()})
            async with TestClient(TestServer(app)) as client:
                resp = await client.get("/git/owner/repo.git/info/refs")
                assert resp.status == 504

    async def test_midstream_stall_truncates_response(self, caplog):
        # if upstream goes silent mid-body, the stream must end without the
        # chunked terminator (a truncated pack, not a "complete" short one)
        async def handle(request):
            resp = web.StreamResponse()
            await resp.prepare(request)
            await resp.write(b"first")
            await asyncio.sleep(0.5)  # > git_transfer_idle_timeout
            await resp.write(b"never-sent")
            await resp.write_eof()
            return resp

        upstream = web.Application()
        upstream.router.add_route("*", "/{path:.*}", handle)
        async with TestServer(upstream) as server:
            config = self._make_config(server, git_transfer_idle_timeout=0.1)
            app = create_routes(config, {"github": GitHubPlugin()})
            async with TestClient(TestServer(app)) as client:
                resp = await client.post(
                    "/git/owner/repo.git/git-upload-pack", data=b"want",
                )
                assert resp.status == 200
                with pytest.raises(aiohttp.ClientPayloadError):
                    await resp.read()
        assert "stalled mid-transfer" in caplog.text


class TestUserAgentForwarding:
    async def test_client_user_agent_passes_through(
        self, git_proxy_client, mock_github_git_server,
    ):
        _, received = mock_github_git_server
        await git_proxy_client.post(
            "/git/owner/repo.git/info/lfs/objects/batch",
            json={"operation": "upload"},
            headers={"User-Agent": "git-lfs/3.3.0"},
        )
        # git-lfs must be able to identify itself: GitHub's LFS endpoint
        # rejects batch calls that arrive with a plain git User-Agent
        assert received[-1]["headers"]["User-Agent"] == "git-lfs/3.3.0"
