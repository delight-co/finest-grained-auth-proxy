import os

import pytest

from fgap.core.executor import execute_cli

# Test-friendly union of every binary spawned in this file. The allowlist
# is required by execute_cli — the tests all share one broad set so each
# case can focus on the behaviour it actually exercises.
_ANY = frozenset({
    "echo", "sh", "printenv", "sleep", "cat", "nonexistent_binary_xyz",
})


class TestExecuteCli:
    async def test_successful_execution(self):
        result = await execute_cli("echo", ["hello", "world"], {}, allowed_binaries=_ANY)
        assert result["exit_code"] == 0
        assert result["stdout"].strip() == "hello world"
        assert result["stderr"] == ""

    async def test_nonzero_exit_code(self):
        result = await execute_cli("sh", ["-c", "exit 42"], {}, allowed_binaries=_ANY)
        assert result["exit_code"] == 42

    async def test_stderr_output(self):
        result = await execute_cli("sh", ["-c", "echo oops >&2"], {}, allowed_binaries=_ANY)
        assert result["exit_code"] == 0
        assert "oops" in result["stderr"]

    async def test_env_injection(self):
        result = await execute_cli(
            "printenv", ["FGAP_TEST_VAR"], {"FGAP_TEST_VAR": "injected_value"},
            allowed_binaries=_ANY,
        )
        assert result["exit_code"] == 0
        assert result["stdout"].strip() == "injected_value"

    async def test_env_does_not_leak_to_parent(self):
        marker = "__FGAP_LEAK_TEST__"
        original = os.environ.get(marker)
        await execute_cli("echo", ["hi"], {marker: "leaked"}, allowed_binaries=_ANY)
        assert os.environ.get(marker) == original

    async def test_timeout_kills_process(self):
        result = await execute_cli("sleep", ["10"], {}, timeout=1, allowed_binaries=_ANY)
        assert result["exit_code"] == -1
        assert "timed out" in result["stderr"]

    async def test_binary_not_found(self):
        result = await execute_cli("nonexistent_binary_xyz", [], {}, allowed_binaries=_ANY)
        assert result["exit_code"] == -1
        assert "not found" in result["stderr"].lower()

    async def test_gh_force_tty_injected(self):
        """GH_FORCE_TTY should be set in subprocess environment."""
        result = await execute_cli("printenv", ["GH_FORCE_TTY"], {}, allowed_binaries=_ANY)
        assert result["exit_code"] == 0
        assert result["stdout"].strip() == "true"

    async def test_no_color_injected(self):
        """NO_COLOR should be set in subprocess environment."""
        result = await execute_cli("printenv", ["NO_COLOR"], {}, allowed_binaries=_ANY)
        assert result["exit_code"] == 0
        assert result["stdout"].strip() == "1"

    async def test_stdin_data(self):
        result = await execute_cli("cat", [], {}, stdin_data="hello stdin", allowed_binaries=_ANY)
        assert result["exit_code"] == 0
        assert "hello stdin" in result["stdout"]


class TestAllowedBinaries:
    async def test_unknown_binary_rejected(self):
        with pytest.raises(ValueError, match="unknown binary"):
            await execute_cli(
                "not_in_allowlist", [], {},
                allowed_binaries=frozenset({"echo", "sh"}),
            )

    async def test_empty_allowlist_rejects_everything(self):
        with pytest.raises(ValueError):
            await execute_cli("echo", [], {}, allowed_binaries=frozenset())
