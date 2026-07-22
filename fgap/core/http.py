"""Shared HTTP clients for outbound requests.

The server creates a single aiohttp ClientSession on startup and shares
it across all handlers.  Functions that need an HTTP session call
``get_session()`` — if the server session exists it's returned,
otherwise they fall back to creating their own (backward compatible
with tests that don't initialise the server).

A second, HTTP/2-capable client (httpx) is shared the same way via
``get_h2_client()``.  Streaming upstreams use it because some edges
only pass SSE through unbuffered on HTTP/2; httpx negotiates h2 via
ALPN and falls back to HTTP/1.1 when the upstream doesn't offer it.
"""

import aiohttp
import httpx

_session: aiohttp.ClientSession | None = None
_h2_client: httpx.AsyncClient | None = None


def set_session(session: aiohttp.ClientSession) -> None:
    """Store the shared session (called on server startup)."""
    global _session
    _session = session


def get_session() -> aiohttp.ClientSession | None:
    """Return the shared session, or None if not initialised."""
    return _session


async def close_session() -> None:
    """Close the shared session (called on server shutdown)."""
    global _session
    if _session:
        await _session.close()
        _session = None


def set_h2_client(client: httpx.AsyncClient) -> None:
    """Store the shared HTTP/2-capable client (called on server startup)."""
    global _h2_client
    _h2_client = client


def get_h2_client() -> httpx.AsyncClient | None:
    """Return the shared HTTP/2-capable client, or None if not initialised."""
    return _h2_client


async def close_h2_client() -> None:
    """Close the shared HTTP/2-capable client (called on server shutdown)."""
    global _h2_client
    if _h2_client:
        await _h2_client.aclose()
        _h2_client = None
