"""OAuth2 token manager with automatic refresh.

Manages access_token + refresh_token lifecycle:
- Checks expiry before each request
- Refreshes automatically when expired
- Persists token state to disk (refresh_token is single-use for some providers)

Token state file format::

    {
        "access_token": "...",
        "refresh_token": "...",
        "expires_at": 1234567890.0
    }
"""

import json
import logging
import os
import time

import aiohttp

logger = logging.getLogger(__name__)

# Refresh 30 seconds before actual expiry to avoid race conditions
_EXPIRY_BUFFER_SECONDS = 30

# Default token state directory
_DEFAULT_STATE_DIR = "/var/lib/fgap/tokens"


class OAuth2TokenManager:
    """Manages OAuth2 tokens with automatic refresh."""

    def __init__(
        self,
        service_name: str,
        token_url: str,
        client_id: str,
        client_secret: str,
        initial_refresh_token: str,
        initial_access_token: str = "",
        state_dir: str = _DEFAULT_STATE_DIR,
    ):
        self.service_name = service_name
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self._state_dir = state_dir

        # Try to load persisted state, fall back to initial values
        state = self._load_state()
        if state:
            self._access_token = state.get("access_token", initial_access_token)
            self._refresh_token = state.get("refresh_token", initial_refresh_token)
            self._expires_at = state.get("expires_at", 0.0)
            logger.info("Loaded persisted token state for %s", service_name)
        else:
            self._access_token = initial_access_token
            self._refresh_token = initial_refresh_token
            self._expires_at = 0.0  # Force refresh on first use

    @property
    def access_token(self) -> str:
        return self._access_token

    def is_expired(self) -> bool:
        return time.time() >= (self._expires_at - _EXPIRY_BUFFER_SECONDS)

    async def get_valid_token(self) -> str:
        """Return a valid access token, refreshing if needed."""
        if not self.is_expired() and self._access_token:
            return self._access_token
        await self.refresh()
        return self._access_token

    async def refresh(self) -> None:
        """Refresh the access token using the refresh token."""
        logger.info("Refreshing OAuth2 token for %s", self.service_name)

        data = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self._refresh_token,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.token_url,
                data=data,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(
                        f"OAuth2 refresh failed for {self.service_name}: "
                        f"HTTP {resp.status}: {text}"
                    )

                result = await resp.json()

        self._access_token = result["access_token"]
        self._expires_at = time.time() + result.get("expires_in", 3600)

        # Some providers (e.g. freee) return a new refresh_token each time
        if "refresh_token" in result:
            self._refresh_token = result["refresh_token"]

        self._save_state()
        logger.info(
            "OAuth2 token refreshed for %s (expires in %ds)",
            self.service_name,
            result.get("expires_in", 0),
        )

    async def handle_401(self) -> str:
        """Called when upstream returns 401. Force refresh and return new token."""
        self._expires_at = 0.0  # Force expiry
        return await self.get_valid_token()

    def _state_file(self) -> str:
        return os.path.join(self._state_dir, f"{self.service_name}.json")

    def _load_state(self) -> dict | None:
        path = self._state_file()
        if not os.path.isfile(path):
            return None
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load token state for %s: %s", self.service_name, e)
            return None

    def _save_state(self) -> None:
        path = self._state_file()
        try:
            os.makedirs(self._state_dir, exist_ok=True)
            state = {
                "access_token": self._access_token,
                "refresh_token": self._refresh_token,
                "expires_at": self._expires_at,
            }
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(state, f)
            os.replace(tmp, path)
        except OSError as e:
            logger.warning("Failed to save token state for %s: %s", self.service_name, e)
