import asyncio
import os


async def execute_cli(
    binary: str,
    args: list[str],
    env_overrides: dict,
    timeout: int | None = None,
    stdin_data: str | None = None,
) -> dict:
    """Execute a CLI command as an async subprocess.

    Sets ``GH_FORCE_TTY`` so that ``gh`` emits status messages (e.g.
    "✓ Merged ...") that it normally suppresses under pipes, and
    ``NO_COLOR`` to prevent ANSI color codes in the output.

    The credential is injected via env_overrides and never touches the caller's
    environment.

    Returns:
        {"exit_code": int, "stdout": str, "stderr": str}
    """
    env = {
        **os.environ,
        "GH_FORCE_TTY": "1",
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
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s",
        }

    return {
        "exit_code": proc.returncode,
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
    }
