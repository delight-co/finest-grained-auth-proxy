import asyncio
import os

# GNU timeout's convention for "killed because it ran too long". Callers can
# tell a proxy-killed command apart from the command itself failing (its own
# exit code and stderr) or the binary being absent (-1).
EXIT_TIMEOUT = 124


async def execute_cli(
    binary: str,
    args: list[str],
    env_overrides: dict,
    timeout: int | None = None,
    stdin_data: str | None = None,
    *,
    allowed_binaries: frozenset[str],
) -> dict:
    """Execute a CLI command as an async subprocess.

    Sets ``GH_FORCE_TTY`` so that ``gh`` emits status messages (e.g.
    "✓ Merged ...") that it normally suppresses under pipes, and
    ``NO_COLOR`` to prevent ANSI color codes in the output.

    The credential is injected via env_overrides and never touches the caller's
    environment.

    Returns:
        {"exit_code": int, "stdout": str, "stderr": str}

        A command that outlives ``timeout`` is killed and reported with
        ``exit_code`` :data:`EXIT_TIMEOUT` (124) and a stderr note
        attributing the kill to the proxy — the command itself did not
        fail; it was still running.

    Raises:
        ValueError: if ``binary`` is not in ``allowed_binaries``. The set
            must be supplied by the caller — this function is generic and
            has no notion of which binaries are legal in a given
            deployment; making the allowlist explicit at the call site
            keeps the invariant local to the code that assembles it.
    """
    if binary not in allowed_binaries:
        raise ValueError(
            f"execute_cli refused unknown binary {binary!r} "
            f"(allowed: {sorted(allowed_binaries)})"
        )

    env = {
        **os.environ,
        "GH_FORCE_TTY": "true",
        "NO_COLOR": "1",
        **env_overrides,
    }

    try:
        proc = await asyncio.create_subprocess_exec(
            binary, *args,
            stdin=asyncio.subprocess.PIPE if stdin_data is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except FileNotFoundError:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Command not found: {binary}",
        }

    input_bytes = stdin_data.encode("utf-8") if stdin_data is not None else None
    try:
        if timeout is not None:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=input_bytes), timeout=timeout,
            )
        else:
            stdout, stderr = await proc.communicate(input=input_bytes)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {
            "exit_code": EXIT_TIMEOUT,
            "stdout": "",
            "stderr": (
                f"fgap proxy: command killed after {timeout}s by the proxy "
                f"CLI timeout (timeouts.cli) — the command itself did not "
                f"fail; it was still running. Blocking commands that wait "
                f"on external events do not fit the proxy's single-shot "
                f"execution model; prefer a polling equivalent."
            ),
        }

    return {
        "exit_code": proc.returncode,
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
    }
