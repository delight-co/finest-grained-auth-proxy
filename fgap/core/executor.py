import asyncio
import os
import pty


async def _drain_pty(fd: int) -> list[bytes]:
    """Read remaining data from a PTY master fd after process exit."""
    chunks: list[bytes] = []
    try:
        while True:
            data = os.read(fd, 4096)
            if not data:
                break
            chunks.append(data)
    except OSError:
        pass
    return chunks


async def execute_cli(
    binary: str,
    args: list[str],
    env_overrides: dict,
    timeout: int = 60,
    stdin_data: str | None = None,
) -> dict:
    """Execute a CLI command as an async subprocess.

    Uses PTY for stdout/stderr so that tools like ``gh`` detect a terminal
    environment and emit status messages (e.g. "✓ Merged ...") that they
    suppress when running under pipes.

    The credential is injected via env_overrides and never touches the caller's
    environment.

    Returns:
        {"exit_code": int, "stdout": str, "stderr": str}
    """
    env = {**os.environ, **env_overrides}

    stdout_master, stdout_slave = pty.openpty()
    stderr_master, stderr_slave = pty.openpty()

    try:
        proc = await asyncio.create_subprocess_exec(
            binary, *args,
            stdin=asyncio.subprocess.PIPE if stdin_data is not None else None,
            stdout=stdout_slave,
            stderr=stderr_slave,
            env=env,
        )
    except FileNotFoundError:
        os.close(stdout_master)
        os.close(stdout_slave)
        os.close(stderr_master)
        os.close(stderr_slave)
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Command not found: {binary}",
        }

    # Parent doesn't need the slave ends
    os.close(stdout_slave)
    os.close(stderr_slave)

    # Collect output from PTY masters via event loop readers
    loop = asyncio.get_event_loop()
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []

    def _make_reader(master_fd: int, chunks: list[bytes]):
        def reader():
            try:
                data = os.read(master_fd, 4096)
                if data:
                    chunks.append(data)
            except OSError:
                loop.remove_reader(master_fd)
        return reader

    loop.add_reader(stdout_master, _make_reader(stdout_master, stdout_chunks))
    loop.add_reader(stderr_master, _make_reader(stderr_master, stderr_chunks))

    try:
        if stdin_data is not None:
            proc.stdin.write(stdin_data.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        loop.remove_reader(stdout_master)
        loop.remove_reader(stderr_master)
        os.close(stdout_master)
        os.close(stderr_master)
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s",
        }

    # Drain any remaining data
    stdout_chunks.extend(await _drain_pty(stdout_master))
    stderr_chunks.extend(await _drain_pty(stderr_master))

    loop.remove_reader(stdout_master)
    loop.remove_reader(stderr_master)
    os.close(stdout_master)
    os.close(stderr_master)

    stdout = b"".join(stdout_chunks).decode("utf-8", errors="replace").replace("\r\n", "\n")
    stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace").replace("\r\n", "\n")

    return {
        "exit_code": proc.returncode,
        "stdout": stdout,
        "stderr": stderr,
    }
