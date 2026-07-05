"""GitHub App installation token minting and caching.

Why App tokens exist next to PATs in this plugin: GitHub's Git LFS batch
API rejects fine-grained personal access tokens (a long-standing platform
limitation), so any workflow that pushes or fetches LFS objects through
the proxy cannot run on a fine-grained PAT. App installation tokens are
accepted by every endpoint the proxy fronts — including LFS — and they
can be narrowed to specific repositories and permissions at mint time,
which keeps the least-privilege model this project is named after.

Mechanics: the App's RSA private key signs a short-lived JWT (the App's
own identity), and the JWT mints an installation access token that lives
one hour. The store re-mints a few minutes before expiry, so callers can
treat tokens as always-fresh; the private key on disk is the only
long-lived secret.
"""

import asyncio
import calendar
import time

import aiohttp
import jwt

from fgap.core.http import get_session

DEFAULT_API_BASE = "https://api.github.com"
JWT_TTL_S = 540           # GitHub caps App JWTs at 10 minutes
JWT_BACKDATE_S = 60       # absorb clock drift
REFRESH_MARGIN_S = 300    # re-mint this long before expiry


class AppTokenError(Exception):
    pass


def _load_private_key(cred: dict) -> bytes:
    if "private_key" in cred:
        return cred["private_key"].encode()
    if "private_key_path" in cred:
        with open(cred["private_key_path"], "rb") as f:
            return f.read()
    raise AppTokenError(
        "App credential needs 'private_key' or 'private_key_path'")


def _narrowed_repositories(cred: dict, resource: str) -> list[str] | None:
    """Repository narrowing for the minted token.

    - omitted  -> None (token covers everything the installation covers)
    - "matched" -> narrow to the single repository that matched the
      credential's resource patterns (finest possible grain)
    - [names]  -> narrow to the listed repository names
    """
    repos = cred.get("repositories")
    if repos is None:
        return None
    if repos == "matched":
        return [resource.split("/", 1)[1]]
    return list(repos)


class AppTokenStore:
    """Caches installation tokens per (credential, narrowing) shape."""

    def __init__(self):
        self._cache: dict[tuple, tuple[str, float]] = {}
        self._locks: dict[tuple, asyncio.Lock] = {}

    async def get_token(self, cred: dict, resource: str,
                        api_base: str = DEFAULT_API_BASE) -> str:
        repositories = _narrowed_repositories(cred, resource)
        permissions = cred.get("permissions")
        key = (
            str(cred["app_id"]),
            str(cred["installation_id"]),
            tuple(sorted(repositories)) if repositories else None,
            tuple(sorted(permissions.items())) if permissions else None,
        )
        token = self._fresh(key)
        if token:
            return token
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            token = self._fresh(key)  # minted while we waited
            if token:
                return token
            token, expires_at = await self._mint(
                cred, repositories, permissions, api_base)
            self._cache[key] = (token, expires_at)
            return token

    def _fresh(self, key: tuple) -> str | None:
        cached = self._cache.get(key)
        if cached and cached[1] - time.time() > REFRESH_MARGIN_S:
            return cached[0]
        return None

    async def _mint(self, cred: dict, repositories: list[str] | None,
                    permissions: dict | None,
                    api_base: str) -> tuple[str, float]:
        now = int(time.time())
        app_jwt = jwt.encode(
            {"iat": now - JWT_BACKDATE_S, "exp": now + JWT_TTL_S,
             "iss": str(cred["app_id"])},
            _load_private_key(cred), algorithm="RS256")
        body: dict = {}
        if repositories:
            body["repositories"] = repositories
        if permissions:
            body["permissions"] = permissions

        session = get_session()
        own_session = session is None
        if own_session:
            session = aiohttp.ClientSession()
        try:
            async with session.post(
                f"{api_base}/app/installations/{cred['installation_id']}"
                "/access_tokens",
                headers={"Authorization": f"Bearer {app_jwt}",
                         "Accept": "application/vnd.github+json"},
                json=body,
            ) as resp:
                data = await resp.json()
                if resp.status != 201:
                    raise AppTokenError(
                        f"minting installation token failed "
                        f"({resp.status}): {data.get('message', data)}")
        finally:
            if own_session:
                await session.close()

        # expires_at is RFC3339 UTC, e.g. 2026-01-01T00:00:00Z
        expires_at = calendar.timegm(time.strptime(
            data["expires_at"], "%Y-%m-%dT%H:%M:%SZ"))
        return data["token"], expires_at
