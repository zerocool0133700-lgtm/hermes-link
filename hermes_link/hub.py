from __future__ import annotations

from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import secrets
import sqlite3
import time
from typing import Any
import urllib.error
import urllib.request
from urllib.parse import urlparse

from .executor import run_hermes_task
from .crypto import NODE_HEADER, verify_request_signature
from .models import utc_now
from .protocol import json_response, parse_json_body


HUB_NODE_ONLINE_TTL_SECONDS = 90


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _online_from_last_seen(last_seen_at: str | None, ttl_seconds: int = HUB_NODE_ONLINE_TTL_SECONDS) -> tuple[bool, int | None]:
    if not last_seen_at:
        return False, None
    try:
        age = int((datetime.now(timezone.utc) - _parse_utc(last_seen_at)).total_seconds())
    except (TypeError, ValueError):
        return False, None
    return age <= ttl_seconds, max(age, 0)


class HubHTTPServer(HTTPServer):
    allow_reuse_address = True


class HubStore:
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
                  node_token text not null unique,
                  shared_secret text,
                  created_at text not null,
                  updated_at text not null,
                  last_seen_at text
                );
                create table if not exists aliases(
                  alias text primary key,
                  node_id text not null
                );
                create table if not exists enrollment_tokens(
                  token text primary key,
                  allowed_node_id text,
                  aliases_json text not null,
                  created_at text not null,
                  expires_at text not null,
                  used_at text
                );
                create table if not exists hub_tasks(
                  task_id text primary key,
                  from_node_id text not null,
                  to_node_id text not null,
                  prompt text not null,
                  options_json text not null,
                  status text not null,
                  created_at text not null,
                  claimed_at text,
                  finished_at text,
                  exit_code integer,
                  stdout text not null default '',
                  stderr text not null default ''
                );
                """
            )
            try:
                conn.execute("alter table nodes add column shared_secret text")
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise

    def create_enrollment_token(self, allowed_node_id: str | None = None, ttl_seconds: int = 600, aliases: list[str] | None = None) -> dict[str, Any]:
        token = secrets.token_urlsafe(24)
        now_dt = datetime.now(timezone.utc).replace(microsecond=0)
        expires_dt = now_dt + timedelta(seconds=ttl_seconds)
        row = {
            "token": token,
            "allowed_node_id": allowed_node_id,
            "aliases": aliases or [],
            "created_at": now_dt.isoformat().replace("+00:00", "Z"),
            "expires_at": expires_dt.isoformat().replace("+00:00", "Z"),
            "used_at": None,
        }
        with self._connect() as conn:
            conn.execute(
                "insert into enrollment_tokens(token, allowed_node_id, aliases_json, created_at, expires_at, used_at) values (?, ?, ?, ?, ?, null)",
                (row["token"], row["allowed_node_id"], json.dumps(row["aliases"]), row["created_at"], row["expires_at"]),
            )
        return row

    def consume_enrollment_token(self, token: str, node_id: str | None = None) -> tuple[bool, str, dict[str, Any] | None]:
        with self._connect() as conn:
            row = conn.execute("select * from enrollment_tokens where token=?", (token,)).fetchone()
            if not row:
                return False, "invalid", None
            data = dict(row) | {"aliases": json.loads(row["aliases_json"])}
            if row["used_at"]:
                return False, "used", data
            if _parse_utc(row["expires_at"]) < datetime.now(timezone.utc):
                return False, "expired", data
            if row["allowed_node_id"] and node_id and row["allowed_node_id"] != node_id:
                return False, "wrong_node", data
            cur = conn.execute("update enrollment_tokens set used_at=? where token=? and used_at is null", (utc_now(), token))
        return (cur.rowcount == 1, "ok" if cur.rowcount == 1 else "used", data)

    def register_node(self, node_id: str, display_name: str, base_url: str, capabilities: dict[str, Any] | None = None, aliases: list[str] | None = None, node_token: str | None = None, shared_secret: str | None = None) -> str:
        now = utc_now()
        token = node_token or secrets.token_urlsafe(32)
        with self._connect() as conn:
            conn.execute(
                """
                insert into nodes(node_id, display_name, base_url, capabilities_json, node_token, shared_secret, created_at, updated_at, last_seen_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(node_id) do update set
                  display_name=excluded.display_name,
                  base_url=excluded.base_url,
                  capabilities_json=excluded.capabilities_json,
                  node_token=excluded.node_token,
                  shared_secret=excluded.shared_secret,
                  updated_at=excluded.updated_at,
                  last_seen_at=excluded.last_seen_at
                """,
                (node_id, display_name, base_url, json.dumps(capabilities or {}, sort_keys=True), token, shared_secret, now, now, now),
            )
            conn.execute("insert or replace into aliases(alias, node_id) values (?, ?)", (node_id, node_id))
            for alias in aliases or []:
                conn.execute("insert or replace into aliases(alias, node_id) values (?, ?)", (alias, node_id))
        return token

    def node_for_token(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        with self._connect() as conn:
            row = conn.execute("select * from nodes where node_token=?", (token,)).fetchone()
            if row:
                conn.execute("update nodes set last_seen_at=? where node_token=?", (utc_now(), token))
        return self._row_to_node(row) if row else None

    def node_with_secret(self, node_id: str) -> tuple[dict[str, Any], str] | None:
        with self._connect() as conn:
            row = conn.execute("select * from nodes where node_id=?", (node_id,)).fetchone()
        if not row or not row["shared_secret"]:
            return None
        return self._row_to_node(row), row["shared_secret"]

    def resolve_node_id(self, node_or_alias: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute("select node_id from aliases where alias=?", (node_or_alias,)).fetchone()
        return row["node_id"] if row else None

    def list_nodes(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("select node_id, display_name, base_url, capabilities_json, created_at, updated_at, last_seen_at from nodes order by node_id").fetchall()
        nodes = [self._row_to_node(row) for row in rows]
        aliases = self.list_aliases()
        for node in nodes:
            node["aliases"] = aliases.get(node["node_id"], [])
        return nodes

    def list_aliases(self) -> dict[str, list[str]]:
        with self._connect() as conn:
            rows = conn.execute("select alias, node_id from aliases order by alias").fetchall()
        aliases: dict[str, list[str]] = {}
        for row in rows:
            alias = row["alias"]
            node_id = row["node_id"]
            if alias == node_id:
                continue
            aliases.setdefault(node_id, []).append(alias)
        return aliases

    def list_profiles(self) -> list[dict[str, Any]]:
        profiles: list[dict[str, Any]] = []
        for node in self.list_nodes():
            capabilities = node.get("capabilities") or {}
            names = capabilities.get("profiles") if isinstance(capabilities, dict) else None
            if not isinstance(names, list) or not names:
                names = ["default"]
            for profile in names:
                if not isinstance(profile, str) or not profile:
                    continue
                profiles.append(
                    {
                        "id": f"link:{node['node_id']}/{profile}",
                        "node_id": node["node_id"],
                        "profile": profile,
                        "display_name": f"{node.get('display_name') or node['node_id']} / {profile}",
                        "aliases": node.get("aliases") or [],
                        "base_url": node.get("base_url"),
                        "last_seen_at": node.get("last_seen_at"),
                        "last_seen_age_seconds": node.get("last_seen_age_seconds"),
                        "online": bool(node.get("online")),
                        "capabilities": capabilities,
                    }
                )
        return profiles

    def create_task(self, from_node_id: str, to_node_id: str, prompt: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        resolved = self.resolve_node_id(to_node_id)
        if not resolved:
            raise KeyError(to_node_id)
        task_id = "hubtask_" + secrets.token_urlsafe(16)
        row = {
            "task_id": task_id,
            "from_node_id": from_node_id,
            "to_node_id": resolved,
            "prompt": prompt,
            "options": options or {},
            "status": "pending",
            "created_at": utc_now(),
            "claimed_at": None,
            "finished_at": None,
            "exit_code": None,
            "stdout": "",
            "stderr": "",
        }
        with self._connect() as conn:
            conn.execute(
                """
                insert into hub_tasks(task_id, from_node_id, to_node_id, prompt, options_json, status, created_at, claimed_at, finished_at, exit_code, stdout, stderr)
                values (?, ?, ?, ?, ?, ?, ?, null, null, null, '', '')
                """,
                (task_id, from_node_id, resolved, prompt, json.dumps(options or {}, sort_keys=True), row["status"], row["created_at"]),
            )
        return row

    def claim_task(self, node_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("select * from hub_tasks where to_node_id=? and status='pending' order by created_at limit 1", (node_id,)).fetchone()
            if not row:
                return None
            conn.execute("update hub_tasks set status='running', claimed_at=? where task_id=? and status='pending'", (utc_now(), row["task_id"]))
            row = conn.execute("select * from hub_tasks where task_id=?", (row["task_id"],)).fetchone()
        return self._row_to_task(row) if row else None

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("select * from hub_tasks where task_id=?", (task_id,)).fetchone()
        return self._row_to_task(row) if row else None

    def finish_task(self, task_id: str, node_id: str, status: str, exit_code: int | None, stdout: str, stderr: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("select * from hub_tasks where task_id=?", (task_id,)).fetchone()
            if not row or row["to_node_id"] != node_id:
                return None
            if row["status"] == "cancelled":
                return self._row_to_task(row)
            conn.execute(
                "update hub_tasks set status=?, finished_at=?, exit_code=?, stdout=?, stderr=? where task_id=?",
                (status, utc_now(), exit_code, stdout, stderr, task_id),
            )
            row = conn.execute("select * from hub_tasks where task_id=?", (task_id,)).fetchone()
        return self._row_to_task(row) if row else None

    def cancel_task(self, task_id: str, node_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("select * from hub_tasks where task_id=?", (task_id,)).fetchone()
            if not row or row["from_node_id"] != node_id:
                return None
            if row["status"] in {"succeeded", "failed", "timed_out", "cancelled"}:
                return self._row_to_task(row)
            conn.execute(
                "update hub_tasks set status='cancelled', finished_at=?, exit_code=?, stdout=?, stderr=? where task_id=?",
                (utc_now(), None, row["stdout"] or "", "Cancelled by requester", task_id),
            )
            row = conn.execute("select * from hub_tasks where task_id=?", (task_id,)).fetchone()
        return self._row_to_task(row) if row else None

    @staticmethod
    def _row_to_node(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        if "capabilities_json" in data:
            data["capabilities"] = json.loads(data.pop("capabilities_json"))
        online, age = _online_from_last_seen(data.get("last_seen_at"))
        data["online"] = online
        data["last_seen_age_seconds"] = age
        data.pop("node_token", None)
        data.pop("shared_secret", None)
        return data

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["options"] = json.loads(data.pop("options_json"))
        return data


def _bearer(headers) -> str | None:
    value = headers.get("Authorization") or ""
    if value.startswith("Bearer "):
        return value[len("Bearer ") :]
    return None


def make_hub_handler(store: HubStore):
    store.init_schema()

    class Handler(BaseHTTPRequestHandler):
        server_version = "HermesLinkHub/0.1"

        def log_message(self, format, *args):  # noqa: A002
            return

        def _json(self, status: int, data: dict):
            json_response(self, status, data)

        def _path(self) -> str:
            return urlparse(self.path).path

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length", "0"))
            return self.rfile.read(length) if length else b""

        def _require_node(self) -> dict[str, Any] | None:
            node = store.node_for_token(_bearer(self.headers))
            if not node:
                self._json(401, {"error": "unauthorized"})
            return node

        def _require_node_or_signed(self, body: bytes) -> dict[str, Any] | None:
            node = store.node_for_token(_bearer(self.headers))
            if node:
                return node
            peer = self.headers.get(NODE_HEADER)
            found = store.node_with_secret(peer) if peer else None
            if found:
                node, secret = found
                if verify_request_signature(secret, self.command, self._path(), body, dict(self.headers.items()), record_nonce=lambda _nonce: True):
                    return node
            self._json(401, {"error": "unauthorized"})
            return None

        def do_GET(self):
            path = self._path()
            if path == "/health":
                return self._json(200, {"ok": True, "service": "hermes-link-hub"})
            node = self._require_node()
            if not node:
                return
            if path == "/nodes":
                return self._json(200, {"nodes": store.list_nodes()})
            if path == "/profiles":
                return self._json(200, {"profiles": store.list_profiles()})
            if path.startswith("/tasks/"):
                parts = path.strip("/").split("/")
                task = store.get_task(parts[1]) if len(parts) >= 2 else None
                if not task:
                    return self._json(404, {"error": "task not found"})
                if task["from_node_id"] != node["node_id"] and task["to_node_id"] != node["node_id"]:
                    return self._json(403, {"error": "forbidden"})
                return self._json(200, task)
            if path.startswith("/chat/"):
                parts = path.strip("/").split("/")
                task = store.get_task(parts[1]) if len(parts) >= 2 else None
                if not task:
                    return self._json(404, {"error": "task not found"})
                if task["from_node_id"] != node["node_id"] and task["to_node_id"] != node["node_id"]:
                    return self._json(403, {"error": "forbidden"})
                return self._json(200, {"chat": task})
            return self._json(404, {"error": "not found"})

        def do_POST(self):
            path = self._path()
            body = self._read_body()
            data = parse_json_body(body) if body else {}
            if path == "/enroll":
                node_id = data.get("node_id")
                token = data.get("token")
                if not isinstance(node_id, str) or not node_id:
                    return self._json(400, {"error": "node_id is required"})
                if not isinstance(token, str) or not token:
                    return self._json(400, {"error": "token is required"})
                ok, reason, token_row = store.consume_enrollment_token(token, node_id)
                if not ok:
                    return self._json(403, {"error": f"token {reason}"})
                aliases = (token_row or {}).get("aliases") or []
                shared_secret = data.get("shared_secret") if isinstance(data.get("shared_secret"), str) else None
                node_token = store.register_node(node_id, data.get("display_name") or node_id, data.get("base_url") or "poll://" + node_id, data.get("capabilities") or {}, aliases=aliases, shared_secret=shared_secret)
                node = store.node_for_token(node_token)
                response = {
                    "node": node,
                    "node_token": node_token,
                    "hub_node_id": "dave-link-hub",
                    "hub_display_name": "Dave Link Hub",
                    "hub_base_url": f"http://{self.headers.get('Host', '127.0.0.1:8770')}",
                    "trust_level": "hub",
                }
                if shared_secret:
                    response["shared_secret"] = shared_secret
                return self._json(200, response)
            node = self._require_node_or_signed(body)
            if not node:
                return
            if path == "/heartbeat":
                return self._json(200, {"ok": True, "node_id": node["node_id"]})
            if path == "/tasks":
                prompt = data.get("prompt")
                to_node_id = data.get("to_node_id")
                if not isinstance(prompt, str) or not prompt:
                    return self._json(400, {"error": "prompt is required"})
                if not isinstance(to_node_id, str) or not to_node_id:
                    return self._json(400, {"error": "to_node_id is required"})
                try:
                    task = store.create_task(node["node_id"], to_node_id, prompt, data.get("options") or {})
                except KeyError:
                    return self._json(404, {"error": "target node not found"})
                return self._json(200, task)
            if path == "/chat":
                target = data.get("target")
                prompt = data.get("message") or data.get("prompt")
                if not isinstance(target, str) or not target:
                    return self._json(400, {"error": "target is required"})
                if not isinstance(prompt, str) or not prompt:
                    return self._json(400, {"error": "message is required"})
                if target.startswith("link:"):
                    target_body = target[len("link:") :]
                    target_node, _, profile = target_body.partition("/")
                else:
                    target_node, profile = target, ""
                raw_options = data.get("options")
                options: dict[str, Any] = dict(raw_options) if isinstance(raw_options, dict) else {}
                if profile:
                    options.setdefault("profile", profile)
                try:
                    task = store.create_task(node["node_id"], target_node, prompt, options)
                except KeyError:
                    return self._json(404, {"error": "target node not found"})
                return self._json(200, {"chat": task})
            if path == "/tasks/claim" or path in {"/claim", "/worker", "/worker/next"}:
                task = store.claim_task(node["node_id"])
                return self._json(200, {"task": task})
            if path.startswith("/tasks/") and path.endswith("/cancel"):
                task_id = path.strip("/").split("/")[1]
                result = store.cancel_task(task_id, node["node_id"])
                if not result:
                    return self._json(404, {"error": "task not found"})
                return self._json(200, result)
            if path.startswith("/chat/") and path.endswith("/cancel"):
                parts = path.strip("/").split("/")
                task_id = parts[1] if len(parts) >= 2 else ""
                result = store.cancel_task(task_id, node["node_id"])
                if not result:
                    return self._json(404, {"error": "task not found"})
                return self._json(200, {"chat": result})
            if path.startswith("/tasks/") and path.endswith("/result"):
                task_id = path.strip("/").split("/")[1]
                result = store.finish_task(task_id, node["node_id"], data.get("status") or "failed", data.get("exit_code"), data.get("stdout") or "", data.get("stderr") or "")
                if not result:
                    return self._json(404, {"error": "task not found"})
                return self._json(200, result)
            return self._json(404, {"error": "not found"})

    return Handler


def serve_hub(db_path: str | Path, host: str, port: int) -> None:
    store = HubStore(db_path)
    store.init_schema()
    server = HubHTTPServer((host, port), make_hub_handler(store))
    server.serve_forever()


def hub_json_request(method: str, url: str, data: dict | None = None, token: str | None = None, timeout: int = 30) -> dict:
    body = None if data is None else json.dumps(data, sort_keys=True).encode()
    headers: dict[str, str] = {}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def hub_worker_once(hub_url: str, node_token: str, max_timeout_seconds: int = 600) -> bool:
    base_url = hub_url.rstrip("/")
    data = hub_json_request("POST", base_url + "/tasks/claim", {}, token=node_token)
    task = data.get("task")
    if not task:
        return False

    task_id = task["task_id"]

    def should_cancel() -> bool:
        try:
            current = hub_json_request("GET", base_url + f"/tasks/{task_id}", token=node_token, timeout=5)
        except Exception:
            return False
        return current.get("status") == "cancelled"

    result = run_hermes_task(
        task["prompt"],
        task.get("options") or {},
        max_timeout_seconds=max_timeout_seconds,
        should_cancel=should_cancel,
    )
    hub_json_request(
        "POST",
        base_url + f"/tasks/{task_id}/result",
        {"status": result.status, "exit_code": result.exit_code, "stdout": result.stdout, "stderr": result.stderr},
        token=node_token,
    )
    return True


def hub_worker_loop(hub_url: str, node_token: str, poll_interval: float = 2.0, max_timeout_seconds: int = 600) -> None:
    while True:
        did_work = hub_worker_once(hub_url, node_token, max_timeout_seconds=max_timeout_seconds)
        if not did_work:
            hub_json_request("POST", hub_url.rstrip("/") + "/heartbeat", {}, token=node_token)
            time.sleep(poll_interval)
