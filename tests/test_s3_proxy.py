"""Tests for the S3-compatible storage proxy plugin."""

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from multidict import MultiDict

from fgap.plugins.s3.proxy import (
    _check_policy,
    _creates_object,
    make_routes,
)

REAL_KEY_ID = "REALACCESSKEY"
REAL_SECRET = "real-secret-key"


# =============================================================================
# Policy
# =============================================================================


class TestCheckPolicy:
    def test_no_policy_allows_everything(self):
        assert _check_policy("DELETE", "any-bucket", MultiDict(), {}) is None

    def test_bucket_allowed(self):
        cfg = {"buckets": ["media"]}
        assert _check_policy("GET", "media", MultiDict(), cfg) is None

    def test_bucket_denied(self):
        cfg = {"buckets": ["media"]}
        assert _check_policy("GET", "other", MultiDict(), cfg) is not None

    def test_root_denied_with_allowlist(self):
        # ListBuckets has no bucket in the path
        cfg = {"buckets": ["media"]}
        assert _check_policy("GET", "", MultiDict(), cfg) is not None

    def test_delete_denied(self):
        cfg = {"deny": ["delete"]}
        assert _check_policy("DELETE", "media", MultiDict(), cfg) is not None

    def test_abort_multipart_allowed_despite_deny(self):
        cfg = {"deny": ["delete"]}
        query = MultiDict({"uploadId": "xyz"})
        assert _check_policy("DELETE", "media", query, cfg) is None

    def test_batch_delete_denied(self):
        cfg = {"deny": ["delete"]}
        query = MultiDict({"delete": ""})
        assert _check_policy("POST", "media", query, cfg) is not None

    def test_delete_allowed_without_deny(self):
        assert _check_policy("DELETE", "media", MultiDict(), {"buckets": ["media"]}) is None


class TestCreatesObject:
    def test_put_object(self):
        assert _creates_object("PUT", "some/key.mp4", MultiDict())

    def test_upload_part_is_not_creation(self):
        query = MultiDict({"partNumber": "1", "uploadId": "xyz"})
        assert not _creates_object("PUT", "some/key.mp4", query)

    def test_complete_multipart_is_creation(self):
        query = MultiDict({"uploadId": "xyz"})
        assert _creates_object("POST", "some/key.mp4", query)

    def test_create_multipart_is_not_creation(self):
        query = MultiDict({"uploads": ""})
        assert not _creates_object("POST", "some/key.mp4", query)

    def test_get_is_not_creation(self):
        assert not _creates_object("GET", "some/key.mp4", MultiDict())

    def test_bucket_only_is_not_creation(self):
        assert not _creates_object("PUT", "", MultiDict())


# =============================================================================
# Route generation
# =============================================================================


class TestMakeRoutes:
    def test_no_services_returns_empty(self):
        assert make_routes({}) == []
        assert make_routes({"services": {}}) == []

    def test_creates_routes_for_s3_methods(self):
        config = {"services": {"media": {"endpoint": "https://example.com"}}}
        routes = make_routes(config)
        methods = {r[0] for r in routes}
        assert methods == {"GET", "PUT", "POST", "DELETE", "HEAD"}


# =============================================================================
# Proxy integration
# =============================================================================


@pytest.fixture
async def mock_upstream():
    """Mock S3-compatible upstream that records requests."""
    app = web.Application(client_max_size=0)
    state = {"requests": []}

    async def handle(request: web.Request):
        body = await request.read()
        state["requests"].append({
            "method": request.method,
            "path": request.path,
            "query": dict(request.query),
            "raw_path_qs": request.raw_path,
            "headers": dict(request.headers),
            "body": body,
        })
        return web.Response(
            body=b"upstream-response",
            headers={
                "ETag": '"abc123"',
                "x-amz-request-id": "REQ42",
                "X-Secret-Internal": "do-not-forward",
            },
        )

    app.router.add_route("*", "/{path:.*}", handle)
    async with TestServer(app) as server:
        yield server, state


@pytest.fixture
async def proxy_app(mock_upstream):
    """fgap app with two s3 services: plain and locked-down."""
    server, state = mock_upstream
    endpoint = str(server.make_url("")).rstrip("/")

    config = {
        "services": {
            "plain": {
                "endpoint": endpoint,
                "region": "auto",
                "access_key_id": REAL_KEY_ID,
                "secret_access_key": REAL_SECRET,
            },
            "locked": {
                "endpoint": endpoint,
                "region": "auto",
                "access_key_id": REAL_KEY_ID,
                "secret_access_key": REAL_SECRET,
                "buckets": ["media"],
                "deny": ["delete"],
                "immutable_puts": True,
            },
        },
    }

    app = web.Application(client_max_size=0)
    for method, path, handler in make_routes(config):
        app.router.add_route(method, path, handler)

    async with TestServer(app) as proxy_server:
        yield proxy_server, state


async def _request(proxy, method, path, **kwargs):
    import aiohttp
    from yarl import URL

    # encoded=True: send the path exactly as written, like a real S3
    # client does — yarl would otherwise re-normalize percent-encoding.
    url = URL(str(proxy.make_url("/")).rstrip("/") + path, encoded=True)
    async with aiohttp.ClientSession() as session:
        async with session.request(method, url, **kwargs) as resp:
            # resp.headers is case-insensitive; keep it that way
            return resp.status, await resp.read(), resp.headers


class TestS3Proxy:
    async def test_get_resigned_and_forwarded(self, proxy_app):
        proxy, state = proxy_app
        status, body, _ = await _request(
            proxy, "GET", "/s3/plain/media/path/file.mp4",
            headers={
                # Dummy signature as a stock client would send it
                "Authorization": "AWS4-HMAC-SHA256 Credential=dummy/x/y/s3/aws4_request, SignedHeaders=host, Signature=000",
                "X-Amz-Date": "20200101T000000Z",
                "X-Amz-Content-SHA256": "dummyhash",
            },
        )
        assert status == 200
        assert body == b"upstream-response"

        req = state["requests"][-1]
        assert req["method"] == "GET"
        assert req["path"] == "/media/path/file.mp4"
        auth = req["headers"]["Authorization"]
        assert auth.startswith("AWS4-HMAC-SHA256")
        assert f"Credential={REAL_KEY_ID}/" in auth
        assert "dummy" not in auth
        # Signature artifacts regenerated, not passed through
        assert req["headers"]["X-Amz-Date"] != "20200101T000000Z"
        assert req["headers"]["X-Amz-Content-SHA256"] != "dummyhash"

    async def test_put_streams_body_with_content_length(self, proxy_app):
        proxy, state = proxy_app
        payload = b"x" * 10_000
        status, _, _ = await _request(
            proxy, "PUT", "/s3/plain/media/video.mp4",
            data=payload,
            headers={"Content-Type": "video/mp4"},
        )
        assert status == 200

        req = state["requests"][-1]
        assert req["body"] == payload
        assert req["headers"]["Content-Length"] == str(len(payload))
        assert "Transfer-Encoding" not in req["headers"]
        assert req["headers"]["Content-Type"] == "video/mp4"
        # Streamed body is signed as UNSIGNED-PAYLOAD
        assert req["headers"]["X-Amz-Content-SHA256"] == "UNSIGNED-PAYLOAD"

    async def test_amz_metadata_forwarded(self, proxy_app):
        proxy, state = proxy_app
        status, _, _ = await _request(
            proxy, "PUT", "/s3/plain/media/file.bin",
            data=b"data",
            headers={"x-amz-meta-origin": "workflow-7"},
        )
        assert status == 200
        req = state["requests"][-1]
        assert req["headers"]["x-amz-meta-origin"] == "workflow-7"
        assert "x-amz-meta-origin" in req["headers"]["Authorization"]

    async def test_query_string_forwarded_byte_exact(self, proxy_app):
        # botocore signs the query as given and S3 endpoints canonicalize
        # the wire bytes; percent-encoding must survive untouched or
        # signatures break (e.g. ListObjectsV2 with prefix=a%2Fb).
        proxy, state = proxy_app
        status, _, _ = await _request(
            proxy, "GET", "/s3/plain/media?list-type=2&prefix=team%2F",
        )
        assert status == 200
        req = state["requests"][-1]
        assert req["raw_path_qs"] == "/media?list-type=2&prefix=team%2F"
        assert req["query"]["prefix"] == "team/"

    async def test_accept_encoding_forwarded(self, proxy_app):
        proxy, state = proxy_app
        status, _, _ = await _request(
            proxy, "GET", "/s3/plain/media/file.bin",
            headers={"Accept-Encoding": "identity"},
        )
        assert status == 200
        req = state["requests"][-1]
        assert req["headers"]["Accept-Encoding"] == "identity"
        assert "accept-encoding" in req["headers"]["Authorization"]

    async def test_no_accept_encoding_injected(self, proxy_app):
        # aiohttp must not auto-add Accept-Encoding: the upstream would
        # compress responses the client never asked for. The client-side
        # auto-header is skipped too so the proxy really receives none.
        proxy, state = proxy_app
        status, _, _ = await _request(
            proxy, "GET", "/s3/plain/media/file.bin",
            skip_auto_headers=("Accept-Encoding",),
        )
        assert status == 200
        assert "Accept-Encoding" not in state["requests"][-1]["headers"]

    async def test_response_headers_forwarded_selectively(self, proxy_app):
        proxy, state = proxy_app
        _, _, headers = await _request(proxy, "GET", "/s3/plain/media/file.bin")
        assert headers["ETag"] == '"abc123"'
        assert headers["x-amz-request-id"] == "REQ42"
        assert "X-Secret-Internal" not in headers

    async def test_unknown_service_404(self, proxy_app):
        proxy, state = proxy_app
        status, _, _ = await _request(proxy, "GET", "/s3/nope/bucket/key")
        assert status == 404

    async def test_head_request(self, proxy_app):
        proxy, state = proxy_app
        status, _, _ = await _request(proxy, "HEAD", "/s3/plain/media/file.bin")
        assert status == 200
        assert state["requests"][-1]["method"] == "HEAD"


class TestS3ProxyPolicy:
    async def test_bucket_allowlist_enforced(self, proxy_app):
        proxy, state = proxy_app
        before = len(state["requests"])
        status, body, _ = await _request(proxy, "GET", "/s3/locked/other-bucket/key")
        assert status == 403
        assert b"AccessDenied" in body
        assert len(state["requests"]) == before  # never reached upstream

    async def test_delete_denied(self, proxy_app):
        proxy, state = proxy_app
        before = len(state["requests"])
        status, body, _ = await _request(proxy, "DELETE", "/s3/locked/media/key.mp4")
        assert status == 403
        assert b"AccessDenied" in body
        assert len(state["requests"]) == before

    async def test_batch_delete_denied(self, proxy_app):
        proxy, state = proxy_app
        status, _, _ = await _request(
            proxy, "POST", "/s3/locked/media?delete", data=b"<Delete/>",
        )
        assert status == 403

    async def test_abort_multipart_allowed(self, proxy_app):
        proxy, state = proxy_app
        status, _, _ = await _request(
            proxy, "DELETE", "/s3/locked/media/key.mp4?uploadId=xyz",
        )
        assert status == 200

    async def test_delete_allowed_on_plain_service(self, proxy_app):
        proxy, state = proxy_app
        status, _, _ = await _request(proxy, "DELETE", "/s3/plain/media/key.mp4")
        assert status == 200


class TestImmutablePuts:
    async def test_put_gets_if_none_match(self, proxy_app):
        proxy, state = proxy_app
        status, _, _ = await _request(
            proxy, "PUT", "/s3/locked/media/new.mp4", data=b"data",
        )
        assert status == 200
        req = state["requests"][-1]
        assert req["headers"]["If-None-Match"] == "*"
        assert "if-none-match" in req["headers"]["Authorization"]

    async def test_upload_part_not_marked(self, proxy_app):
        proxy, state = proxy_app
        status, _, _ = await _request(
            proxy, "PUT", "/s3/locked/media/new.mp4?partNumber=1&uploadId=xyz",
            data=b"data",
        )
        assert status == 200
        assert "If-None-Match" not in state["requests"][-1]["headers"]

    async def test_complete_multipart_marked(self, proxy_app):
        proxy, state = proxy_app
        status, _, _ = await _request(
            proxy, "POST", "/s3/locked/media/new.mp4?uploadId=xyz",
            data=b"<CompleteMultipartUpload/>",
        )
        assert status == 200
        assert state["requests"][-1]["headers"]["If-None-Match"] == "*"

    async def test_plain_service_untouched(self, proxy_app):
        proxy, state = proxy_app
        status, _, _ = await _request(
            proxy, "PUT", "/s3/plain/media/new.mp4", data=b"data",
        )
        assert status == 200
        assert "If-None-Match" not in state["requests"][-1]["headers"]


def _aws_chunked_body(*chunks: bytes, trailer: bytes = b"") -> bytes:
    out = b""
    for chunk in chunks:
        out += f"{len(chunk):x};chunk-signature=deadbeef\r\n".encode()
        out += chunk + b"\r\n"
    out += b"0;chunk-signature=deadbeef\r\n"
    if trailer:
        out += trailer + b"\r\n"
    out += b"\r\n"
    return out


class TestAwsChunkedDecoding:
    async def test_chunked_body_decoded(self, proxy_app):
        proxy, state = proxy_app
        raw = _aws_chunked_body(b"a" * 100, b"b" * 50)
        status, _, _ = await _request(
            proxy, "PUT", "/s3/plain/media/file.bin",
            data=raw,
            headers={
                "Content-Encoding": "aws-chunked",
                "X-Amz-Decoded-Content-Length": "150",
                "X-Amz-Content-SHA256": "STREAMING-AWS4-HMAC-SHA256-PAYLOAD",
            },
        )
        assert status == 200
        req = state["requests"][-1]
        assert req["body"] == b"a" * 100 + b"b" * 50
        assert req["headers"]["Content-Length"] == "150"
        assert "aws-chunked" not in req["headers"].get("Content-Encoding", "")
        assert "X-Amz-Decoded-Content-Length" not in req["headers"]

    async def test_chunked_with_checksum_trailer(self, proxy_app):
        proxy, state = proxy_app
        raw = _aws_chunked_body(
            b"payload-bytes", trailer=b"x-amz-checksum-crc32:AAAAAA==",
        )
        status, _, _ = await _request(
            proxy, "PUT", "/s3/plain/media/file.bin",
            data=raw,
            headers={
                "Content-Encoding": "aws-chunked",
                "X-Amz-Decoded-Content-Length": "13",
                "X-Amz-Trailer": "x-amz-checksum-crc32",
            },
        )
        assert status == 200
        req = state["requests"][-1]
        assert req["body"] == b"payload-bytes"
        assert "X-Amz-Trailer" not in req["headers"]
