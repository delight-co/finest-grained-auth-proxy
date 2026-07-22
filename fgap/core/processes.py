"""Managed local processes supervised by the proxy server.

Some upstreams the proxy fronts are not remote SaaS but *local helper
processes* — typically stdio MCP servers wrapped by a stdio-to-HTTP
bridge — that need credentials in their environment. Declaring them in
config keeps those credentials on the proxy host: the server spawns
the processes on startup, restarts them with backoff when they die,
and terminates them (whole process group) on shutdown. The sandbox
side only ever sees the ``http_proxy`` route in front of the local
port, never the credential.

Config example::

    "managed_processes": {
        "some-mcp": {
            "command": ["npx", "-y", "supergateway",
                        "--stdio", "npx -y some-mcp-server",
                        "--outputTransport", "streamableHttp",
                        "--port", "9101"],
            "env": {"SOME_API_KEY": "sk_xxx"},
            "restart": true,           // default true
            "backoff_initial": 1.0,    // seconds, default 1
            "backoff_max": 30.0        // seconds, default 30
        }
    }

Pair it with an ``http_proxy`` service whose upstream is
``http://127.0.0.1:9101`` so the sandbox reaches the helper through
the proxy. Process status is served at ``GET /processes``.

Behavior notes:

- A spawn failure at startup is treated as a config error and aborts
  server startup (already-started processes are stopped again).
- Crashes after startup are respawned with exponential backoff
  (doubling from ``backoff_initial`` to ``backoff_max``); the backoff
  resets once a process has stayed up for a minute.
- stdout/stderr lines are logged with the process name as prefix, so
  they land in the server's (secret-masked) log.
"""

import asyncio
import contextlib
import logging
import os
import signal
import time

logger = logging.getLogger(__name__)

_STABLE_UPTIME_SECONDS = 60.0
_TERM_GRACE_SECONDS = 10.0


def validate_config(config: dict) -> None:
    """Fail fast on malformed managed_processes config."""
    for name, cfg in config.items():
        command = cfg.get("command")
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(c, str) for c in command)
        ):
            raise ValueError(
                f"managed process '{name}': 'command' must be a non-empty "
                f"list of strings"
            )
        env = cfg.get("env", {})
        if not isinstance(env, dict) or not all(
            isinstance(k, str) and isinstance(v, str)
            for k, v in env.items()
        ):
            raise ValueError(
                f"managed process '{name}': 'env' must be a mapping of "
                f"string to string"
            )


class ManagedProcess:
    """One supervised subprocess with restart-on-crash."""

    def __init__(self, name: str, cfg: dict):
        self.name = name
        self._command = list(cfg["command"])
        self._env = dict(cfg.get("env", {}))
        self._restart = bool(cfg.get("restart", True))
        self._backoff_initial = float(cfg.get("backoff_initial", 1.0))
        self._backoff_max = float(cfg.get("backoff_max", 30.0))
        self._proc: asyncio.subprocess.Process | None = None
        self._supervise_task: asyncio.Task | None = None
        self._pump_task: asyncio.Task | None = None
        self._stopping = False
        self.restarts = 0

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        """Spawn the process and begin supervising it.

        A spawn failure here propagates: at server startup a command
        that cannot start is a config error, not something to retry.
        """
        await self._spawn()
        self._supervise_task = asyncio.create_task(self._supervise())

    async def _spawn(self) -> None:
        env = {**os.environ, **self._env}
        # start_new_session puts the child in its own process group so
        # shutdown can take wrappers (npx etc.) and their children down
        # together.
        self._proc = await asyncio.create_subprocess_exec(
            *self._command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        self._pump_task = asyncio.create_task(self._pump_output(self._proc))
        logger.info(
            "managed process '%s' started (pid %d)",
            self.name, self._proc.pid,
        )

    async def _pump_output(self, proc: asyncio.subprocess.Process) -> None:
        """Log child output line by line under the process name."""
        assert proc.stdout is not None
        buf = b""
        while True:
            chunk = await proc.stdout.read(4096)
            if not chunk:
                break
            buf += chunk
            *lines, buf = buf.split(b"\n")
            for raw in lines:
                line = raw.decode(errors="replace").rstrip()
                if line:
                    logger.info("[%s] %s", self.name, line)
        tail = buf.decode(errors="replace").rstrip()
        if tail:
            logger.info("[%s] %s", self.name, tail)

    async def _supervise(self) -> None:
        backoff = self._backoff_initial
        while True:
            started = time.monotonic()
            returncode = await self._proc.wait()
            if self._stopping:
                return
            uptime = time.monotonic() - started
            logger.warning(
                "managed process '%s' exited with code %s after %.1fs",
                self.name, returncode, uptime,
            )
            if not self._restart:
                return
            if uptime >= _STABLE_UPTIME_SECONDS:
                backoff = self._backoff_initial
            await asyncio.sleep(backoff)
            if self._stopping:
                return
            backoff = min(backoff * 2, self._backoff_max)
            try:
                await self._spawn()
                self.restarts += 1
            except OSError as e:
                logger.error(
                    "managed process '%s' respawn failed: %s", self.name, e,
                )
                # self._proc is still the dead one, so the next wait()
                # returns immediately and we retry with growing backoff.

    async def stop(self) -> None:
        """Terminate the process group and stop supervising."""
        self._stopping = True
        proc = self._proc
        if proc is not None and proc.returncode is None:
            self._signal_group(proc, signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), _TERM_GRACE_SECONDS)
            except asyncio.TimeoutError:
                logger.warning(
                    "managed process '%s' ignored SIGTERM, killing",
                    self.name,
                )
                self._signal_group(proc, signal.SIGKILL)
                await proc.wait()
        if self._supervise_task is not None:
            self._supervise_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._supervise_task
        if self._pump_task is not None:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._pump_task, 5)

    def _signal_group(self, proc, sig: int) -> None:
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            pass
        except PermissionError:
            proc.send_signal(sig)

    def status(self) -> dict:
        return {
            "name": self.name,
            "running": self.running,
            "pid": self._proc.pid if self._proc is not None else None,
            "restarts": self.restarts,
        }


class ProcessSupervisor:
    """Spawns and supervises all configured managed processes."""

    def __init__(self, config: dict):
        validate_config(config)
        self._procs = [
            ManagedProcess(name, cfg) for name, cfg in config.items()
        ]

    async def start_all(self) -> None:
        started: list[ManagedProcess] = []
        try:
            for proc in self._procs:
                await proc.start()
                started.append(proc)
        except Exception:
            # Roll back so a config error at startup doesn't leave
            # orphaned children behind.
            await asyncio.gather(*(p.stop() for p in started))
            raise

    async def stop_all(self) -> None:
        await asyncio.gather(*(p.stop() for p in self._procs))

    def status(self) -> list[dict]:
        return [p.status() for p in self._procs]
