from __future__ import annotations

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
from pathlib import Path
import secrets
import threading
from urllib.parse import urlparse

from .audit import safe_prompt_summary
from .config import LinkConfig, LinkPaths
from .crypto import NODE_HEADER, verify_request_signature
from .executor import run_hermes_task
from .models import LinkTask, NodeRecord, PairingRecord, utc_now
from .protocol import json_response, node_public_dict, parse_json_body, task_public_dict
from .store import LinkStore


class LinkHTTPServer(HTTPServer):
    allow_reuse_address = True


def make_handler(paths: LinkPaths, config: LinkConfig, store: LinkStore):
    store.init_schema()
    self_node = NodeRecord(config.node_id, config.display_name, config.base_url, config.capabilities or {})
    store.upsert_node(self_node)
    pairing_tokens: dict[str, dict] = {}

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

        def do_GET(self):
            path = self._path()
            if path == "/health":
                return self._json(200, {"ok": True, "service": "hermes-link"})
            if path == "/nodes/self":
                return self._json(200, node_public_dict(self_node))
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
                token = secrets.token_urlsafe(24)
                pairing_tokens[token] = {"created_at": utc_now()}
                store.add_audit("pair.start", summary="pairing token created", details={"token_prefix": token[:4]})
                return self._json(200, {"pairing_token": token, "node": node_public_dict(self_node)})

            if path == "/pair/complete":
                token = data.get("pairing_token")
                if not token or token not in pairing_tokens:
                    return self._json(401, {"error": "invalid pairing token"})
                peer_node_id = data.get("node_id")
                peer_base_url = data.get("base_url")
                shared_secret = data.get("shared_secret") or secrets.token_urlsafe(32)
                if not peer_node_id or not peer_base_url:
                    return self._json(400, {"error": "node_id and base_url are required"})
                del pairing_tokens[token]
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


def serve(paths: LinkPaths, config: LinkConfig, host: str, port: int) -> LinkHTTPServer:
    store = LinkStore(paths.db_path)
    handler = make_handler(paths, config, store)
    server = LinkHTTPServer((host, port), handler)
    server.serve_forever()
    return server
