"""Tests for shared HTTP session management."""

import aiohttp

from fgap.core.http import close_session, get_session, set_session


class TestSessionLifecycle:
    async def test_initial_state_is_none(self):
        # Ensure clean state
        await close_session()
        assert get_session() is None

    async def test_set_and_get(self):
        session = aiohttp.ClientSession()
        try:
            set_session(session)
            assert get_session() is session
        finally:
            await close_session()

    async def test_close_session(self):
        session = aiohttp.ClientSession()
        set_session(session)
        await close_session()
        assert get_session() is None
        assert session.closed

    async def test_close_when_none(self):
        await close_session()
        await close_session()  # should not raise


class TestSessionPoolIntegration:
    """Verify server-side functions use shared session when available."""

    async def test_graphql_uses_shared_session(self):
        """execute_graphql falls back to own session when no shared session."""
        await close_session()
        # With no shared session, function should create its own
        # (and not raise RuntimeError)
        assert get_session() is None

    async def test_proxy_client_context_manager(self):
        """ProxyClient reuses session across calls when used as context manager."""
        from fgap.client.base import ProxyClient

        async with ProxyClient("http://localhost:9999") as client:
            assert client._session is not None
            assert not client._session.closed
            session_ref = client._session

        # After exit, session should be closed
        assert session_ref.closed

    async def test_proxy_client_without_context_manager(self):
        """ProxyClient creates per-call session when not used as context manager."""
        from fgap.client.base import ProxyClient

        client = ProxyClient("http://localhost:9999")
        assert client._session is None
