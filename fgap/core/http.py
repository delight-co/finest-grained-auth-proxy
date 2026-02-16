"""Shared HTTP session for outbound requests.

The server creates a single ClientSession on startup and shares it
across all handlers.  Functions that need an HTTP session call
``get_session()`` — if the server session exists it's returned,
otherwise they fall back to creating their own (backward compatible
with tests that don't initialise the server).
"""

import aiohttp

_session: aiohttp.ClientSession | None = None


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
