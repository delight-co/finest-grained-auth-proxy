import json

import pytest
from aiohttp import web

from fgap.client.gh import _parse_pr_checkout_args, run


def _url(server) -> str:
    return str(server.make_url(""))


async def _fake_remote(url="https://github.com/owner/repo.git"):
    async def getter(*, _run=None):
        return url
    return getter


def _no_git():
    async def getter(*, _run=None):
        return None
    return getter


# =========================================================================
# Pure logic: pr checkout arg parsing
# =========================================================================


class TestParsePrCheckoutArgs:
    def test_number_only(self):
        assert _parse_pr_checkout_args(["51"]) == ("51", None)

    def test_branch_flag(self):
        assert _parse_pr_checkout_args(["51", "-b", "my-branch"]) == ("51", "my-branch")

    def test_branch_attached(self):
        assert _parse_pr_checkout_args(["51", "-bmy-branch"]) == ("51", "my-branch")

    def test_branch_long(self):
        assert _parse_pr_checkout_args(["51", "--branch", "my-branch"]) == ("51", "my-branch")

    def test_branch_equals(self):
        assert _parse_pr_checkout_args(["51", "--branch=my-branch"]) == ("51", "my-branch")

    def test_no_args(self):
        assert _parse_pr_checkout_args([]) == (None, None)

    def test_only_flags(self):
        assert _parse_pr_checkout_args(["-b", "x"]) == (None, "x")


# =========================================================================
# run(): pr checkout (client-side)
# =========================================================================


def _pr_view_response(head_branch: str):
    return web.json_response({
        "exit_code": 0,
        "stdout": json.dumps({"headRefName": head_branch}),
        "stderr": "",
    })


def _make_fake_git(calls: list, overrides: dict | None = None):
    """Create a fake git executor that records calls.

    ``overrides`` maps arg tuples to ``(rc, stdout, stderr)`` responses.
    Default response is ``(0, "", "")``.
    """
    overrides = overrides or {}

    async def fake_git(*args):
        calls.append(args)
        return overrides.get(args, (0, "", ""))

    return fake_git


class TestPrCheckout:
    async def test_basic(self, mock_proxy):
        server, state = mock_proxy
        state["responses"].append(_pr_view_response("feat/branch"))
        git_calls = []
        code = await run(
            ["pr", "checkout", "51", "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
            _git=_make_fake_git(git_calls),
        )
        assert code == 0
        req = state["requests"][0]
        assert req["args"] == ["pr", "view", "51", "--json", "headRefName"]
        assert req["resource"] == "o/r"
        assert ("fetch", "origin", "feat/branch") in git_calls
        assert ("checkout", "feat/branch") in git_calls

    async def test_custom_branch(self, mock_proxy):
        server, state = mock_proxy
        state["responses"].append(_pr_view_response("feat/branch"))
        git_calls = []
        code = await run(
            ["pr", "checkout", "51", "-b", "custom", "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
            _git=_make_fake_git(git_calls),
        )
        assert code == 0
        assert ("checkout", "custom") in git_calls

    async def test_creates_branch_when_not_exists(self, mock_proxy):
        server, state = mock_proxy
        state["responses"].append(_pr_view_response("feat/new"))
        git_calls = []
        code = await run(
            ["pr", "checkout", "51", "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
            _git=_make_fake_git(git_calls, overrides={
                ("checkout", "feat/new"): (1, "", "pathspec did not match"),
            }),
        )
        assert code == 0
        assert ("checkout", "-b", "feat/new", "origin/feat/new") in git_calls

    async def test_co_alias(self, mock_proxy):
        server, state = mock_proxy
        state["responses"].append(_pr_view_response("feat/branch"))
        git_calls = []
        code = await run(
            ["co", "51", "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
            _git=_make_fake_git(git_calls),
        )
        assert code == 0
        assert ("fetch", "origin", "feat/branch") in git_calls

    async def test_no_number(self, capsys):
        code = await run(
            ["pr", "checkout", "-R", "o/r"],
            "http://unused",
            _get_remote_url=_no_git(),
        )
        assert code == 1
        assert "PR number required" in capsys.readouterr().err

    async def test_fetch_failure(self, mock_proxy, capsys):
        server, state = mock_proxy
        state["responses"].append(_pr_view_response("feat/branch"))
        code = await run(
            ["pr", "checkout", "51", "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
            _git=_make_fake_git([], overrides={
                ("fetch", "origin", "feat/branch"): (1, "", "couldn't find remote ref"),
            }),
        )
        assert code == 1
        assert "git fetch failed" in capsys.readouterr().err

    async def test_checkout_failure(self, mock_proxy, capsys):
        server, state = mock_proxy
        state["responses"].append(_pr_view_response("feat/branch"))
        code = await run(
            ["pr", "checkout", "51", "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
            _git=_make_fake_git([], overrides={
                ("checkout", "feat/branch"): (1, "", "error"),
                ("checkout", "-b", "feat/branch", "origin/feat/branch"): (1, "", "fatal"),
            }),
        )
        assert code == 1
        assert "git checkout failed" in capsys.readouterr().err

    async def test_pr_view_failure(self, mock_proxy, capsys):
        server, state = mock_proxy
        state["responses"].append(web.json_response({
            "exit_code": 1,
            "stdout": "",
            "stderr": "Could not resolve to a PullRequest",
        }))
        code = await run(
            ["pr", "checkout", "999", "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
        )
        assert code == 1
        assert "PullRequest" in capsys.readouterr().err

    async def test_help_falls_through(self, mock_proxy):
        """--help should fall through to proxy, not be intercepted."""
        server, state = mock_proxy
        state["responses"].append(web.json_response({
            "exit_code": 0,
            "stdout": "Usage: gh pr checkout",
            "stderr": "",
        }))
        code = await run(
            ["pr", "checkout", "--help", "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
        )
        assert code == 0
        # Should have gone to proxy, not client-side handler
        assert state["requests"][0]["args"] == ["pr", "checkout", "--help"]

    async def test_resource_from_git_remote(self, mock_proxy):
        server, state = mock_proxy
        state["responses"].append(_pr_view_response("feat/branch"))
        git_calls = []
        code = await run(
            ["pr", "checkout", "51"],
            _url(server),
            _get_remote_url=await _fake_remote(
                "http://host.docker.internal:8766/git/owner/repo.git"
            ),
            _git=_make_fake_git(git_calls),
        )
        assert code == 0
        assert state["requests"][0]["resource"] == "owner/repo"
