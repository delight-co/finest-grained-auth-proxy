import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from fgap.client.gh import (
    detect_resource_from_args,
    parse_api_endpoint,
    parse_git_remote_url,
    run,
    strip_repo_flag,
    transform_body_file,
)


# =========================================================================
# Pure logic: resource detection
# =========================================================================


class TestParseGitRemoteUrl:
    def test_ssh(self):
        assert parse_git_remote_url("git@github.com:owner/repo.git") == "owner/repo"

    def test_https(self):
        assert parse_git_remote_url("https://github.com/owner/repo.git") == "owner/repo"

    def test_https_no_dot_git(self):
        assert parse_git_remote_url("https://github.com/owner/repo") == "owner/repo"

    def test_proxy_url(self):
        url = "http://host.docker.internal:8766/git/owner/repo.git"
        assert parse_git_remote_url(url) == "owner/repo"

    def test_unknown_format(self):
        assert parse_git_remote_url("https://gitlab.com/owner/repo") is None


class TestParseApiEndpoint:
    def test_repos_endpoint(self):
        assert parse_api_endpoint("repos/owner/repo/issues") == "owner/repo"

    def test_leading_slash(self):
        assert parse_api_endpoint("/repos/owner/repo/pulls") == "owner/repo"

    def test_bare_repos(self):
        assert parse_api_endpoint("repos/owner/repo") == "owner/repo"

    def test_non_repos(self):
        assert parse_api_endpoint("user/repos") is None

    def test_empty(self):
        assert parse_api_endpoint("") is None


class TestDetectResourceFromArgs:
    def test_dash_r_space(self):
        assert detect_resource_from_args(["-R", "owner/repo"]) == "owner/repo"

    def test_dash_r_attached(self):
        assert detect_resource_from_args(["-Rowner/repo"]) == "owner/repo"

    def test_repo_space(self):
        assert detect_resource_from_args(["--repo", "owner/repo"]) == "owner/repo"

    def test_repo_equals(self):
        assert detect_resource_from_args(["--repo=owner/repo"]) == "owner/repo"

    def test_none(self):
        assert detect_resource_from_args(["issue", "list"]) is None

    def test_mixed_args(self):
        assert detect_resource_from_args(["issue", "list", "-R", "o/r"]) == "o/r"


# =========================================================================
# Pure logic: argument transformation
# =========================================================================


class TestStripRepoFlag:
    def test_dash_r_space(self):
        assert strip_repo_flag(["issue", "list", "-R", "o/r"]) == ["issue", "list"]

    def test_dash_r_attached(self):
        assert strip_repo_flag(["issue", "list", "-Ro/r"]) == ["issue", "list"]

    def test_repo_space(self):
        assert strip_repo_flag(["--repo", "o/r", "issue", "list"]) == ["issue", "list"]

    def test_repo_equals(self):
        assert strip_repo_flag(["issue", "--repo=o/r", "list"]) == ["issue", "list"]

    def test_no_flag(self):
        assert strip_repo_flag(["issue", "list"]) == ["issue", "list"]


class TestTransformBodyFile:
    def test_body_file_flag(self, tmp_path):
        f = tmp_path / "body.md"
        f.write_text("file content")
        result = transform_body_file(["issue", "create", "--body-file", str(f)])
        assert result == ["issue", "create", "--body", "file content"]

    def test_body_file_equals(self, tmp_path):
        f = tmp_path / "body.md"
        f.write_text("content")
        result = transform_body_file(["issue", "create", f"--body-file={f}"])
        assert result == ["issue", "create", "--body", "content"]

    def test_dash_f_space(self, tmp_path):
        f = tmp_path / "body.md"
        f.write_text("content")
        result = transform_body_file(["issue", "create", "-F", str(f)])
        assert result == ["issue", "create", "--body", "content"]

    def test_dash_f_attached(self, tmp_path):
        f = tmp_path / "body.md"
        f.write_text("content")
        result = transform_body_file(["issue", "create", f"-F{f}"])
        assert result == ["issue", "create", "--body", "content"]

    def test_file_not_found(self):
        with pytest.raises(ValueError, match="File not found"):
            transform_body_file(["--body-file", "/nonexistent/file.md"])

    def test_no_body_file(self):
        args = ["issue", "create", "--body", "inline"]
        assert transform_body_file(args) == args


# =========================================================================
# Mock helpers
# =========================================================================


@pytest.fixture
async def mock_proxy():
    """Mock fgap proxy."""
    app = web.Application()
    state = {"responses": [], "requests": [], "auth_status": None}

    async def handle_cli(request):
        data = await request.json()
        state["requests"].append(data)
        if state["responses"]:
            return state["responses"].pop(0)
        return web.json_response({"exit_code": 0, "stdout": "", "stderr": ""})

    async def handle_auth_status(request):
        if state["auth_status"]:
            return state["auth_status"]
        return web.json_response({"plugins": {}})

    app.router.add_post("/cli", handle_cli)
    app.router.add_get("/auth/status", handle_auth_status)
    async with TestServer(app) as server:
        yield server, state


def _url(server) -> str:
    return str(server.make_url(""))


async def _fake_remote(url="https://github.com/owner/repo.git"):
    async def getter(*, _run=None):
        return url
    return getter


async def _fake_branch(name="feat/branch"):
    async def getter(*, _run=None):
        return name
    return getter


def _no_git():
    async def getter(*, _run=None):
        return None
    return getter


# =========================================================================
# run(): help display
# =========================================================================


class TestHelp:
    async def test_no_args(self, capsys):
        code = await run([], "http://unused")
        assert code == 0
        assert "fgap-gh" in capsys.readouterr().out

    async def test_help_flag(self, capsys):
        code = await run(["--help"], "http://unused")
        assert code == 0
        assert "COMMANDS" in capsys.readouterr().out

    async def test_discussion_help(self, capsys):
        code = await run(["discussion"], "http://unused")
        assert code == 0
        assert "Discussions" in capsys.readouterr().out

    async def test_discussion_help_flag(self, capsys):
        code = await run(["discussion", "--help"], "http://unused")
        assert code == 0
        assert "comment edit" in capsys.readouterr().out

    async def test_sub_issue_help(self, capsys):
        code = await run(["sub-issue"], "http://unused")
        assert code == 0
        assert "sub-issues" in capsys.readouterr().out

    async def test_sub_issue_help_flag(self, capsys):
        code = await run(["sub-issue", "-h"], "http://unused")
        assert code == 0
        assert "reorder" in capsys.readouterr().out


# =========================================================================
# run(): api graphql prohibition
# =========================================================================


class TestApiGraphqlProhibition:
    async def test_blocked(self, capsys):
        code = await run(
            ["api", "graphql"],
            "http://unused",
            _get_remote_url=_no_git(),
        )
        assert code == 1
        assert "not supported" in capsys.readouterr().err


# =========================================================================
# run(): resource detection
# =========================================================================


class TestResourceDetection:
    async def test_from_r_flag(self, mock_proxy):
        server, state = mock_proxy
        await run(
            ["issue", "list", "-R", "flag/repo"],
            _url(server),
            _get_remote_url=_no_git(),
        )
        assert state["requests"][0]["resource"] == "flag/repo"

    async def test_from_api_endpoint(self, mock_proxy):
        server, state = mock_proxy
        await run(
            ["api", "repos/api/repo/issues"],
            _url(server),
            _get_remote_url=_no_git(),
        )
        assert state["requests"][0]["resource"] == "api/repo"

    async def test_from_git_remote(self, mock_proxy):
        server, state = mock_proxy
        await run(
            ["issue", "list"],
            _url(server),
            _get_remote_url=await _fake_remote("https://github.com/git/repo.git"),
        )
        assert state["requests"][0]["resource"] == "git/repo"

    async def test_r_flag_takes_priority(self, mock_proxy):
        server, state = mock_proxy
        await run(
            ["issue", "list", "-R", "flag/repo"],
            _url(server),
            _get_remote_url=await _fake_remote("https://github.com/git/repo.git"),
        )
        assert state["requests"][0]["resource"] == "flag/repo"

    async def test_no_resource_fails(self, capsys):
        code = await run(
            ["issue", "list"],
            "http://unused",
            _get_remote_url=_no_git(),
        )
        assert code == 1
        assert "Could not determine" in capsys.readouterr().err


# =========================================================================
# run(): argument transformation
# =========================================================================


class TestArgTransformation:
    async def test_r_flag_stripped(self, mock_proxy):
        server, state = mock_proxy
        await run(
            ["issue", "list", "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
        )
        assert state["requests"][0]["args"] == ["issue", "list"]

    async def test_body_file_transformed(self, mock_proxy, tmp_path):
        f = tmp_path / "body.md"
        f.write_text("hello")
        server, state = mock_proxy
        await run(
            ["issue", "create", "--body-file", str(f), "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
        )
        assert "--body" in state["requests"][0]["args"]
        assert "hello" in state["requests"][0]["args"]

    async def test_body_file_not_found(self, capsys):
        code = await run(
            ["issue", "create", "--body-file", "/nope", "-R", "o/r"],
            "http://unused",
            _get_remote_url=_no_git(),
        )
        assert code == 1
        assert "File not found" in capsys.readouterr().err


# =========================================================================
# run(): pr create --head injection
# =========================================================================


class TestPrCreateHead:
    async def test_injects_head(self, mock_proxy):
        server, state = mock_proxy
        await run(
            ["pr", "create", "--title", "T", "-R", "owner/repo"],
            _url(server),
            _get_remote_url=_no_git(),
            _get_branch=await _fake_branch("my-branch"),
        )
        args = state["requests"][0]["args"]
        assert "--head" in args
        head_idx = args.index("--head")
        assert args[head_idx + 1] == "owner:my-branch"

    async def test_does_not_override_existing_head(self, mock_proxy):
        server, state = mock_proxy
        await run(
            ["pr", "create", "--head", "other:branch", "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
            _get_branch=await _fake_branch("my-branch"),
        )
        args = state["requests"][0]["args"]
        assert args.count("--head") == 1
        head_idx = args.index("--head")
        assert args[head_idx + 1] == "other:branch"

    async def test_no_branch_no_injection(self, mock_proxy):
        server, state = mock_proxy
        await run(
            ["pr", "create", "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
            _get_branch=_no_git(),
        )
        args = state["requests"][0]["args"]
        assert "--head" not in args


# =========================================================================
# run(): proxy call and output
# =========================================================================


class TestProxyCallAndOutput:
    async def test_stdout_printed(self, mock_proxy, capsys):
        server, state = mock_proxy
        state["responses"].append(
            web.json_response({"exit_code": 0, "stdout": "hello", "stderr": ""}),
        )
        code = await run(
            ["issue", "list", "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
        )
        assert code == 0
        assert "hello" in capsys.readouterr().out

    async def test_stderr_printed(self, mock_proxy, capsys):
        server, state = mock_proxy
        state["responses"].append(
            web.json_response({"exit_code": 0, "stdout": "", "stderr": "info msg"}),
        )
        await run(
            ["issue", "list", "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
        )
        assert "info msg" in capsys.readouterr().err

    async def test_nonzero_exit_code(self, mock_proxy, capsys):
        server, state = mock_proxy
        state["responses"].append(
            web.json_response({"exit_code": 1, "stdout": "", "stderr": "error"}),
        )
        code = await run(
            ["issue", "view", "999", "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
        )
        assert code == 1
        assert "error" in capsys.readouterr().err

    async def test_connection_error(self, capsys):
        code = await run(
            ["issue", "list", "-R", "o/r"],
            "http://127.0.0.1:1",
            _get_remote_url=_no_git(),
        )
        assert code == 1
        assert "Cannot connect" in capsys.readouterr().err

    async def test_proxy_html_error(self, mock_proxy, capsys):
        server, state = mock_proxy
        state["responses"].append(
            web.Response(text="<html>Error</html>", content_type="text/html"),
        )
        code = await run(
            ["issue", "list", "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
        )
        assert code == 1
        assert "HTML" in capsys.readouterr().err


# =========================================================================
# run(): full flow
# =========================================================================


class TestFullFlow:
    async def test_discussion_via_proxy(self, mock_proxy):
        server, state = mock_proxy
        state["responses"].append(
            web.json_response({"exit_code": 0, "stdout": "#1\tTest", "stderr": ""}),
        )
        code = await run(
            ["discussion", "list", "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
        )
        assert code == 0
        req = state["requests"][0]
        assert req["args"] == ["discussion", "list"]
        assert req["resource"] == "o/r"

    async def test_sub_issue_via_proxy(self, mock_proxy):
        server, state = mock_proxy
        state["responses"].append(
            web.json_response({"exit_code": 0, "stdout": "10\tOPEN\tChild", "stderr": ""}),
        )
        code = await run(
            ["sub-issue", "list", "5", "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
        )
        assert code == 0
        req = state["requests"][0]
        assert req["args"] == ["sub-issue", "list", "5"]


# =========================================================================
# run(): auth command
# =========================================================================


class TestAuth:
    async def test_auth_help(self, capsys):
        code = await run(["auth"], "http://unused")
        assert code == 0
        assert "auth status" in capsys.readouterr().out

    async def test_auth_help_flag(self, capsys):
        code = await run(["auth", "--help"], "http://unused")
        assert code == 0

    async def test_auth_unknown_subcommand(self, capsys):
        code = await run(["auth", "login"], "http://unused")
        assert code == 1
        assert "Unknown" in capsys.readouterr().err

    async def test_auth_status_valid(self, mock_proxy, capsys):
        server, state = mock_proxy
        state["auth_status"] = web.json_response({"plugins": {"github": [
            {
                "masked_token": "ghp_abc1***",
                "valid": True,
                "user": "testuser",
                "scopes": "repo",
                "rate_limit_remaining": "4999",
                "resources": ["*"],
            },
        ]}})
        code = await run(["auth", "status"], _url(server))
        assert code == 0
        out = capsys.readouterr().out
        assert "ghp_abc1***" in out
        assert "testuser" in out
        assert "repo" in out

    async def test_auth_status_invalid(self, mock_proxy, capsys):
        server, state = mock_proxy
        state["auth_status"] = web.json_response({"plugins": {"github": [
            {
                "masked_token": "ghp_bad_***",
                "valid": False,
                "error": "Bad credentials",
                "resources": ["*"],
            },
        ]}})
        code = await run(["auth", "status"], _url(server))
        assert code == 0
        out = capsys.readouterr().out
        assert "Bad credentials" in out

    async def test_auth_status_no_creds(self, mock_proxy, capsys):
        server, state = mock_proxy
        state["auth_status"] = web.json_response({"plugins": {}})
        code = await run(["auth", "status"], _url(server))
        assert code == 0
        assert "No GitHub" in capsys.readouterr().out

    async def test_auth_status_connection_error(self, capsys):
        code = await run(["auth", "status"], "http://127.0.0.1:1")
        assert code == 1
        assert "Cannot connect" in capsys.readouterr().err
