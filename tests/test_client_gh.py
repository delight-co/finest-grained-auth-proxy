import io

import pytest
from aiohttp import web

from fgap.client.gh import (
    detect_repo_positional,
    detect_resource_from_args,
    parse_api_endpoint,
    parse_git_remote_url,
    run,
    strip_repo_flag,
    transform_api_field_files,
    transform_api_input,
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


class TestDetectRepoPositional:
    def test_view_with_positional(self):
        assert detect_repo_positional(["repo", "view", "owner/repo"]) == "owner/repo"

    def test_positional_followed_by_flags(self):
        args = ["repo", "view", "owner/repo", "--json", "visibility"]
        assert detect_repo_positional(args) == "owner/repo"

    def test_clone_with_positional(self):
        assert detect_repo_positional(["repo", "clone", "owner/repo"]) == "owner/repo"

    def test_no_positional(self):
        assert detect_repo_positional(["repo", "view"]) is None

    def test_flag_before_positional_not_detected(self):
        """Only the argument right after the subcommand counts."""
        assert detect_repo_positional(["repo", "view", "--json", "visibility"]) is None

    def test_flag_value_not_mistaken_for_repo(self):
        """A branch name like feat/x must not be picked up as a repository."""
        assert detect_repo_positional(["repo", "view", "--branch", "feat/x"]) is None

    def test_non_repo_command(self):
        assert detect_repo_positional(["issue", "view", "owner/repo"]) is None

    def test_not_owner_repo_shape(self):
        assert detect_repo_positional(["repo", "create", "bare-name"]) is None

    def test_https_url(self):
        args = ["repo", "view", "https://github.com/owner/repo"]
        assert detect_repo_positional(args) == "owner/repo"

    def test_https_url_dot_git(self):
        args = ["repo", "view", "https://github.com/owner/repo.git"]
        assert detect_repo_positional(args) == "owner/repo"

    def test_ssh_url(self):
        args = ["repo", "clone", "git@github.com:owner/repo.git"]
        assert detect_repo_positional(args) == "owner/repo"

    def test_host_owner_repo(self):
        args = ["repo", "view", "github.com/owner/repo"]
        assert detect_repo_positional(args) == "owner/repo"

    def test_non_github_url(self):
        args = ["repo", "view", "https://gitlab.com/owner/repo"]
        assert detect_repo_positional(args) is None


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

    def test_dash_f_passed_through(self):
        """-F is --field (not --body-file) and must pass through to proxy-side gh."""
        args = ["api", "repos/o/r/issues", "-X", "POST", "-F", "in_reply_to=123"]
        assert transform_body_file(args) == args

    def test_dash_f_attached_passed_through(self):
        """-Fkey=value must also pass through."""
        args = ["api", "repos/o/r/issues", "-X", "POST", "-Fin_reply_to=123"]
        assert transform_body_file(args) == args

    def test_dash_f_stdin_reads_body(self):
        """-F - reads stdin and converts to --body."""
        args = ["pr", "comment", "123", "-F", "-"]
        fake_stdin = io.StringIO("hello from stdin")
        result = transform_body_file(args, _stdin=fake_stdin)
        assert result == ["pr", "comment", "123", "--body", "hello from stdin"]

    def test_body_file_stdin_reads_body(self):
        """--body-file - reads stdin and converts to --body."""
        args = ["pr", "create", "--body-file", "-"]
        fake_stdin = io.StringIO("stdin content")
        result = transform_body_file(args, _stdin=fake_stdin)
        assert result == ["pr", "create", "--body", "stdin content"]

    def test_dash_f_file_reads_body(self, tmp_path):
        """-F <path> in non-api context reads file and converts to --body."""
        f = tmp_path / "body.md"
        f.write_text("file content")
        result = transform_body_file(["issue", "create", "-F", str(f)])
        assert result == ["issue", "create", "--body", "file content"]

    def test_dash_f_file_in_api_passed_through(self, tmp_path):
        """-F <path> in api context is --field, not --body-file."""
        f = tmp_path / "body.md"
        f.write_text("content")
        args = ["api", "repos/o/r/issues", "-F", str(f)]
        assert transform_body_file(args) == args

    def test_file_not_found(self):
        with pytest.raises(ValueError, match="File not found"):
            transform_body_file(["--body-file", "/nonexistent/file.md"])

    def test_no_body_file(self):
        args = ["issue", "create", "--body", "inline"]
        assert transform_body_file(args) == args


class TestTransformApiInput:
    def test_input_file(self, tmp_path):
        f = tmp_path / "payload.json"
        f.write_text('{"body":"test"}')
        args, stdin_data = transform_api_input(["api", "repos/o/r/issues", "--input", str(f)])
        assert args == ["api", "repos/o/r/issues", "--input", "-"]
        assert stdin_data == '{"body":"test"}'

    def test_input_equals_file(self, tmp_path):
        f = tmp_path / "payload.json"
        f.write_text('{"body":"test"}')
        args, stdin_data = transform_api_input(["api", "repos/o/r/issues", f"--input={f}"])
        assert args == ["api", "repos/o/r/issues", "--input=-"]
        assert stdin_data == '{"body":"test"}'

    def test_input_stdin(self):
        fake_stdin = io.StringIO('{"body":"hello"}')
        args, stdin_data = transform_api_input(["api", "repos/o/r/issues", "--input", "-"], _stdin=fake_stdin)
        assert args == ["api", "repos/o/r/issues", "--input", "-"]
        assert stdin_data == '{"body":"hello"}'

    def test_no_input_returns_none(self):
        args, stdin_data = transform_api_input(["api", "repos/o/r/issues", "-X", "POST"])
        assert args == ["api", "repos/o/r/issues", "-X", "POST"]
        assert stdin_data is None

    def test_non_api_skipped(self, tmp_path):
        f = tmp_path / "payload.json"
        f.write_text("content")
        original = ["issue", "create", "--input", str(f)]
        args, stdin_data = transform_api_input(original)
        assert args == original
        assert stdin_data is None

    def test_input_file_not_found(self):
        with pytest.raises(ValueError, match="File not found"):
            transform_api_input(["api", "repos/o/r/issues", "--input", "/nonexistent"])


# =========================================================================
# Mock helpers
# =========================================================================


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

    async def test_issue_help_without_repo(self, mock_proxy):
        server, state = mock_proxy
        code = await run(
            ["issue", "--help"],
            _url(server),
            _get_remote_url=_no_git(),
        )
        assert code == 0
        assert state["requests"][0]["resource"] == ""

    async def test_pr_help_without_repo(self, mock_proxy):
        server, state = mock_proxy
        code = await run(
            ["pr", "--help"],
            _url(server),
            _get_remote_url=_no_git(),
        )
        assert code == 0
        assert state["requests"][0]["resource"] == ""

    async def test_api_help_without_repo(self, mock_proxy):
        server, state = mock_proxy
        code = await run(
            ["api", "--help"],
            _url(server),
            _get_remote_url=_no_git(),
        )
        assert code == 0
        assert state["requests"][0]["resource"] == ""

    async def test_issue_edit_help_without_repo(self, mock_proxy):
        server, state = mock_proxy
        code = await run(
            ["issue", "edit", "--help"],
            _url(server),
            _get_remote_url=_no_git(),
        )
        assert code == 0

    async def test_no_resource_without_help_still_fails(self, capsys):
        code = await run(
            ["issue", "list"],
            "http://unused",
            _get_remote_url=_no_git(),
        )
        assert code == 1
        assert "Could not determine" in capsys.readouterr().err


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
# run(): repo view
# =========================================================================


class TestRepoView:
    async def test_positional_used_as_resource(self, mock_proxy):
        """repo view owner/repo works outside any git checkout."""
        server, state = mock_proxy
        code = await run(
            ["repo", "view", "cli/cli"],
            _url(server),
            _get_remote_url=_no_git(),
        )
        assert code == 0
        req = state["requests"][0]
        assert req["resource"] == "cli/cli"
        assert req["args"] == ["repo", "view", "cli/cli"]

    async def test_no_positional_injects_remote_resource(self, mock_proxy):
        """Bare repo view inside a checkout targets the remote's repo."""
        server, state = mock_proxy
        code = await run(
            ["repo", "view"],
            _url(server),
            _get_remote_url=await _fake_remote("https://github.com/owner/repo.git"),
        )
        assert code == 0
        req = state["requests"][0]
        assert req["resource"] == "owner/repo"
        assert req["args"] == ["repo", "view", "owner/repo"]

    async def test_r_flag_converted_to_positional(self, mock_proxy):
        server, state = mock_proxy
        code = await run(
            ["repo", "view", "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
        )
        assert code == 0
        req = state["requests"][0]
        assert req["resource"] == "o/r"
        assert req["args"] == ["repo", "view", "o/r"]

    async def test_flags_preserved_after_injection(self, mock_proxy):
        server, state = mock_proxy
        code = await run(
            ["repo", "view", "--json", "visibility"],
            _url(server),
            _get_remote_url=await _fake_remote("https://github.com/owner/repo.git"),
        )
        assert code == 0
        args = state["requests"][0]["args"]
        assert args == ["repo", "view", "owner/repo", "--json", "visibility"]

    async def test_positional_not_duplicated(self, mock_proxy):
        server, state = mock_proxy
        await run(
            ["repo", "view", "cli/cli", "--json", "visibility"],
            _url(server),
            _get_remote_url=_no_git(),
        )
        args = state["requests"][0]["args"]
        assert args.count("cli/cli") == 1

    async def test_url_positional_used_as_resource(self, mock_proxy):
        """URL-form positionals select the credential for the named repo;
        the URL itself passes through to gh, which accepts it upstream."""
        server, state = mock_proxy
        code = await run(
            ["repo", "view", "https://github.com/cli/cli"],
            _url(server),
            _get_remote_url=_no_git(),
        )
        assert code == 0
        req = state["requests"][0]
        assert req["resource"] == "cli/cli"
        assert req["args"] == ["repo", "view", "https://github.com/cli/cli"]


# =========================================================================
# transform_api_field_files
# =========================================================================


class TestTransformApiFieldFiles:
    def test_expands_dash_f_at_file(self, tmp_path):
        f = tmp_path / "body.md"
        f.write_text("hello world")
        result = transform_api_field_files(
            ["api", "/repos/o/r/pulls/1", "-f", f"body=@{f}"],
        )
        assert result == ["api", "/repos/o/r/pulls/1", "-f", "body=hello world"]

    def test_expands_dash_F_at_file(self, tmp_path):
        f = tmp_path / "payload.json"
        f.write_text('{"key": "value"}')
        result = transform_api_field_files(
            ["api", "/x", "-F", f"data=@{f}"],
        )
        assert result == ["api", "/x", "-F", 'data={"key": "value"}']

    def test_expands_field_glued_form(self, tmp_path):
        f = tmp_path / "b.md"
        f.write_text("contents")
        result = transform_api_field_files(
            ["api", "/x", f"--field=body=@{f}"],
        )
        assert result == ["api", "/x", "--field=body=contents"]

    def test_expands_raw_field(self, tmp_path):
        f = tmp_path / "b.md"
        f.write_text("x")
        result = transform_api_field_files(
            ["api", "/x", "--raw-field", f"body=@{f}"],
        )
        assert result == ["api", "/x", "--raw-field", "body=x"]

    def test_plain_value_untouched(self):
        result = transform_api_field_files(
            ["api", "/x", "-f", "body=hello"],
        )
        assert result == ["api", "/x", "-f", "body=hello"]

    def test_at_stdin_untouched(self):
        result = transform_api_field_files(
            ["api", "/x", "-f", "body=@-"],
        )
        assert result == ["api", "/x", "-f", "body=@-"]

    def test_non_api_untouched(self, tmp_path):
        f = tmp_path / "b.md"
        f.write_text("contents")
        result = transform_api_field_files(
            ["issue", "create", "-f", f"body=@{f}"],
        )
        # -f on non-api subcommands is not this flag; leave the arg alone.
        assert result == ["issue", "create", "-f", f"body=@{f}"]

    def test_missing_file_raises(self):
        with pytest.raises(ValueError, match="File not found"):
            transform_api_field_files(
                ["api", "/x", "-f", "body=@/nonexistent/path.md"],
            )

    def test_bare_at_without_key_untouched(self):
        # Malformed: no '=', so no key/value pair — leave it as-is.
        result = transform_api_field_files(
            ["api", "/x", "-f", "@file.md"],
        )
        assert result == ["api", "/x", "-f", "@file.md"]

    def test_preserves_other_flags(self, tmp_path):
        f = tmp_path / "b.md"
        f.write_text("x")
        result = transform_api_field_files(
            ["api", "/x", "-X", "PATCH", "-H", "H: v", "-f", f"body=@{f}"],
        )
        assert result == ["api", "/x", "-X", "PATCH", "-H", "H: v", "-f", "body=x"]


# =========================================================================
# run(): issue close --duplicate-of
# =========================================================================


class TestIssueCloseAsDuplicate:
    async def test_happy_path_number(self, mock_proxy):
        server, state = mock_proxy
        # Canonical issue lookup returns id=999.
        state["responses"].append(web.json_response({
            "exit_code": 0, "stdout": '{"id": 999}', "stderr": "",
        }))
        code = await run(
            ["issue", "close", "108", "--duplicate-of", "117", "-R", "o/r"],
            _url(server),
        )
        assert code == 0
        # Two /cli requests: the ID lookup, then the PATCH close.
        assert len(state["requests"]) == 2
        lookup, patch = state["requests"]
        assert lookup["args"] == ["api", "/repos/o/r/issues/117"]
        assert patch["args"] == [
            "api", "-X", "PATCH", "/repos/o/r/issues/108",
            "-f", "state=closed", "-f", "state_reason=duplicate",
            "-F", "duplicate_issue_id=999",
        ]

    async def test_duplicate_of_with_hash_prefix(self, mock_proxy):
        server, state = mock_proxy
        state["responses"].append(web.json_response({
            "exit_code": 0, "stdout": '{"id": 999}', "stderr": "",
        }))
        code = await run(
            ["issue", "close", "108", "--duplicate-of", "#117", "-R", "o/r"],
            _url(server),
        )
        assert code == 0
        assert state["requests"][0]["args"] == ["api", "/repos/o/r/issues/117"]

    async def test_reason_duplicate_alone_errors(self, mock_proxy, capsys):
        server, state = mock_proxy
        code = await run(
            ["issue", "close", "108", "--reason", "duplicate", "-R", "o/r"],
            _url(server),
        )
        assert code == 1
        assert "--reason duplicate requires --duplicate-of" in (
            capsys.readouterr().err
        )
        assert state["requests"] == []

    async def test_conflicting_reason_errors(self, mock_proxy, capsys):
        server, state = mock_proxy
        code = await run(
            [
                "issue", "close", "108",
                "--reason", "completed", "--duplicate-of", "117",
                "-R", "o/r",
            ],
            _url(server),
        )
        assert code == 1
        assert "incompatible with --duplicate-of" in capsys.readouterr().err
        assert state["requests"] == []

    async def test_cross_repo_duplicate_rejected_for_now(
        self, mock_proxy, capsys,
    ):
        server, state = mock_proxy
        code = await run(
            [
                "issue", "close", "108",
                "--duplicate-of", "anthropics/claude-code#42",
                "-R", "o/r",
            ],
            _url(server),
        )
        assert code == 1
        assert "cross-repo duplicates are not supported" in (
            capsys.readouterr().err
        )
        assert state["requests"] == []

    async def test_completed_reason_falls_through_to_stock(self, mock_proxy):
        """--reason completed is stock gh territory; our handler stays out."""
        server, state = mock_proxy
        code = await run(
            [
                "issue", "close", "108", "--reason", "completed",
                "-R", "o/r",
            ],
            _url(server),
        )
        assert code == 0
        # Exactly one /cli request — the passthrough gh issue close.
        assert len(state["requests"]) == 1
        assert state["requests"][0]["args"] == [
            "issue", "close", "108", "--reason", "completed",
        ]

    async def test_comment_posted_before_close(self, mock_proxy):
        server, state = mock_proxy
        state["responses"].append(web.json_response({
            "exit_code": 0, "stdout": '{"id": 999}', "stderr": "",
        }))
        code = await run(
            [
                "issue", "close", "108",
                "--duplicate-of", "117",
                "-c", "sup #117",
                "-R", "o/r",
            ],
            _url(server),
        )
        assert code == 0
        assert len(state["requests"]) == 3
        # ID lookup → issue comment → PATCH close
        assert state["requests"][0]["args"][0:2] == ["api", "/repos/o/r/issues/117"]
        assert state["requests"][1]["args"] == [
            "issue", "comment", "108", "-b", "sup #117",
        ]
        assert state["requests"][2]["args"][:4] == [
            "api", "-X", "PATCH", "/repos/o/r/issues/108",
        ]


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

    async def test_no_branch_fails_with_clear_error(self, mock_proxy, capsys):
        """Outside a git checkout, fail client-side instead of sending a
        head-less pr create that dies server-side with a misleading error."""
        server, state = mock_proxy
        code = await run(
            ["pr", "create", "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
            _get_branch=_no_git(),
        )
        assert code == 1
        err = capsys.readouterr().err
        assert "could not determine the head branch" in err
        assert "--head" in err
        assert state["requests"] == []

    async def test_explicit_head_outside_checkout_reaches_proxy(self, mock_proxy):
        """With --head given, pr create works outside any git checkout."""
        server, state = mock_proxy
        code = await run(
            ["pr", "create", "--head", "o:branch", "--title", "T", "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
            _get_branch=_no_git(),
        )
        assert code == 0
        assert len(state["requests"]) == 1

    async def test_help_outside_checkout_not_blocked(self, mock_proxy):
        """pr create --help must not be rejected by the head check."""
        server, state = mock_proxy
        code = await run(
            ["pr", "create", "--help"],
            _url(server),
            _get_remote_url=_no_git(),
            _get_branch=_no_git(),
        )
        assert code == 0
        assert len(state["requests"]) == 1


# =========================================================================
# run(): pr <subcmd> branch inference
# =========================================================================


class TestPrBranchInference:
    async def test_pr_merge_no_selector_injects_branch(self, mock_proxy):
        """pr merge without a positional selector: inject current branch."""
        server, state = mock_proxy
        await run(
            ["pr", "merge", "--squash", "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
            _get_branch=await _fake_branch("my-feat"),
        )
        args = state["requests"][0]["args"]
        # Selector appears right after the subcommand name.
        assert args[:3] == ["pr", "merge", "my-feat"]

    async def test_pr_view_with_number_leaves_args_alone(self, mock_proxy):
        server, state = mock_proxy
        await run(
            ["pr", "view", "42", "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
            _get_branch=await _fake_branch("my-feat"),
        )
        args = state["requests"][0]["args"]
        assert args[:3] == ["pr", "view", "42"]
        assert "my-feat" not in args

    async def test_pr_view_with_url_leaves_args_alone(self, mock_proxy):
        server, state = mock_proxy
        await run(
            ["pr", "view", "https://github.com/o/r/pull/9", "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
            _get_branch=await _fake_branch("my-feat"),
        )
        args = state["requests"][0]["args"]
        assert args[2] == "https://github.com/o/r/pull/9"

    async def test_pr_edit_flag_value_not_treated_as_selector(
        self, mock_proxy,
    ):
        """--add-label bug is a flag+value pair, not a positional selector."""
        server, state = mock_proxy
        await run(
            ["pr", "edit", "--add-label", "bug", "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
            _get_branch=await _fake_branch("my-feat"),
        )
        args = state["requests"][0]["args"]
        assert args[:3] == ["pr", "edit", "my-feat"]
        # value token survived intact
        label_idx = args.index("--add-label")
        assert args[label_idx + 1] == "bug"

    async def test_pr_status_not_affected(self, mock_proxy):
        """pr status has no selector and stays out of scope."""
        server, state = mock_proxy
        await run(
            ["pr", "status", "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
            _get_branch=await _fake_branch("my-feat"),
        )
        args = state["requests"][0]["args"]
        assert "my-feat" not in args

    async def test_no_branch_fails_with_clear_error(self, mock_proxy, capsys):
        server, state = mock_proxy
        code = await run(
            ["pr", "merge", "--squash", "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
            _get_branch=_no_git(),
        )
        assert code == 1
        err = capsys.readouterr().err
        assert "could not determine the PR selector" in err
        assert state["requests"] == []

    async def test_help_not_blocked(self, mock_proxy):
        server, state = mock_proxy
        code = await run(
            ["pr", "merge", "--help"],
            _url(server),
            _get_remote_url=_no_git(),
            _get_branch=_no_git(),
        )
        assert code == 0
        assert len(state["requests"]) == 1


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

    async def test_stderr_only_printed_to_stdout(self, mock_proxy, capsys):
        """When stdout is empty, stderr goes to stdout so callers see it."""
        server, state = mock_proxy
        state["responses"].append(
            web.json_response({"exit_code": 0, "stdout": "", "stderr": "✓ Merged"}),
        )
        await run(
            ["pr", "merge", "1", "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
        )
        captured = capsys.readouterr()
        assert "✓ Merged" in captured.out
        assert captured.err == ""

    async def test_stderr_with_stdout_stays_on_stderr(self, mock_proxy, capsys):
        """When stdout has data, stderr stays on stderr."""
        server, state = mock_proxy
        state["responses"].append(
            web.json_response({"exit_code": 0, "stdout": "data", "stderr": "info msg"}),
        )
        await run(
            ["issue", "list", "-R", "o/r"],
            _url(server),
            _get_remote_url=_no_git(),
        )
        captured = capsys.readouterr()
        assert "data" in captured.out
        assert "info msg" in captured.err

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


