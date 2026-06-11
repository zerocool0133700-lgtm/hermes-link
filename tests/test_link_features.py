import base64
import hashlib
import json
import time

from tests.test_server import request, signed_headers, start_server


def test_signed_node_introspection_reports_capabilities(tmp_path, monkeypatch):
    server, base = start_server(tmp_path, monkeypatch)
    try:
        status, data = request("GET", base + "/introspect/node", headers=signed_headers("GET", "/introspect/node"))
        assert status == 200
        assert data["kind"] == "node"
        assert data["node"]["node_id"] == "box-b"
        assert data["health"]["ok"] is True
        assert data["health"]["link_version"] == data["link_version"] == "0.1.0"
        assert data["capabilities"]["max_task_seconds"] == 5
    finally:
        server.shutdown()


def test_signed_file_transfer_stages_and_returns_content(tmp_path, monkeypatch):
    server, base = start_server(tmp_path, monkeypatch)
    content = b"hello forest file"
    body = {
        "filename": "note.txt",
        "mime_type": "text/plain",
        "sha256": hashlib.sha256(content).hexdigest(),
        "content_base64": base64.b64encode(content).decode(),
    }
    raw = json.dumps(body).encode()
    try:
        status, data = request("POST", base + "/files", body, signed_headers("POST", "/files", raw))
        assert status == 200
        assert data["filename"] == "note.txt"
        assert data["sha256"] == body["sha256"]

        status, fetched = request("GET", base + f"/files/{data['file_id']}", headers=signed_headers("GET", f"/files/{data['file_id']}"))
        assert status == 200
        assert base64.b64decode(fetched["content_base64"]) == content
    finally:
        server.shutdown()


def test_signed_sessions_list_and_chat_resume(tmp_path, monkeypatch):
    server, base = start_server(tmp_path, monkeypatch)
    try:
        status, sessions = request("GET", base + "/sessions", headers=signed_headers("GET", "/sessions"))
        assert status == 200
        assert "20260610_abc" in sessions["raw_output"]

        body = {"session_id": "20260610_abc", "prompt": "hello old agent", "options": {"timeout_seconds": 5}}
        raw = json.dumps(body).encode()
        status, task = request("POST", base + "/sessions/chat", body, signed_headers("POST", "/sessions/chat", raw))
        assert status == 200
        task_id = task["task_id"]
        result = {"status": "queued", "stdout": ""}
        deadline = time.time() + 5
        while time.time() < deadline:
            status, result = request("GET", base + f"/tasks/{task_id}/result", headers=signed_headers("GET", f"/tasks/{task_id}/result"))
            if result["status"] in {"succeeded", "failed", "timed_out"}:
                break
            time.sleep(0.05)
        assert result["status"] == "succeeded"
        assert "resumed:20260610_abc:hello old agent" in result["stdout"]
    finally:
        server.shutdown()


def test_signed_update_check_endpoint(tmp_path, monkeypatch):
    server, base = start_server(tmp_path, monkeypatch)
    try:
        status, data = request("GET", base + "/introspect/update", headers=signed_headers("GET", "/introspect/update"))
        assert status == 200
        assert data["kind"] == "update"
        assert data["remote_update_enabled"] is False
        assert "link_git_status" in data
    finally:
        server.shutdown()
