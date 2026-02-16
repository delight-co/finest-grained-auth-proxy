"""Common proxy communication for CLI wrappers.

Provides ProxyClient for sending tool invocations to the fgap proxy
and receiving structured results.
"""

import aiohttp


class ProxyClient:
    """Client for the fgap proxy /cli endpoint.

    Usage::

        client = ProxyClient("http://localhost:8766")
        result = await client.call_cli("gh", ["issue", "list"], "owner/repo")
        # result = {"exit_code": 0, "stdout": "...", "stderr": "..."}
    """

    def __init__(self, proxy_url: str, *, timeout: int = 60):
        self.proxy_url = proxy_url.rstrip("/")
        self.timeout = timeout

    async def call_cli(
        self, tool: str, args: list[str], resource: str,
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

        client_timeout = aiohttp.ClientTimeout(total=self.timeout)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=body, timeout=client_timeout,
                ) as resp:
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
