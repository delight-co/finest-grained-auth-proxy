"""S3-compatible object storage proxy with SigV4 re-signing.

Generalizes the credential-injection pattern to S3-style APIs, where
authentication is a signature over the whole request (method, path,
query, headers, payload hash) rather than a static header.

The sandbox runs a stock S3 client (aws cli, rclone, boto3) configured
with *dummy* credentials and pointed at ``{proxy}/s3/{service}``. This
plugin strips the incoming (dummy) signature, enforces policy, re-signs
the request with the real credentials, and streams it to the upstream
S3-compatible endpoint (AWS S3, Cloudflare R2, MinIO, ...).

Client setup (aws cli)::

    ~/.aws/credentials:
        [media]
        aws_access_key_id = dummy
        aws_secret_access_key = dummy

    ~/.aws/config:
        [profile media]
        region = auto
        endpoint_url = http://proxy-host:8766/s3/media
        request_checksum_calculation = when_required
        response_checksum_validation = when_required
        s3 =
            addressing_style = path

Path-style addressing is required: the bucket must appear in the URL
path, not the hostname.

Policy knobs per service:

- ``buckets``: allow-list of bucket names
- ``deny: ["delete"]``: reject DeleteObject / DeleteObjects /
  DeleteBucket. AbortMultipartUpload stays allowed — it only discards
  an unfinished upload, and blocking it would strand stray parts.
- ``immutable_puts``: inject ``If-None-Match: *`` into object-creating
  requests, so an existing key can never be overwritten (requires an
  upstream that supports conditional writes, e.g. R2 or AWS S3).
"""

import logging

import aiohttp
from aiohttp import web
from botocore.auth import S3SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.config import Config
from botocore.credentials import Credentials
from yarl import URL

logger = logging.getLogger(__name__)

# Total seconds allowed for one proxied transfer (uploads of large
# objects can be slow); connection establishment gets a tighter bound.
DEFAULT_TRANSFER_TIMEOUT = 600

# Client request headers that are forwarded upstream and included in the
# signature. Everything else is dropped — notably the incoming dummy
# Authorization and signature headers.
_FORWARDED_REQUEST_HEADERS = frozenset({
    "accept-encoding",
    "content-type",
    "content-md5",
    "cache-control",
    "content-disposition",
    "content-encoding",
    "content-language",
    "expires",
    "if-match",
    "if-none-match",
    "if-modified-since",
    "if-unmodified-since",
    "range",
})

# x-amz-* headers that are signature artifacts of the incoming request
# or aws-chunked framing metadata; they are regenerated or become
# meaningless after re-signing. All other x-amz-* headers (checksums,
# metadata, storage class, copy source, ...) are forwarded.
_STRIPPED_AMZ_HEADERS = frozenset({
    "x-amz-date",
    "x-amz-content-sha256",
    "x-amz-security-token",
    "x-amz-decoded-content-length",
    "x-amz-trailer",
})

# Upstream response headers forwarded back to the client, in addition to
# all x-amz-* headers.
_FORWARDED_RESPONSE_HEADERS = frozenset({
    "content-type",
    "content-length",
    "content-range",
    "content-encoding",
    "content-disposition",
    "content-language",
    "accept-ranges",
    "cache-control",
    "expires",
    "etag",
    "last-modified",
})


def _check_policy(method: str, bucket: str, query, service_config: dict) -> str | None:
    """Return a denial reason, or None if the request is allowed."""
    buckets = service_config.get("buckets")
    if buckets is not None and bucket not in buckets:
        return f"bucket not allowed: {bucket or '(root)'}"
    if "delete" in service_config.get("deny", []):
        # DELETE with uploadId is AbortMultipartUpload — allowed.
        if method == "DELETE" and "uploadId" not in query:
            return "delete is denied by policy"
        # POST ?delete is the DeleteObjects batch API.
        if method == "POST" and "delete" in query:
            return "batch delete is denied by policy"
    return None


def _creates_object(method: str, key: str, query) -> bool:
    """True for requests that create an object at a key.

    PutObject:               PUT /key            (no uploadId/partNumber)
    CompleteMultipartUpload: POST /key?uploadId=X
    UploadPart (PUT ?partNumber&uploadId) and CreateMultipartUpload
    (POST ?uploads) do not themselves create the object.
    """
    if not key:
        return False
    if method == "PUT":
        return "partNumber" not in query and "uploadId" not in query
    if method == "POST":
        return "uploadId" in query
    return False


def _deny(reason: str) -> web.Response:
    """S3-style XML error so stock clients render the message."""
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Error><Code>AccessDenied</Code>"
        f"<Message>fgap: {reason}</Message></Error>"
    )
    return web.Response(status=403, body=body.encode(), content_type="application/xml")


async def _decode_aws_chunked(stream):
    """Decode an aws-chunked request body, yielding the raw object bytes.

    S3 clients wrap uploads in AWS chunked framing (per-chunk signature
    lines and/or trailing checksums) when they cannot pre-hash the
    payload. The upstream request is re-signed with UNSIGNED-PAYLOAD, so
    the framing must be removed.

    Framing, per chunk: ``{hex-size}[;chunk-signature=...]\\r\\n{data}\\r\\n``
    The final chunk has size 0, optionally followed by trailer lines.
    """
    while True:
        header = await stream.readline()
        if not header:
            return
        line = header.strip()
        if not line:
            continue
        size = int(line.split(b";", 1)[0], 16)
        if size == 0:
            # Consume trailers until the terminating blank line (or EOF).
            while True:
                trailer = await stream.readline()
                if trailer in (b"", b"\r\n", b"\n"):
                    return
        remaining = size
        while remaining:
            chunk = await stream.read(min(remaining, 1 << 16))
            if not chunk:
                raise ValueError("truncated aws-chunked body")
            remaining -= len(chunk)
            yield chunk
        await stream.readline()  # CRLF after chunk data


def make_routes(config: dict) -> list[tuple[str, str, callable]]:
    """Create S3 proxy routes for all configured services.

    Each service exposes ``{method} /s3/{service}/{bucket}/{key...}``
    forwarding to ``{method} {endpoint}/{bucket}/{key...}`` with the
    request re-signed using the service's credentials.
    """
    services = config.get("services", {})
    if not services:
        return []

    async def handle_s3(request: web.Request) -> web.StreamResponse:
        service = request.match_info["service"]
        service_config = services.get(service)
        if not service_config:
            raise web.HTTPNotFound(text=f"Unknown s3 service: {service}")
        return await _proxy_request(request, service, service_config)

    pattern = "/s3/{service}/{path:.*}"
    return [
        (method, pattern, handle_s3)
        for method in ("GET", "PUT", "POST", "DELETE", "HEAD")
    ]


async def _proxy_request(
    request: web.Request, service: str, cfg: dict,
) -> web.StreamResponse:
    endpoint = cfg["endpoint"].rstrip("/")
    region = cfg.get("region", "auto")

    # The signature covers the exact bytes of path and query, so build
    # the upstream URL from the still-encoded raw path and query.
    # botocore signs the query string as given (no re-encoding) while S3
    # endpoints canonicalize what arrives on the wire — the two agree
    # only if the client's original (canonical-form) bytes are preserved.
    prefix = f"/s3/{service}"
    raw_path, _, raw_query = request.raw_path.partition("?")
    object_path = raw_path[len(prefix):] or "/"
    upstream_url = f"{endpoint}{object_path}"
    if raw_query:
        upstream_url += f"?{raw_query}"

    decoded_path = request.match_info.get("path", "")
    bucket, _, key = decoded_path.partition("/")

    reason = _check_policy(request.method, bucket, request.query, cfg)
    if reason:
        logger.warning(
            "s3 service=%s method=%s bucket=%s denied: %s",
            service, request.method, bucket, reason,
        )
        return _deny(reason)

    # Headers to sign and forward.
    headers = {}
    for name, value in request.headers.items():
        lname = name.lower()
        if lname in _FORWARDED_REQUEST_HEADERS:
            headers[name] = value
        elif lname.startswith("x-amz-") and lname not in _STRIPPED_AMZ_HEADERS:
            headers[name] = value

    if cfg.get("immutable_puts") and _creates_object(request.method, key, request.query):
        headers["If-None-Match"] = "*"

    # Body: stream through without buffering. aws-chunked framing is
    # decoded because the re-signed request is UNSIGNED-PAYLOAD.
    body = None
    content_length = request.headers.get("Content-Length")
    if request.body_exists:
        incoming_sha = request.headers.get("x-amz-content-sha256", "")
        encoding = request.headers.get("Content-Encoding", "")
        encodings = [e.strip() for e in encoding.split(",") if e.strip()]
        if "aws-chunked" in [e.lower() for e in encodings] or incoming_sha.startswith("STREAMING-"):
            body = _decode_aws_chunked(request.content)
            content_length = request.headers.get("x-amz-decoded-content-length")
            remaining = [e for e in encodings if e.lower() != "aws-chunked"]
            if remaining:
                headers["Content-Encoding"] = ", ".join(remaining)
            else:
                headers.pop("Content-Encoding", None)
        else:
            body = request.content

    # Re-sign. Content-Length stays out of the signed headers (S3 does
    # not require it there) so the HTTP client owns the wire value.
    credentials = Credentials(cfg["access_key_id"], cfg["secret_access_key"])
    aws_request = AWSRequest(method=request.method, url=upstream_url, headers=headers)
    if body is not None:
        # UNSIGNED-PAYLOAD: the payload hash cannot be known without
        # buffering the stream; TLS to the upstream protects integrity.
        aws_request.context["client_config"] = Config(
            s3={"payload_signing_enabled": False},
        )
    S3SigV4Auth(credentials, "s3", region).add_auth(aws_request)

    out_headers = dict(aws_request.headers)
    out_headers["User-Agent"] = "fgap"
    if body is not None and content_length is not None:
        # Explicit Content-Length keeps aiohttp from switching to
        # chunked transfer encoding, which not all S3 endpoints accept.
        out_headers["Content-Length"] = content_length

    timeout = aiohttp.ClientTimeout(
        total=cfg.get("timeout", DEFAULT_TRANSFER_TIMEOUT),
        sock_connect=30,
    )
    # Dedicated session: transfers outlive the shared session's timeout,
    # and auto_decompress must be off for byte-exact pass-through.
    session = aiohttp.ClientSession(timeout=timeout, auto_decompress=False)
    try:
        async with session.request(
            request.method,
            URL(upstream_url, encoded=True),
            headers=out_headers,
            data=body,
            allow_redirects=False,
            # Never add headers the client didn't send: an auto-added
            # Accept-Encoding makes the upstream compress responses the
            # client never asked for (and can't always decode).
            skip_auto_headers=("Accept-Encoding",),
        ) as resp:
            response = web.StreamResponse(status=resp.status)
            for name, value in resp.headers.items():
                lname = name.lower()
                if lname in _FORWARDED_RESPONSE_HEADERS or lname.startswith("x-amz-"):
                    response.headers[name] = value
            await response.prepare(request)
            async for chunk in resp.content.iter_any():
                await response.write(chunk)
            await response.write_eof()

            logger.info(
                "s3 service=%s method=%s bucket=%s key=%s status=%d",
                service, request.method, bucket, key, resp.status,
            )
            return response
    finally:
        await session.close()
