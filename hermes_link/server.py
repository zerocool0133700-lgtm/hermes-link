from __future__ import annotations

from http.server import HTTPServer, BaseHTTPRequestHandler
import base64
import hashlib
import json
from pathlib import Path
import secrets
import threading
from urllib.parse import urlparse

from . import __version__
from .audit import safe_prompt_summary
from .config import LinkConfig, LinkPaths
from .crypto import NODE_HEADER, verify_request_signature
from .executor import run_hermes_task
from .introspection import list_plugins, list_sessions, update_check
from .models import FileRecord, LinkTask, NodeRecord, PairingRecord, utc_now
from .protocol import json_response, node_public_dict, parse_json_body, profiles_public_dict, task_public_dict
from .store import LinkStore


class LinkHTTPServer(HTTPServer):
    allow_reuse_address = True


def make_handler(
    paths: LinkPaths,
    config: LinkConfig,
    store: LinkStore,
    *,
    pairing_enabled: bool = False,
    pairing_token_ttl_seconds: int = 300,
    allowed_pair_nodes: set[str] | None = None,
):
    store.init_schema()
    self_node = NodeRecord(config.node_id, config.display_name, config.base_url, config.capabilities or {})
    store.upsert_node(self_node)
    allowed_pair_nodes = set(allowed_pair_nodes or set())

    def health_payload() -> dict[str, object]:
        return {"ok": True, "service": "hermes-link", "link_version": __version__}

    class Handler(BaseHTTPRequestHandler):
        server_version = "HermesLink/0.1"

        def log_message(self, format, *args):  # noqa: A002 - stdlib signature
            return

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length", "0"))
            return self.rfile.read(length) if length else b""

        def _json(self, status: int, data: dict):
            json_response(self, status, data)

        def _path(self) -> str:
            return urlparse(self.path).path

        def _require_signed(self, body: bytes) -> tuple[bool, str | None]:
            peer = self.headers.get(NODE_HEADER)
            if not peer:
                store.add_audit("auth.rejected", summary="missing node header", details={"path": self._path()})
                return False, None
            pairing = store.get_pairing(peer)
            if not pairing:
                store.add_audit("auth.rejected", peer_node_id=peer, summary="unknown paired node", details={"path": self._path()})
                return False, peer
            ok = verify_request_signature(
                pairing.shared_secret,
                self.command,
                self._path(),
                body,
                dict(self.headers.items()),
                record_nonce=lambda nonce: store.record_nonce(peer, nonce),
            )
            if not ok:
                store.add_audit("auth.rejected", peer_node_id=peer, summary="invalid signature", details={"path": self._path()})
                return False, peer
            return True, peer

        def _file_dict(self, record: FileRecord, include_content: bool = False) -> dict:
            data = {
                "file_id": record.file_id,
                "peer_node_id": record.peer_node_id,
                "filename": record.filename,
                "stored_path": record.stored_path,
                "size_bytes": record.size_bytes,
                "sha256": record.sha256,
                "mime_type": record.mime_type,
                "created_at": record.created_at,
            }
            if include_content:
                data["content_base64"] = base64.b64encode(Path(record.stored_path).read_bytes()).decode()
            return data

        def do_GET(self):
            path = self._path()
            if path == "/health":
                return self._json(200, health_payload())
            if path == "/nodes/self":
                return self._json(200, node_public_dict(self_node))
            if path == "/profiles":
                ok, peer = self._require_signed(b"")
                if not ok:
                    return self._json(401, {"error": "unauthorized"})
                store.add_audit("profiles.list.ok", peer_node_id=peer, summary="profile discovery")
                return self._json(200, profiles_public_dict(self_node))
            if path == "/introspect/node":
                ok, peer = self._require_signed(b"")
                if not ok:
                    return self._json(401, {"error": "unauthorized"})
                tasks = store.list_audit(limit=1)
                payload = {
                    "kind": "node",
                    "node": node_public_dict(self_node),
                    "link_version": __version__,
                    "health": health_payload(),
                    "last_audit_event": tasks[0] if tasks else None,
                    "capabilities": config.capabilities or {},
                }
                store.add_audit("introspect.node.ok", peer_node_id=peer, summary="node introspection")
                return self._json(200, payload)
            if path == "/introspect/plugins":
                ok, peer = self._require_signed(b"")
                if not ok:
                    return self._json(401, {"error": "unauthorized"})
                result = list_plugins()
                store.add_audit(f"introspect.plugins.{result.status}", peer_node_id=peer, summary=result.status)
                status = 200 if result.status == "ok" else 500
                return self._json(status, result.data)
            if path == "/mesh/nodes":
                ok, peer = self._require_signed(b"")
                if not ok:
                    return self._json(401, {"error": "unauthorized"})
                nodes_by_id = {node.node_id: node_public_dict(node) for node in store.list_nodes()}
                direct_pairings = {pairing.peer_node_id: pairing for pairing in store.list_pairings()}
                for peer_node_id, pairing in direct_pairings.items():
                    nodes_by_id.setdefault(
                        peer_node_id,
                        {
                            "node_id": peer_node_id,
                            "display_name": peer_node_id,
                            "base_url": pairing.peer_base_url,
                            "capabilities": {},
                        },
                    )
                nodes = [nodes_by_id[node_id] for node_id in sorted(nodes_by_id)]
                for node in nodes:
                    node["relationship"] = "self" if node["node_id"] == self_node.node_id else "direct" if node["node_id"] in direct_pairings else "known"
                store.add_audit("mesh.nodes.listed", peer_node_id=peer, summary=f"returned {len(nodes)} nodes")
                return self._json(200, {"nodes": nodes})
            if path == "/sessions":
                ok, peer = self._require_signed(b"")
                if not ok:
                    return self._json(401, {"error": "unauthorized"})
                result = list_sessions()
                store.add_audit(f"introspect.sessions.{result.status}", peer_node_id=peer, summary=result.status)
                status = 200 if result.status == "ok" else 500
                return self._json(status, result.data)
            if path == "/introspect/update":
                ok, peer = self._require_signed(b"")
                if not ok:
                    return self._json(401, {"error": "unauthorized"})
                result = update_check()
                store.add_audit(f"introspect.update.{result.status}", peer_node_id=peer, summary=result.status)
                status = 200 if result.status == "ok" else 500
                return self._json(status, result.data)
            if path == "/files":
                ok, _peer = self._require_signed(b"")
                if not ok:
                    return self._json(401, {"error": "unauthorized"})
                return self._json(200, {"files": [self._file_dict(row) for row in store.list_files()]})
            if path.startswith("/files/"):
                ok, _peer = self._require_signed(b"")
                if not ok:
                    return self._json(401, {"error": "unauthorized"})
                file_id = path.strip("/").split("/", 1)[1]
                record = store.get_file(file_id)
                if not record:
                    return self._json(404, {"error": "file not found"})
                return self._json(200, self._file_dict(record, include_content=True))
            if path.startswith("/tasks/"):
                ok, _peer = self._require_signed(b"")
                if not ok:
                    return self._json(401, {"error": "unauthorized"})
                parts = path.strip("/").split("/")
                task_id = parts[1] if len(parts) >= 2 else ""
                task = store.get_task(task_id)
                if not task:
                    return self._json(404, {"error": "task not found"})
                include_result = len(parts) == 3 and parts[2] == "result"
                return self._json(200, task_public_dict(task, include_result=include_result))
            return self._json(404, {"error": "not found"})

        def do_POST(self):
            path = self._path()
            body = self._read_body()
            try:
                data = parse_json_body(body)
            except ValueError as exc:
                return self._json(400, {"error": str(exc)})

            if path == "/pair/start":
                if not pairing_enabled:
                    store.add_audit("pair.start_rejected", summary="pairing disabled")
                    return self._json(403, {"error": "pairing disabled"})
                token = secrets.token_urlsafe(24)
                token_row = store.create_pairing_token(token, pairing_token_ttl_seconds)
                store.add_audit("pair.start", summary="pairing token created", details={"token_prefix": token[:4], "expires_at": token_row["expires_at"]})
                return self._json(200, {"pairing_token": token, "expires_at": token_row["expires_at"], "node": node_public_dict(self_node)})

            if path == "/pair/complete":
                token = data.get("pairing_token")
                peer_node_id = data.get("node_id")
                peer_base_url = data.get("base_url")
                shared_secret = data.get("shared_secret") or secrets.token_urlsafe(32)
                if not peer_node_id or not peer_base_url:
                    return self._json(400, {"error": "node_id and base_url are required"})
                if allowed_pair_nodes and peer_node_id not in allowed_pair_nodes:
                    store.add_audit("pair.peer_rejected", peer_node_id=peer_node_id, summary="node not allowed")
                    return self._json(403, {"error": "node not allowed for pairing"})
                ok, reason = store.consume_pairing_token(token) if token else (False, "invalid")
                if not ok:
                    event = "pair.token_expired" if reason == "expired" else "pair.token_rejected"
                    store.add_audit(event, summary=reason, details={"token_prefix": str(token or "")[:4]})
                    return self._json(401, {"error": "invalid pairing token"})
                store.upsert_pairing(PairingRecord(peer_node_id, peer_base_url, shared_secret, "dispatch"))
                store.upsert_node(NodeRecord(peer_node_id, data.get("display_name", peer_node_id), peer_base_url, data.get("capabilities") or {}))
                store.add_audit("pair.complete", peer_node_id=peer_node_id, summary="paired node")
                return self._json(200, {"node": node_public_dict(self_node), "shared_secret": shared_secret})

            if path == "/tasks":
                ok, peer = self._require_signed(body)
                if not ok:
                    return self._json(401, {"error": "unauthorized"})
                prompt = data.get("prompt")
                if not isinstance(prompt, str) or not prompt.strip():
                    return self._json(400, {"error": "prompt is required"})
                options = data.get("options") or {}
                task = LinkTask.new(peer_node_id=peer or "unknown", prompt=prompt, options=options)
                store.create_task(task)
                store.add_audit("task.created", peer_node_id=peer, task_id=task.task_id, summary=safe_prompt_summary(prompt), details={"options": options})

                thread = threading.Thread(target=_run_task, args=(task.task_id,), daemon=True)
                thread.start()
                return self._json(200, task_public_dict(task))

            if path == "/sessions/chat":
                ok, peer = self._require_signed(body)
                if not ok:
                    return self._json(401, {"error": "unauthorized"})
                session_id = data.get("session_id")
                prompt = data.get("prompt")
                if not isinstance(session_id, str) or not session_id.strip():
                    return self._json(400, {"error": "session_id is required"})
                if not isinstance(prompt, str) or not prompt.strip():
                    return self._json(400, {"error": "prompt is required"})
                options = data.get("options") or {}
                options["resume_session"] = session_id
                task = LinkTask.new(peer_node_id=peer or "unknown", prompt=prompt, options=options)
                store.create_task(task)
                store.add_audit("session.chat.created", peer_node_id=peer, task_id=task.task_id, summary=safe_prompt_summary(prompt), details={"session_id": session_id})
                threading.Thread(target=_run_task, args=(task.task_id,), daemon=True).start()
                return self._json(200, task_public_dict(task))

            if path == "/files":
                ok, peer = self._require_signed(body)
                if not ok:
                    return self._json(401, {"error": "unauthorized"})
                filename = Path(str(data.get("filename") or "attachment.bin")).name
                content_b64 = data.get("content_base64")
                if not isinstance(content_b64, str):
                    return self._json(400, {"error": "content_base64 is required"})
                try:
                    content = base64.b64decode(content_b64.encode(), validate=True)
                except Exception:
                    return self._json(400, {"error": "invalid base64 content"})
                max_bytes = int(((config.capabilities or {}).get("files") or {}).get("max_bytes", 10 * 1024 * 1024))
                if len(content) > max_bytes:
                    return self._json(413, {"error": "file exceeds max_bytes", "max_bytes": max_bytes})
                sha256 = hashlib.sha256(content).hexdigest()
                if data.get("sha256") and data["sha256"] != sha256:
                    return self._json(400, {"error": "sha256 mismatch", "actual_sha256": sha256})
                file_id = "linkfile_" + secrets.token_urlsafe(16)
                staging_rel = ((config.capabilities or {}).get("files") or {}).get("staging_dir", "files/incoming")
                staging_dir = paths.link_home / str(staging_rel)
                staging_dir.mkdir(parents=True, exist_ok=True)
                stored_path = staging_dir / f"{file_id}_{filename}"
                stored_path.write_bytes(content)
                record = FileRecord(file_id, peer or "unknown", filename, str(stored_path), len(content), sha256, data.get("mime_type") or "application/octet-stream")
                store.create_file(record)
                store.add_audit("file.received", peer_node_id=peer, summary=filename, details={"file_id": file_id, "size_bytes": len(content), "sha256": sha256})
                return self._json(200, self._file_dict(record))

            return self._json(404, {"error": "not found"})

    def _run_task(task_id: str) -> None:
        task = store.get_task(task_id)
        if not task:
            return
        max_timeout = int((config.capabilities or {}).get("max_task_seconds", 600))
        store.update_task(task_id, status="running", started_at=utc_now())
        result = run_hermes_task(task.prompt, task.options, max_timeout_seconds=max_timeout)
        store.update_task(task_id, status=result.status, finished_at=utc_now(), exit_code=result.exit_code, stdout=result.stdout, stderr=result.stderr)
        store.add_audit(f"task.{result.status}", peer_node_id=task.peer_node_id, task_id=task_id, summary=result.status, details={"exit_code": result.exit_code})

    return Handler


def serve(
    paths: LinkPaths,
    config: LinkConfig,
    host: str,
    port: int,
    *,
    pairing_enabled: bool = False,
    pairing_token_ttl_seconds: int = 300,
    allowed_pair_nodes: set[str] | None = None,
) -> LinkHTTPServer:
    store = LinkStore(paths.db_path)
    handler = make_handler(
        paths,
        config,
        store,
        pairing_enabled=pairing_enabled,
        pairing_token_ttl_seconds=pairing_token_ttl_seconds,
        allowed_pair_nodes=allowed_pair_nodes,
    )
    server = LinkHTTPServer((host, port), handler)
    server.serve_forever()
    return server
