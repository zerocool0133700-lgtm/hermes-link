from __future__ import annotations

import json
from typing import Any

from .models import LinkTask, NodeRecord


def parse_json_body(body: bytes) -> dict[str, Any]:
    try:
        data = json.loads((body or b"{}").decode())
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid json: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("invalid json: expected object")
    return data


def json_bytes(data: dict[str, Any], status: int = 200) -> bytes:
    return json.dumps(data, sort_keys=True).encode() + b"\n"


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if value is None:
        return ""
    return str(value)


def json_response(handler, status: int, data: dict[str, Any]) -> None:
    payload = json_bytes(data)
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def node_public_dict(node: NodeRecord) -> dict[str, Any]:
    return {"node_id": node.node_id, "display_name": node.display_name, "base_url": node.base_url, "capabilities": node.capabilities}


def task_public_dict(task: LinkTask, include_result: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        "task_id": task.task_id,
        "peer_node_id": task.peer_node_id,
        "status": task.status,
        "created_at": task.created_at,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        "exit_code": task.exit_code,
    }
    if include_result:
        data.update({"stdout": _text(task.stdout), "stderr": _text(task.stderr)})
    return data
