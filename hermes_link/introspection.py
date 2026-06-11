from __future__ import annotations

from dataclasses import dataclass
import json
import os
import subprocess
from typing import Any


@dataclass(slots=True)
class IntrospectionResult:
    status: str
    data: dict[str, Any]


def _hermes_bin() -> str:
    return os.getenv("HERMES_LINK_HERMES_BIN", "hermes")


def _run_hermes(args: list[str], timeout_seconds: int = 10) -> tuple[str, dict[str, Any]]:
    hermes_bin = _hermes_bin()
    try:
        proc = subprocess.run(
            [hermes_bin, *args],
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            shell=False,
        )
    except FileNotFoundError:
        return "error", {"error": f"hermes binary not found: {hermes_bin}", "command": [hermes_bin, *args]}
    except subprocess.TimeoutExpired:
        return "error", {"error": f"hermes {' '.join(args)} timed out after {timeout_seconds} seconds", "command": [hermes_bin, *args]}
    return (
        "ok" if proc.returncode == 0 else "error",
        {"command": [hermes_bin, *args], "exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr},
    )


def list_plugins(timeout_seconds: int = 10) -> IntrospectionResult:
    status, data = _run_hermes(["plugins", "list", "--json"], timeout_seconds)
    if status == "ok":
        try:
            plugins = json.loads(data["stdout"] or "[]")
        except json.JSONDecodeError:
            return IntrospectionResult("ok", {"kind": "plugins", "format": "raw", "raw_output": data["stdout"]})
        return IntrospectionResult("ok", {"kind": "plugins", "format": "json", "plugins": plugins})

    fallback_status, fallback = _run_hermes(["plugins", "list"], timeout_seconds)
    if fallback_status == "ok":
        return IntrospectionResult("ok", {"kind": "plugins", "format": "raw", "raw_output": fallback["stdout"]})
    return IntrospectionResult(
        "error",
        {
            "kind": "plugins",
            "error": "hermes plugins list failed",
            "stderr": (fallback.get("stderr") or data.get("stderr") or data.get("error", "")).strip(),
            "exit_code": fallback.get("exit_code", data.get("exit_code")),
        },
    )


def list_sessions(timeout_seconds: int = 10) -> IntrospectionResult:
    status, data = _run_hermes(["sessions", "list"], timeout_seconds)
    if status == "ok":
        return IntrospectionResult("ok", {"kind": "sessions", "format": "raw", "raw_output": data["stdout"]})
    return IntrospectionResult("error", {"kind": "sessions", "error": "hermes sessions list failed", **data})


def update_check(timeout_seconds: int = 20) -> IntrospectionResult:
    status, data = _run_hermes(["--version"], timeout_seconds)
    link_status = "unknown"
    link_details: dict[str, Any] = {}
    try:
        proc = subprocess.run(["git", "status", "--short", "--branch"], text=True, capture_output=True, timeout=timeout_seconds, shell=False)
        link_status = "ok" if proc.returncode == 0 else "error"
        link_details = {"exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
    except Exception as exc:  # pragma: no cover - defensive, not core behavior
        link_details = {"error": str(exc)}
    return IntrospectionResult(
        "ok" if status == "ok" else "error",
        {"kind": "update", "hermes_version": data, "link_git_status": {"status": link_status, **link_details}, "remote_update_enabled": False},
    )
