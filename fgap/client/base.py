"""Common proxy communication for CLI wrappers.

Provides ProxyClient for sending tool invocations to the fgap proxy
and receiving structured results.
"""

import aiohttp


class ProxyClient:
    """Client for the fgap proxy /cli endpoint.

    Usage as async context manager (recommended — reuses session)::

        async with ProxyClient("http://localhost:8766") as client:
            result = await client.call_cli("gh", ["issue", "list"], "owner/repo")

    Usage without context manager (creates session per call)::

        client = ProxyClient("http://localhost:8766")
        result = await client.call_cli("gh", ["issue", "list"], "owner/repo")
    """

    def __init__(self, proxy_url: str, *, timeout: int = 60):
        self.proxy_url = proxy_url.rstrip("/")
        self.timeout = timeout
        self._session: aiohttp.ClientSession | None = None
        self._owns_session = False

    async def __aenter__(self):
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.timeout),
        )
        self._owns_session = True
        return self

    async def __aexit__(self, *exc):
        if self._owns_session and self._session:
            await self._session.close()
            self._session = None
            self._owns_session = False

    async def _get_session(self) -> tuple[aiohttp.ClientSession, bool]:
        """Return (session, should_close)."""
        if self._session:
            return self._session, False
        return aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.timeout),
        ), True

    async def call_cli(
        self, tool: str, args: list[str], resource: str,
        *, stdin_data: str | None = None,
    ) -> dict:
        """Send a CLI invocation to the proxy.

        Returns:
            {"exit_code": int, "stdout": str, "stderr": str}

        Raises:
            ConnectionError: Cannot reach the proxy.
            ValueError: Proxy returned an error (4xx/5xx) or invalid response.
        """
        url = f"{self.proxy_url}/cli"
        body = {"tool": tool, "args": args, "resource": resource}
        if stdin_data is not None:
            body["stdin_data"] = stdin_data

        session, should_close = await self._get_session()
        try:
            async with session.post(url, json=body) as resp:
                content_type = resp.headers.get("Content-Type", "")
                if "text/html" in content_type:
                    raise ValueError(
                        f"Proxy returned HTML (status {resp.status})"
                    )

                if resp.status != 200:
                    text = await resp.text()
                    raise ValueError(
                        f"Proxy error (status {resp.status}): {text}"
                    )

                data = await resp.json()

                if "exit_code" not in data:
                    raise ValueError(
                        f"Invalid proxy response: missing exit_code"
                    )

                return {
                    "exit_code": data["exit_code"],
                    "stdout": data.get("stdout", ""),
                    "stderr": data.get("stderr", ""),
                }
        except aiohttp.ClientConnectionError as e:
            raise ConnectionError(
                f"Cannot connect to proxy at {self.proxy_url}: {e}"
            ) from e
        finally:
            if should_close:
                await session.close()

    async def get_auth_status(self) -> dict:
        """Get credential status from the proxy.

        Returns:
            {"plugins": {"github": [...], "google": [...]}}

        Raises:
            ConnectionError: Cannot reach the proxy.
            ValueError: Proxy returned an error or invalid response.
        """
        url = f"{self.proxy_url}/auth/status"

        session, should_close = await self._get_session()
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise ValueError(
                        f"Proxy error (status {resp.status}): {text}"
                    )
                return await resp.json()
        except aiohttp.ClientConnectionError as e:
            raise ConnectionError(
                f"Cannot connect to proxy at {self.proxy_url}: {e}"
            ) from e
        finally:
            if should_close:
                await session.close()
