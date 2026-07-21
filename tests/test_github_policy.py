import pytest
from aiohttp.test_utils import TestClient, TestServer

from fgap.core.router import create_routes
from fgap.plugins.github import GitHubPlugin
from fgap.plugins.github.policy import check_policy


# A dummy credential so the router can select one for the resource; the
# policy check runs before credential resolution, but the router still
# needs a matching credential to proceed past policy in the negative
# assertions we *don't* reach. The token value here must never appear in
# any response body — that is itself a regression guard.
_DUMMY_TOKEN = "ghp_DUMMY_NOT_A_REAL_TOKEN"
CONFIG = {"plugins": {"github": {"credentials": [
    {"token": _DUMMY_TOKEN, "resources": ["owner/*"]},
]}}}


class TestCheckPolicyUnit:
    def test_auth_token_denied(self):
        r = check_policy(["auth", "token"], "owner/repo", {})
        assert r is not None
        assert "leak" in r

    def test_auth_status_show_token_denied(self):
        assert check_policy(
            ["auth", "status", "--show-token"], "owner/repo", {},
        ) is not None

    def test_auth_setup_git_denied(self):
        assert check_policy(
            ["auth", "setup-git"], "owner/repo", {},
        ) is not None

    def test_auth_help_also_denied(self):
        # --help under gh auth is harmless but gh auth * is denied
        # wholesale; this keeps the rule simple and the legit path
        # (fgap-gh auth status -> /auth/status) unaffected.
        assert check_policy(["auth", "--help"], "", {}) is not None

    def test_issue_list_allowed(self):
        assert check_policy(["issue", "list"], "owner/repo", {}) is None

    def test_pr_list_allowed(self):
        assert check_policy(["pr", "list"], "owner/repo", {}) is None

    def test_empty_args_allowed(self):
        assert check_policy([], "owner/repo", {}) is None


@pytest.fixture
async def gh_client():
    app = create_routes(CONFIG, {"github": GitHubPlugin()})
    async with TestClient(TestServer(app)) as client:
        yield client


class TestRouterEnforcement:
    """The deny must hold at the /cli choke point, not just in the unit.

    A caller that bypasses the fgap-gh client and POSTs /cli directly
    must still get 403, and the response must not leak the credential.
    """

    async def test_auth_token_returns_403(self, gh_client):
        resp = await gh_client.post("/cli", json={
            "tool": "gh",
            "args": ["auth", "token"],
            "resource": "owner/repo",
        })
        assert resp.status == 403
        body = await resp.text()
        assert _DUMMY_TOKEN not in body

    async def test_auth_status_show_token_returns_403(self, gh_client):
        resp = await gh_client.post("/cli", json={
            "tool": "gh",
            "args": ["auth", "status", "--show-token"],
            "resource": "owner/repo",
        })
        assert resp.status == 403

    async def test_auth_setup_git_returns_403(self, gh_client):
        resp = await gh_client.post("/cli", json={
            "tool": "gh",
            "args": ["auth", "setup-git"],
            "resource": "owner/repo",
        })
        assert resp.status == 403
        body = await resp.text()
        assert _DUMMY_TOKEN not in body
