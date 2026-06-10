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


def list_plugins(timeout_seconds: int = 10) -> IntrospectionResult:
    hermes_bin = _hermes_bin()
    try:
        proc = subprocess.run(
            [hermes_bin, "plugins", "list", "--json"],
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            shell=False,
        )
    except FileNotFoundError:
        return IntrospectionResult("error", {"kind": "plugins", "error": f"hermes binary not found: {hermes_bin}"})
    except subprocess.TimeoutExpired:
        return IntrospectionResult("error", {"kind": "plugins", "error": f"hermes plugins list timed out after {timeout_seconds} seconds"})

    if proc.returncode == 0:
        try:
            plugins = json.loads(proc.stdout or "[]")
        except json.JSONDecodeError:
            return IntrospectionResult("ok", {"kind": "plugins", "format": "raw", "raw_output": proc.stdout})
        return IntrospectionResult("ok", {"kind": "plugins", "format": "json", "plugins": plugins})

    fallback = subprocess.run(
        [hermes_bin, "plugins", "list"],
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        shell=False,
    )
    if fallback.returncode == 0:
        return IntrospectionResult("ok", {"kind": "plugins", "format": "raw", "raw_output": fallback.stdout})
    return IntrospectionResult(
        "error",
        {
            "kind": "plugins",
            "error": "hermes plugins list failed",
            "stderr": (fallback.stderr or proc.stderr).strip(),
            "exit_code": fallback.returncode,
        },
    )
