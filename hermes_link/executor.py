from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
from typing import Any


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


@dataclass(slots=True)
class TaskExecutionResult:
    status: str
    exit_code: int | None
    stdout: str
    stderr: str


def build_hermes_command(prompt: str, options: dict[str, Any] | None = None) -> list[str]:
    options = options or {}
    hermes_bin = os.getenv("HERMES_LINK_HERMES_BIN", "hermes")
    cmd = [hermes_bin]
    if profile := options.get("profile"):
        cmd.extend(["--profile", str(profile)])
    cmd.extend(["chat", "-q", prompt])
    if toolsets := options.get("toolsets"):
        cmd.extend(["--toolsets", str(toolsets)])
    return cmd


def run_hermes_task(prompt: str, options: dict[str, Any] | None = None, max_timeout_seconds: int = 600) -> TaskExecutionResult:
    options = options or {}
    timeout = int(options.get("timeout_seconds", max_timeout_seconds))
    timeout = max(1, min(timeout, max_timeout_seconds))
    workdir = options.get("workdir")
    cwd = str(Path(workdir).expanduser()) if workdir else None
    try:
        proc = subprocess.run(
            build_hermes_command(prompt, options),
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        stderr = _text(exc.stderr)
        timeout_message = f"Task timed out after {timeout} seconds"
        return TaskExecutionResult("timed_out", None, _text(exc.stdout), "\n".join(part for part in (stderr, timeout_message) if part))
    except OSError as exc:
        return TaskExecutionResult("failed", None, "", str(exc))
    status = "succeeded" if proc.returncode == 0 else "failed"
    return TaskExecutionResult(status, proc.returncode, _text(proc.stdout), _text(proc.stderr))
