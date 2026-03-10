import os

from fgap.core.executor import execute_cli


class TestExecuteCli:
    async def test_successful_execution(self):
        result = await execute_cli("echo", ["hello", "world"], {})
        assert result["exit_code"] == 0
        assert result["stdout"].strip() == "hello world"
        assert result["stderr"] == ""

    async def test_nonzero_exit_code(self):
        result = await execute_cli("sh", ["-c", "exit 42"], {})
        assert result["exit_code"] == 42

    async def test_stderr_output(self):
        result = await execute_cli("sh", ["-c", "echo oops >&2"], {})
        assert result["exit_code"] == 0
        assert "oops" in result["stderr"]

    async def test_env_injection(self):
        result = await execute_cli(
            "printenv", ["FGAP_TEST_VAR"], {"FGAP_TEST_VAR": "injected_value"},
        )
        assert result["exit_code"] == 0
        assert result["stdout"].strip() == "injected_value"

    async def test_env_does_not_leak_to_parent(self):
        marker = "__FGAP_LEAK_TEST__"
        original = os.environ.get(marker)
        await execute_cli("echo", ["hi"], {marker: "leaked"})
        assert os.environ.get(marker) == original

    async def test_timeout_kills_process(self):
        result = await execute_cli("sleep", ["10"], {}, timeout=1)
        assert result["exit_code"] == -1
        assert "timed out" in result["stderr"]

    async def test_binary_not_found(self):
        result = await execute_cli("nonexistent_binary_xyz", [], {})
        assert result["exit_code"] == -1
        assert "not found" in result["stderr"].lower()

    async def test_subprocess_sees_tty(self):
        """Subprocess should see stdout/stderr as TTY (via PTY)."""
        result = await execute_cli(
            "python3", ["-c",
                "import sys; "
                "print(f'stdout={sys.stdout.isatty()} stderr={sys.stderr.isatty()}')"
            ], {},
        )
        assert result["exit_code"] == 0
        assert "stdout=True" in result["stdout"]
        assert "stderr=True" in result["stdout"]

    async def test_stdin_data(self):
        result = await execute_cli("cat", [], {}, stdin_data="hello stdin")
        assert result["exit_code"] == 0
        assert "hello stdin" in result["stdout"]

    async def test_crlf_normalized(self):
        """PTY \\r\\n should be normalized to \\n in output."""
        result = await execute_cli("echo", ["line"], {})
        assert "\r\n" not in result["stdout"]
        assert "line\n" in result["stdout"]
