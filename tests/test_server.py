import json
import stat
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from hermes_link.config import LinkConfig, LinkPaths, save_config
from hermes_link.crypto import sign_request
from hermes_link.server import LinkHTTPServer, make_handler
from hermes_link.store import LinkStore


def start_server(tmp_path, monkeypatch):
    fake = tmp_path / "hermes"
    fake.write_text("#!/usr/bin/env python3\nimport sys\nprint('remote ok:' + sys.argv[-1])\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("HERMES_LINK_HERMES_BIN", str(fake))

    paths = LinkPaths(tmp_path, tmp_path / "link.db", tmp_path / "config.json")
    config = LinkConfig("box-b", "Box B", "http://127.0.0.1:0", {"profiles": ["default"], "max_task_seconds": 5})
    save_config(paths, config)
    store = LinkStore(paths.db_path)
    store.init_schema()
    store.upsert_pairing(__import__("hermes_link.models", fromlist=["PairingRecord"]).PairingRecord("box-a", "http://127.0.0.1:9999", "secret", "dispatch"))
    handler = make_handler(paths, config, store)
    server = LinkHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def request(method, url, body=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode())


def signed_headers(method, path, body=b""):
    return sign_request("box-a", "secret", method, path, body)


def test_health_and_self_endpoints(tmp_path, monkeypatch):
    server, base = start_server(tmp_path, monkeypatch)
    try:
        status, data = request("GET", base + "/health")
        assert status == 200
        assert data["ok"] is True
        status, data = request("GET", base + "/nodes/self")
        assert data["node_id"] == "box-b"
    finally:
        server.shutdown()


def test_signed_task_lifecycle(tmp_path, monkeypatch):
    server, base = start_server(tmp_path, monkeypatch)
    try:
        body = {"prompt": "hello remote", "options": {"timeout_seconds": 5}}
        raw = json.dumps(body).encode()
        status, task = request("POST", base + "/tasks", body, signed_headers("POST", "/tasks", raw))
        assert status == 200
        task_id = task["task_id"]

        deadline = time.time() + 5
        while time.time() < deadline:
            status, meta = request("GET", base + f"/tasks/{task_id}", headers=signed_headers("GET", f"/tasks/{task_id}"))
            if meta["status"] in {"succeeded", "failed", "timed_out"}:
                break
            time.sleep(0.05)
        status, result = request("GET", base + f"/tasks/{task_id}/result", headers=signed_headers("GET", f"/tasks/{task_id}/result"))
        assert result["status"] == "succeeded"
        assert "remote ok:hello remote" in result["stdout"]
    finally:
        server.shutdown()


def test_unsigned_task_is_rejected(tmp_path, monkeypatch):
    server, base = start_server(tmp_path, monkeypatch)
    try:
        try:
            request("POST", base + "/tasks", {"prompt": "nope"})
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        else:
            raise AssertionError("unsigned task should fail")
    finally:
        server.shutdown()
