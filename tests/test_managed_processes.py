"""Tests for managed local processes (fgap.core.processes)."""

import asyncio
import sys

import pytest

from fgap.core.masking import collect_secrets
from fgap.core.processes import (
    ManagedProcess,
    ProcessSupervisor,
    validate_config,
)

PY = sys.executable

SLEEP_FOREVER = [PY, "-c", "import time; time.sleep(30)"]
EXIT_NOW = [PY, "-c", "pass"]
MISSING_BINARY = ["/nonexistent-fgap-test-binary"]


async def _wait_until(predicate, timeout=5.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while not predicate():
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


class TestValidation:
    def test_command_must_be_nonempty_string_list(self):
        with pytest.raises(ValueError):
            validate_config({"p": {"command": []}})
        with pytest.raises(ValueError):
            validate_config({"p": {"command": "not-a-list"}})
        with pytest.raises(ValueError):
            validate_config({"p": {"command": [1, 2]}})

    def test_env_must_be_string_map(self):
        with pytest.raises(ValueError):
            validate_config({"p": {"command": ["x"], "env": {"A": 1}}})
        with pytest.raises(ValueError):
            validate_config({"p": {"command": ["x"], "env": "nope"}})

    def test_valid_config_passes(self):
        validate_config({"p": {"command": ["x"], "env": {"A": "b"}}})


class TestLifecycle:
    async def test_spawn_and_stop(self):
        proc = ManagedProcess("t", {"command": SLEEP_FOREVER})
        await proc.start()
        try:
            assert proc.running
            assert proc.status()["pid"] is not None
        finally:
            await proc.stop()
        assert not proc.running

    async def test_env_delivered_to_child(self, tmp_path):
        out = tmp_path / "envout"
        code = (
            "import os, pathlib; "
            f"pathlib.Path({str(out)!r})"
            ".write_text(os.environ['FGAP_TEST_KEY'])"
        )
        proc = ManagedProcess("t", {
            "command": [PY, "-c", code],
            "env": {"FGAP_TEST_KEY": "sekrit-value"},
            "restart": False,
        })
        await proc.start()
        try:
            await _wait_until(lambda: out.exists() and out.read_text())
            assert out.read_text() == "sekrit-value"
        finally:
            await proc.stop()

    async def test_restart_on_crash_with_backoff(self):
        proc = ManagedProcess("t", {
            "command": EXIT_NOW,
            "backoff_initial": 0.02,
            "backoff_max": 0.05,
        })
        await proc.start()
        try:
            await _wait_until(lambda: proc.restarts >= 2)
        finally:
            await proc.stop()

    async def test_no_restart_when_disabled(self):
        proc = ManagedProcess("t", {"command": EXIT_NOW, "restart": False})
        await proc.start()
        try:
            await _wait_until(lambda: not proc.running)
            await asyncio.sleep(0.1)
            assert proc.restarts == 0
            assert not proc.running
        finally:
            await proc.stop()

    async def test_spawn_failure_raises(self):
        proc = ManagedProcess("t", {"command": MISSING_BINARY})
        with pytest.raises(OSError):
            await proc.start()


class TestSupervisor:
    async def test_start_all_rolls_back_on_failure(self):
        supervisor = ProcessSupervisor({
            "ok": {"command": SLEEP_FOREVER},
            "bad": {"command": MISSING_BINARY},
        })
        with pytest.raises(OSError):
            await supervisor.start_all()
        assert all(not s["running"] for s in supervisor.status())

    async def test_status_shape(self):
        supervisor = ProcessSupervisor({
            "one": {"command": SLEEP_FOREVER},
        })
        await supervisor.start_all()
        try:
            (status,) = supervisor.status()
            assert status["name"] == "one"
            assert status["running"] is True
            assert status["restarts"] == 0
        finally:
            await supervisor.stop_all()

    def test_invalid_config_rejected_at_construction(self):
        with pytest.raises(ValueError):
            ProcessSupervisor({"p": {"command": []}})


class TestEnvSecretCollection:
    def test_secretish_env_values_are_masked(self):
        config = {"managed_processes": {"m": {
            "command": ["x"],
            "env": {
                "SOME_API_KEY": "supersecretvalue",
                "AUTH_TOKEN": "another-secret",
                "PORT": "9101",
                "SHORT_KEY": "abc",
            },
        }}}
        secrets = collect_secrets(config)
        assert "supersecretvalue" in secrets
        assert "another-secret" in secrets
        # Non-credential names and short values must never be masked —
        # masking "9101" would strike port numbers out of every log line.
        assert "9101" not in secrets
        assert "abc" not in secrets
