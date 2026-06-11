from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Callable


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
    if resume_session := options.get("resume_session"):
        cmd.extend(["--resume", str(resume_session)])
    cmd.extend(["chat", "-q", prompt])
    if toolsets := options.get("toolsets"):
        cmd.extend(["--toolsets", str(toolsets)])
    return cmd


def run_hermes_task(
    prompt: str,
    options: dict[str, Any] | None = None,
    max_timeout_seconds: int = 600,
    should_cancel: Callable[[], bool] | None = None,
    cancel_poll_seconds: float = 1.0,
) -> TaskExecutionResult:
    options = options or {}
    timeout = int(options.get("timeout_seconds", max_timeout_seconds))
    timeout = max(1, min(timeout, max_timeout_seconds))
    workdir = options.get("workdir")
    cwd = str(Path(workdir).expanduser()) if workdir else None
    cmd = build_hermes_command(prompt, options)
    if should_cancel and should_cancel():
        return TaskExecutionResult("cancelled", None, "", "Task cancelled before start")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except subprocess.TimeoutExpired as exc:
        stderr = _text(exc.stderr)
        timeout_message = f"Task timed out after {timeout} seconds"
        return TaskExecutionResult("timed_out", None, _text(exc.stdout), "\n".join(part for part in (stderr, timeout_message) if part))
    except OSError as exc:
        return TaskExecutionResult("failed", None, "", str(exc))

    deadline = time.monotonic() + timeout
    poll_interval = max(0.1, float(cancel_poll_seconds))
    while proc.poll() is None:
        if should_cancel and should_cancel():
            proc.terminate()
            try:
                stdout, stderr = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
            return TaskExecutionResult("cancelled", proc.returncode, stdout or "", (stderr or "") + "Task cancelled by requester")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            proc.kill()
            stdout, stderr = proc.communicate()
            return TaskExecutionResult("timed_out", None, stdout or "", f"Task timed out after {timeout} seconds")
        time.sleep(min(poll_interval, remaining))

    stdout, stderr = proc.communicate()
    status = "succeeded" if proc.returncode == 0 else "failed"
    return TaskExecutionResult(status, proc.returncode, _text(proc.stdout), _text(proc.stderr))
