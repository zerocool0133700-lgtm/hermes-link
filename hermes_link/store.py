from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from .models import LinkTask, NodeRecord, PairingRecord, utc_now


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class LinkStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                create table if not exists nodes(
                  node_id text primary key,
                  display_name text not null,
                  base_url text not null,
                  capabilities_json text not null,
                  created_at text not null,
                  updated_at text not null
                );
                create table if not exists pairings(
                  peer_node_id text primary key,
                  peer_base_url text not null,
                  shared_secret text not null,
                  trust_level text not null,
                  created_at text not null
                );
                create table if not exists pairing_tokens(
                  token text primary key,
                  created_at text not null,
                  expires_at text not null,
                  used_at text
                );
                create table if not exists tasks(
                  task_id text primary key,
                  peer_node_id text not null,
                  prompt text not null,
                  options_json text not null,
                  status text not null,
                  created_at text not null,
                  started_at text,
                  finished_at text,
                  exit_code integer,
                  stdout text not null default '',
                  stderr text not null default ''
                );
                create table if not exists nonces(
                  peer_node_id text not null,
                  nonce text not null,
                  seen_at text not null,
                  primary key(peer_node_id, nonce)
                );
                create table if not exists audit(
                  id integer primary key autoincrement,
                  at text not null,
                  event_type text not null,
                  peer_node_id text,
                  task_id text,
                  summary text not null,
                  details_json text not null
                );
                """
            )

    def upsert_node(self, node: NodeRecord) -> None:
        now = utc_now()
        created_at = node.created_at or now
        with self._connect() as conn:
            conn.execute(
                """
                insert into nodes(node_id, display_name, base_url, capabilities_json, created_at, updated_at)
                values (?, ?, ?, ?, ?, ?)
                on conflict(node_id) do update set
                  display_name=excluded.display_name,
                  base_url=excluded.base_url,
                  capabilities_json=excluded.capabilities_json,
                  updated_at=excluded.updated_at
                """,
                (node.node_id, node.display_name, node.base_url, json.dumps(node.capabilities), created_at, now),
            )

    def get_node(self, node_id: str) -> NodeRecord | None:
        with self._connect() as conn:
            row = conn.execute("select * from nodes where node_id=?", (node_id,)).fetchone()
        return self._row_to_node(row) if row else None

    def list_nodes(self) -> list[NodeRecord]:
        with self._connect() as conn:
            rows = conn.execute("select * from nodes order by node_id").fetchall()
        return [self._row_to_node(row) for row in rows]

    def upsert_pairing(self, pairing: PairingRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                insert into pairings(peer_node_id, peer_base_url, shared_secret, trust_level, created_at)
                values (?, ?, ?, ?, ?)
                on conflict(peer_node_id) do update set
                  peer_base_url=excluded.peer_base_url,
                  shared_secret=excluded.shared_secret,
                  trust_level=excluded.trust_level
                """,
                (pairing.peer_node_id, pairing.peer_base_url, pairing.shared_secret, pairing.trust_level, pairing.created_at or utc_now()),
            )

    def get_pairing(self, peer_node_id: str) -> PairingRecord | None:
        with self._connect() as conn:
            row = conn.execute("select * from pairings where peer_node_id=?", (peer_node_id,)).fetchone()
        return self._row_to_pairing(row) if row else None

    def list_pairings(self) -> list[PairingRecord]:
        with self._connect() as conn:
            rows = conn.execute("select * from pairings order by peer_node_id").fetchall()
        return [self._row_to_pairing(row) for row in rows]

    def delete_pairing(self, peer_node_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("delete from pairings where peer_node_id=?", (peer_node_id,))
        return cur.rowcount > 0

    def create_pairing_token(self, token: str, ttl_seconds: int) -> dict[str, Any]:
        now_dt = datetime.now(timezone.utc).replace(microsecond=0)
        expires_dt = now_dt + timedelta(seconds=ttl_seconds)
        created_at = now_dt.isoformat().replace("+00:00", "Z")
        expires_at = expires_dt.isoformat().replace("+00:00", "Z")
        with self._connect() as conn:
            conn.execute(
                "insert into pairing_tokens(token, created_at, expires_at, used_at) values (?, ?, ?, null)",
                (token, created_at, expires_at),
            )
        return {"token": token, "created_at": created_at, "expires_at": expires_at, "used_at": None}

    def get_pairing_token(self, token: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("select * from pairing_tokens where token=?", (token,)).fetchone()
        return dict(row) if row else None

    def consume_pairing_token(self, token: str) -> tuple[bool, str]:
        row = self.get_pairing_token(token)
        if not row:
            return False, "invalid"
        if row["used_at"]:
            return False, "used"
        if _parse_utc(row["expires_at"]) < datetime.now(timezone.utc):
            return False, "expired"
        with self._connect() as conn:
            cur = conn.execute("update pairing_tokens set used_at=? where token=? and used_at is null", (utc_now(), token))
        return (cur.rowcount == 1, "ok" if cur.rowcount == 1 else "used")

    def create_task(self, task: LinkTask) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                insert into tasks(task_id, peer_node_id, prompt, options_json, status, created_at, started_at, finished_at, exit_code, stdout, stderr)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (task.task_id, task.peer_node_id, task.prompt, json.dumps(task.options), task.status, task.created_at, task.started_at, task.finished_at, task.exit_code, task.stdout, task.stderr),
            )

    def update_task(self, task_id: str, **fields: Any) -> None:
        if not fields:
            return
        allowed = {"status", "started_at", "finished_at", "exit_code", "stdout", "stderr"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unsupported task fields: {sorted(unknown)}")
        assignments = ", ".join(f"{key}=?" for key in fields)
        values = list(fields.values()) + [task_id]
        with self._connect() as conn:
            conn.execute(f"update tasks set {assignments} where task_id=?", values)

    def get_task(self, task_id: str) -> LinkTask | None:
        with self._connect() as conn:
            row = conn.execute("select * from tasks where task_id=?", (task_id,)).fetchone()
        return self._row_to_task(row) if row else None

    def record_nonce(self, peer_node_id: str, nonce: str) -> bool:
        try:
            with self._connect() as conn:
                conn.execute("insert into nonces(peer_node_id, nonce, seen_at) values (?, ?, ?)", (peer_node_id, nonce, utc_now()))
            return True
        except sqlite3.IntegrityError:
            return False

    def add_audit(self, event_type: str, peer_node_id: str | None = None, task_id: str | None = None, summary: str = "", details: dict[str, Any] | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "insert into audit(at, event_type, peer_node_id, task_id, summary, details_json) values (?, ?, ?, ?, ?, ?)",
                (utc_now(), event_type, peer_node_id, task_id, summary, json.dumps(details or {}, sort_keys=True)),
            )

    def list_audit(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("select * from audit order by id desc limit ?", (limit,)).fetchall()
        return [dict(row) | {"details": json.loads(row["details_json"])} for row in rows]

    @staticmethod
    def _row_to_node(row: sqlite3.Row) -> NodeRecord:
        return NodeRecord(row["node_id"], row["display_name"], row["base_url"], json.loads(row["capabilities_json"]), row["created_at"], row["updated_at"])

    @staticmethod
    def _row_to_pairing(row: sqlite3.Row) -> PairingRecord:
        return PairingRecord(row["peer_node_id"], row["peer_base_url"], row["shared_secret"], row["trust_level"], row["created_at"])

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> LinkTask:
        return LinkTask(
            task_id=row["task_id"], peer_node_id=row["peer_node_id"], prompt=row["prompt"], options=json.loads(row["options_json"]),
            status=row["status"], created_at=row["created_at"], started_at=row["started_at"], finished_at=row["finished_at"],
            exit_code=row["exit_code"], stdout=row["stdout"], stderr=row["stderr"],
        )
