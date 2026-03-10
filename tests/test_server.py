import json
import os
import signal
import subprocess
import sys
import time

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MAIN = os.path.join(_REPO, "main.py")

_MINIMAL_CONFIG = {
    "port": 0,
    "plugins": {},
}


@pytest.fixture
def config_file(tmp_path):
    """Create a minimal config file with correct permissions."""
    path = str(tmp_path / "config.json5")
    with open(path, "w") as f:
        json.dump(_MINIMAL_CONFIG, f)
    os.chmod(path, 0o600)
    return path


def _server_cmd(config_path, *extra_args):
    return [
        "uv", "run", "python", _MAIN,
        "--config", config_path,
        *extra_args,
    ]


class TestServerCli:
    def test_daemon_requires_logfile(self, config_file):
        result = subprocess.run(
            _server_cmd(config_file, "--daemon"),
            capture_output=True, text=True,
        )
        assert result.returncode == 1
        assert "--logfile is required" in result.stderr

    def test_pidfile_written(self, config_file, tmp_path):
        pidfile = str(tmp_path / "fgap.pid")
        logfile = str(tmp_path / "fgap.log")
        proc = subprocess.Popen(
            _server_cmd(config_file, "--pidfile", pidfile, "--logfile", logfile),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(30):
                if os.path.exists(pidfile):
                    break
                time.sleep(0.1)
            assert os.path.exists(pidfile)
            pid = int(open(pidfile).read().strip())
            os.kill(pid, 0)  # PID is valid
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_logfile_written(self, config_file, tmp_path):
        logfile = str(tmp_path / "fgap.log")
        proc = subprocess.Popen(
            _server_cmd(config_file, "--logfile", logfile),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(30):
                if os.path.exists(logfile) and os.path.getsize(logfile) > 0:
                    break
                time.sleep(0.1)
            assert os.path.exists(logfile)
            content = open(logfile).read()
            assert "Starting fgap" in content
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_daemon_runs_in_background(self, config_file, tmp_path):
        pidfile = str(tmp_path / "fgap.pid")
        logfile = str(tmp_path / "fgap.log")
        result = subprocess.run(
            _server_cmd(config_file, "--daemon", "--pidfile", pidfile, "--logfile", logfile),
            timeout=5,
        )
        # Parent exits immediately
        assert result.returncode == 0
        for _ in range(30):
            if os.path.exists(pidfile):
                break
            time.sleep(0.1)
        assert os.path.exists(pidfile)
        pid = int(open(pidfile).read().strip())
        try:
            os.kill(pid, 0)  # Process is running
        finally:
            os.kill(pid, signal.SIGTERM)
