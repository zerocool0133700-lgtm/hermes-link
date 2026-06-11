from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import secrets
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class NodeRecord:
    node_id: str
    display_name: str
    base_url: str
    capabilities: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(slots=True)
class PairingRecord:
    peer_node_id: str
    peer_base_url: str
    shared_secret: str
    trust_level: str = "dispatch"
    created_at: str | None = None


@dataclass(slots=True)
class LinkTask:
    task_id: str
    peer_node_id: str
    prompt: str
    options: dict[str, Any] = field(default_factory=dict)
    status: str = "queued"
    created_at: str = field(default_factory=utc_now)
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""

    @classmethod
    def new(cls, peer_node_id: str, prompt: str, options: dict[str, Any] | None = None) -> "LinkTask":
        return cls(
            task_id="linktask_" + secrets.token_urlsafe(16),
            peer_node_id=peer_node_id,
            prompt=prompt,
            options=options or {},
        )


@dataclass(slots=True)
class FileRecord:
    file_id: str
    peer_node_id: str
    filename: str
    stored_path: str
    size_bytes: int
    sha256: str
    mime_type: str = "application/octet-stream"
    created_at: str = field(default_factory=utc_now)
