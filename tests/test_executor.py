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
