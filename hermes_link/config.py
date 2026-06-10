from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class LinkPaths:
    link_home: Path
    db_path: Path
    config_path: Path


@dataclass(frozen=True, slots=True)
class LinkConfig:
    node_id: str
    display_name: str
    base_url: str = "http://127.0.0.1:8765"
    capabilities: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["capabilities"] = data.get("capabilities") or {}
        return data


def resolve_paths(home: str | Path | None = None) -> LinkPaths:
    if home is None:
        home = os.getenv("HERMES_LINK_HOME")
    if home is None:
        hermes_home = Path(os.getenv("HERMES_HOME", Path.home() / ".hermes"))
        link_home = hermes_home / "link"
    else:
        link_home = Path(home)
    link_home = link_home.expanduser().resolve()
    return LinkPaths(link_home=link_home, db_path=link_home / "link.db", config_path=link_home / "config.json")


def save_config(paths: LinkPaths, config: LinkConfig) -> None:
    paths.link_home.mkdir(parents=True, exist_ok=True)
    paths.config_path.write_text(json.dumps(config.to_dict(), indent=2, sort_keys=True) + "\n")


def load_config(paths: LinkPaths) -> LinkConfig:
    data = json.loads(paths.config_path.read_text())
    return LinkConfig(
        node_id=data["node_id"],
        display_name=data.get("display_name", data["node_id"]),
        base_url=data.get("base_url", "http://127.0.0.1:8765"),
        capabilities=data.get("capabilities") or {},
    )


def default_capabilities(max_task_seconds: int = 600) -> dict[str, Any]:
    return {
        "profiles": ["default"],
        "toolsets": [],
        "max_task_seconds": max_task_seconds,
        "introspection": True,
        "introspection_kinds": ["plugins"],
    }
