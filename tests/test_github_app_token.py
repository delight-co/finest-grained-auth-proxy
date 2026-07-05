"""GitHub App installation tokens: minting, caching, narrowing, and the
resolve path that turns an App credential into injectable env vars."""

import time

import jwt
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from fgap.plugins.github import GitHubPlugin
from fgap.plugins.github.app_token import AppTokenStore
from fgap.plugins.github.credential import select_credential


@pytest.fixture(scope="module")
def rsa_keys():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


@pytest.fixture
async def mock_github_api(rsa_keys):
    """Mock of POST /app/installations/{id}/access_tokens."""
    _, public_pem = rsa_keys
    received = []
    state = {"expires_in": 3600, "counter": 0}

    async def mint(request):
        auth = request.headers.get("Authorization", "")
        claims = jwt.decode(auth.removeprefix("Bearer "), public_pem,
                            algorithms=["RS256"])
        state["counter"] += 1
        received.append({
            "installation_id": request.match_info["iid"],
            "claims": claims,
            "body": await request.json(),
        })
        expires = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                time.gmtime(time.time() + state["expires_in"]))
        return web.json_response(
            {"token": f"ghs_test_{state['counter']}", "expires_at": expires},
            status=201)

    app = web.Application()
    app.router.add_post("/app/installations/{iid}/access_tokens", mint)
    server = TestServer(app)
    await server.start_server()
    yield {"url": str(server.make_url("")).rstrip("/"),
           "received": received, "state": state}
    await server.close()


def app_cred(rsa_keys, **extra) -> dict:
    return {"app_id": 4219319, "installation_id": 777,
            "private_key": rsa_keys[0], "resources": ["myorg/*"], **extra}


async def test_mint_shape_and_jwt_claims(rsa_keys, mock_github_api):
    store = AppTokenStore()
    token = await store.get_token(app_cred(rsa_keys), "myorg/somerepo",
                                  api_base=mock_github_api["url"])
    assert token == "ghs_test_1"
    (call,) = mock_github_api["received"]
    assert call["installation_id"] == "777"
    assert call["claims"]["iss"] == "4219319"
    assert call["claims"]["exp"] > time.time()
    assert call["body"] == {}  # no narrowing requested


async def test_matched_narrowing_and_permissions(rsa_keys, mock_github_api):
    store = AppTokenStore()
    cred = app_cred(rsa_keys, repositories="matched",
                    permissions={"contents": "write"})
    await store.get_token(cred, "myorg/somerepo",
                          api_base=mock_github_api["url"])
    (call,) = mock_github_api["received"]
    assert call["body"] == {"repositories": ["somerepo"],
                            "permissions": {"contents": "write"}}


async def test_token_cached_until_near_expiry(rsa_keys, mock_github_api):
    store = AppTokenStore()
    cred = app_cred(rsa_keys)
    t1 = await store.get_token(cred, "myorg/a", api_base=mock_github_api["url"])
    t2 = await store.get_token(cred, "myorg/b", api_base=mock_github_api["url"])
    assert t1 == t2 == "ghs_test_1"          # same narrowing shape -> cached
    assert len(mock_github_api["received"]) == 1


async def test_near_expiry_token_is_reminted(rsa_keys, mock_github_api):
    mock_github_api["state"]["expires_in"] = 60  # inside the refresh margin
    store = AppTokenStore()
    cred = app_cred(rsa_keys)
    t1 = await store.get_token(cred, "myorg/a", api_base=mock_github_api["url"])
    t2 = await store.get_token(cred, "myorg/a", api_base=mock_github_api["url"])
    assert (t1, t2) == ("ghs_test_1", "ghs_test_2")
    assert len(mock_github_api["received"]) == 2


async def test_matched_narrowing_caches_per_repo(rsa_keys, mock_github_api):
    store = AppTokenStore()
    cred = app_cred(rsa_keys, repositories="matched")
    await store.get_token(cred, "myorg/a", api_base=mock_github_api["url"])
    await store.get_token(cred, "myorg/b", api_base=mock_github_api["url"])
    assert len(mock_github_api["received"]) == 2  # different narrowing shapes


def test_select_credential_returns_app_shape(rsa_keys):
    config = {"credentials": [app_cred(rsa_keys)]}
    cred = select_credential("myorg/somerepo", config)
    assert cred["resource"] == "myorg/somerepo"
    assert cred["app"]["app_id"] == 4219319
    cred = select_credential("otherorg/nope", config)
    assert cred is None


async def test_plugin_resolves_app_credential_to_env(rsa_keys, mock_github_api):
    plugin = GitHubPlugin()
    config = {"credentials": [app_cred(rsa_keys)],
              "_github_api_base_url": mock_github_api["url"]}
    credential = plugin.select_credential("myorg/somerepo", config)
    env = await plugin.resolve_credential_env(credential, config)
    assert env == {"GH_TOKEN": "ghs_test_1", "GH_HOST": "github.com"}


async def test_plugin_resolves_pat_credential_unchanged(rsa_keys):
    plugin = GitHubPlugin()
    config = {"credentials": [{"token": "github_pat_x", "resources": ["*"]}]}
    credential = plugin.select_credential("any/repo", config)
    env = await plugin.resolve_credential_env(credential, config)
    assert env == {"GH_TOKEN": "github_pat_x", "GH_HOST": "github.com"}


async def test_git_proxy_uses_minted_token(rsa_keys, mock_github_api):
    """End to end through the proxy routes: an App credential mints a
    token and git requests reach GitHub with it — the path that makes
    LFS batch calls (rejected for fine-grained PATs) work."""
    import base64

    from aiohttp.test_utils import TestClient, TestServer as TS

    from fgap.core.router import create_routes

    received = []

    async def handle(request):
        received.append(dict(request.headers))
        return web.Response(body=b"OK")

    upstream = web.Application()
    upstream.router.add_route("*", "/{path:.*}", handle)
    async with TS(upstream) as git_server:
        config = {"plugins": {"github": {
            "credentials": [app_cred(rsa_keys, repositories="matched",
                                     permissions={"contents": "write"})],
            "_github_base_url": str(git_server.make_url("")).rstrip("/"),
            "_github_api_base_url": mock_github_api["url"],
        }}}
        app = create_routes(config, {"github": GitHubPlugin()})
        async with TestClient(TS(app)) as client:
            resp = await client.post(
                "/git/myorg/somerepo.git/info/lfs/objects/batch",
                json={"operation": "upload"})
            assert resp.status == 200
    expected = base64.b64encode(b"x-access-token:ghs_test_1").decode()
    assert received[0]["Authorization"] == f"Basic {expected}"
    assert mock_github_api["received"][0]["body"]["repositories"] == ["somerepo"]
