from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import threading
import time
import urllib.request

from hermes_link.crypto import sign_request
from hermes_link.hub import HubStore, make_hub_handler, HubHTTPServer, hub_json_request


def request(method, url, data=None, token=None, headers=None):
    body = None if data is None else json.dumps(data, sort_keys=True).encode()
    headers = dict(headers or {})
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, json.loads(resp.read().decode())


def start_hub(tmp_path):
    store = HubStore(tmp_path / "hub.db")
    store.init_schema()
    server = HubHTTPServer(("127.0.0.1", 0), make_hub_handler(store))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}", store


def make_fake_hermes(tmp_path: Path):
    fake = tmp_path / "hermes"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "print('fake-hermes:' + ' '.join(sys.argv[1:]))\n"
    )
    fake.chmod(0o755)
    return fake


def make_sleepy_fake_hermes(tmp_path: Path):
    fake = tmp_path / "sleepy-hermes"
    marker = tmp_path / "sleepy-started"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys, time\n"
        f"pathlib.Path({str(marker)!r}).write_text('started')\n"
        "print('sleepy-hermes:' + ' '.join(sys.argv[1:]), flush=True)\n"
        "time.sleep(30)\n"
        "print('sleepy-hermes:done', flush=True)\n"
    )
    fake.chmod(0o755)
    return fake, marker


def test_hub_enroll_registers_node_and_alias_with_one_time_token(tmp_path):
    server, base, store = start_hub(tmp_path)
    try:
        token = store.create_enrollment_token("jarvis", ttl_seconds=300, aliases=["windows-box"])["token"]
        status, data = request(
            "POST",
            base + "/enroll",
            {"token": token, "node_id": "jarvis", "display_name": "JARVIS", "base_url": "poll://jarvis", "capabilities": {"profiles": ["default"]}},
        )
        assert status == 200
        assert data["node"]["node_id"] == "jarvis"
        assert data["node_token"]
        assert store.resolve_node_id("windows-box") == "jarvis"
        assert store.consume_enrollment_token(token)[0] is False
    finally:
        server.shutdown()


def test_hub_relay_task_lifecycle_creates_claimable_task_and_result(tmp_path):
    server, base, store = start_hub(tmp_path)
    try:
        jarvis_token = store.register_node("jarvis", "JARVIS", "poll://jarvis", {}, aliases=["windows-box"])
        dave_token = store.register_node("dave-ellie-labs", "Dave", "poll://dave", {}, aliases=[])

        status, task = request("POST", base + "/tasks", {"to_node_id": "windows-box", "prompt": "hello", "options": {"profile": "default"}}, token=dave_token)
        assert status == 200
        assert task["to_node_id"] == "jarvis"
        task_id = task["task_id"]

        status, claim = request("POST", base + "/tasks/claim", {}, token=jarvis_token)
        assert status == 200
        assert claim["task"]["task_id"] == task_id
        assert claim["task"]["prompt"] == "hello"

        status, result = request("POST", base + f"/tasks/{task_id}/result", {"status": "succeeded", "exit_code": 0, "stdout": "ok", "stderr": ""}, token=jarvis_token)
        assert status == 200
        assert result["status"] == "succeeded"

        status, fetched = request("GET", base + f"/tasks/{task_id}/result", token=dave_token)
        assert status == 200
        assert fetched["stdout"] == "ok"
    finally:
        server.shutdown()


def test_hub_worker_once_claims_task_runs_hermes_and_submits_result(tmp_path, monkeypatch):
    from hermes_link.hub import hub_worker_once

    fake = make_fake_hermes(tmp_path)
    monkeypatch.setenv("HERMES_LINK_HERMES_BIN", str(fake))
    server, base, store = start_hub(tmp_path)
    try:
        jarvis_token = store.register_node("jarvis", "JARVIS", "poll://jarvis", {}, aliases=[])
        dave_token = store.register_node("dave", "Dave", "poll://dave", {}, aliases=[])
        _status, task = request("POST", base + "/tasks", {"to_node_id": "jarvis", "prompt": "hello worker", "options": {"profile": "default"}}, token=dave_token)

        assert hub_worker_once(base, jarvis_token, max_timeout_seconds=30) is True

        _status, result = request("GET", base + f"/tasks/{task['task_id']}/result", token=dave_token)
        assert result["status"] == "succeeded"
        assert "--profile default chat -q hello worker" in result["stdout"]
    finally:
        server.shutdown()


def test_hub_accepts_legacy_signed_worker_claim_after_enroll_shared_secret(tmp_path):
    server, base, store = start_hub(tmp_path)
    try:
        token = store.create_enrollment_token("ellie-home2", ttl_seconds=300)["token"]
        shared_secret = "ellie-secret"
        status, data = request(
            "POST",
            base + "/enroll",
            {
                "token": token,
                "node_id": "ellie-home2",
                "display_name": "Hermes ellie-home2",
                "base_url": "http://192.168.1.225:8765",
                "capabilities": {},
                "shared_secret": shared_secret,
            },
        )
        assert status == 200
        assert data["hub_node_id"] == "dave-link-hub"
        assert data["shared_secret"] == shared_secret

        body = {"node_id": "ellie-home2"}
        raw = json.dumps(body, sort_keys=True).encode()
        headers = sign_request("ellie-home2", shared_secret, "POST", "/claim", raw)
        status, claim = request("POST", base + "/claim", body, headers=headers)
        assert status == 200
        assert claim["task"] is None
    finally:
        server.shutdown()


def test_hub_profiles_lists_mesh_targets_with_aliases(tmp_path):
    server, base, store = start_hub(tmp_path)
    try:
        dave_token = store.register_node("dave", "Dave", "poll://dave", {"profiles": ["default"]}, aliases=[])
        store.register_node("jarvis", "JARVIS", "poll://jarvis", {"profiles": ["default", "work"]}, aliases=["windows-box"])

        status, data = request("GET", base + "/profiles", token=dave_token)
        assert status == 200
        profiles = {profile["id"]: profile for profile in data["profiles"]}
        assert "link:jarvis/default" in profiles
        assert "link:jarvis/work" in profiles
        assert profiles["link:jarvis/default"]["display_name"] == "JARVIS / default"
        assert profiles["link:jarvis/default"]["aliases"] == ["windows-box"]

        status, nodes = request("GET", base + "/nodes", token=dave_token)
        assert status == 200
        jarvis = next(node for node in nodes["nodes"] if node["node_id"] == "jarvis")
        assert jarvis["aliases"] == ["windows-box"]
    finally:
        server.shutdown()


def test_hub_profiles_mark_stale_nodes_offline_and_heartbeat_recovers(tmp_path):
    server, base, store = start_hub(tmp_path)
    try:
        dave_token = store.register_node("dave", "Dave", "poll://dave", {"profiles": ["default"]}, aliases=[])
        jarvis_token = store.register_node("jarvis", "JARVIS", "poll://jarvis", {"profiles": ["default"]}, aliases=[])
        stale = (datetime.now(timezone.utc) - timedelta(minutes=10)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        with store._connect() as conn:
            conn.execute("update nodes set last_seen_at=? where node_id='jarvis'", (stale,))

        status, data = request("GET", base + "/profiles", token=dave_token)
        assert status == 200
        jarvis = next(profile for profile in data["profiles"] if profile["id"] == "link:jarvis/default")
        assert jarvis["online"] is False
        assert jarvis["last_seen_age_seconds"] >= 90

        status, _heartbeat = request("POST", base + "/heartbeat", {}, token=jarvis_token)
        assert status == 200
        status, recovered = request("GET", base + "/profiles", token=dave_token)
        assert status == 200
        jarvis = next(profile for profile in recovered["profiles"] if profile["id"] == "link:jarvis/default")
        assert jarvis["online"] is True
        assert jarvis["last_seen_age_seconds"] is not None
        assert jarvis["last_seen_age_seconds"] < 90
    finally:
        server.shutdown()


def test_hub_chat_routes_link_profile_target_to_task(tmp_path):
    server, base, store = start_hub(tmp_path)
    try:
        dave_token = store.register_node("dave", "Dave", "poll://dave", {"profiles": ["default"]}, aliases=[])
        jarvis_token = store.register_node("jarvis", "JARVIS", "poll://jarvis", {"profiles": ["default"]}, aliases=["windows-box"])

        status, data = request("POST", base + "/chat", {"target": "link:windows-box/default", "message": "hello mesh"}, token=dave_token)
        assert status == 200
        task = data["chat"]
        assert task["to_node_id"] == "jarvis"
        assert task["prompt"] == "hello mesh"
        assert task["options"]["profile"] == "default"

        status, claim = request("POST", base + "/tasks/claim", {}, token=jarvis_token)
        assert status == 200
        assert claim["task"]["task_id"] == task["task_id"]

        status, fetched = request("GET", base + f"/chat/{task['task_id']}", token=dave_token)
        assert status == 200
        assert fetched["chat"]["task_id"] == task["task_id"]
    finally:
        server.shutdown()


def test_hub_chat_cancel_marks_requester_task_cancelled_and_preserves_cancel_on_late_result(tmp_path):
    server, base, store = start_hub(tmp_path)
    try:
        dave_token = store.register_node("dave", "Dave", "poll://dave", {"profiles": ["default"]}, aliases=[])
        jarvis_token = store.register_node("jarvis", "JARVIS", "poll://jarvis", {"profiles": ["default"]}, aliases=[])

        _status, data = request("POST", base + "/chat", {"target": "link:jarvis/default", "message": "slow mesh"}, token=dave_token)
        task_id = data["chat"]["task_id"]

        status, cancelled = request("POST", base + f"/chat/{task_id}/cancel", {}, token=dave_token)
        assert status == 200
        assert cancelled["chat"]["status"] == "cancelled"
        assert cancelled["chat"]["stderr"] == "Cancelled by requester"

        status, claim = request("POST", base + "/tasks/claim", {}, token=jarvis_token)
        assert status == 200
        assert claim["task"] is None

        status, late = request("POST", base + f"/tasks/{task_id}/result", {"status": "succeeded", "exit_code": 0, "stdout": "late", "stderr": ""}, token=jarvis_token)
        assert status == 200
        assert late["status"] == "cancelled"
        assert late["stdout"] == ""
    finally:
        server.shutdown()


def test_hub_worker_terminates_running_hermes_when_chat_is_cancelled(tmp_path, monkeypatch):
    from hermes_link.hub import hub_worker_once

    fake, marker = make_sleepy_fake_hermes(tmp_path)
    monkeypatch.setenv("HERMES_LINK_HERMES_BIN", str(fake))
    server, base, store = start_hub(tmp_path)
    try:
        dave_token = store.register_node("dave", "Dave", "poll://dave", {"profiles": ["default"]}, aliases=[])
        jarvis_token = store.register_node("jarvis", "JARVIS", "poll://jarvis", {"profiles": ["default"]}, aliases=[])
        _status, data = request("POST", base + "/chat", {"target": "link:jarvis/default", "message": "slow mesh"}, token=dave_token)
        task_id = data["chat"]["task_id"]

        worker_result: dict[str, bool] = {}
        thread = threading.Thread(target=lambda: worker_result.setdefault("did_work", hub_worker_once(base, jarvis_token, max_timeout_seconds=60)))
        thread.start()

        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.exists()

        status, cancelled = request("POST", base + f"/chat/{task_id}/cancel", {}, token=dave_token)
        assert status == 200
        assert cancelled["chat"]["status"] == "cancelled"

        thread.join(timeout=8)
        assert not thread.is_alive()
        assert worker_result["did_work"] is True

        status, fetched = request("GET", base + f"/chat/{task_id}", token=dave_token)
        assert status == 200
        assert fetched["chat"]["status"] == "cancelled"
        assert "done" not in fetched["chat"]["stdout"]
    finally:
        server.shutdown()


def test_local_three_node_hub_smoke_profiles_chat_cancel_and_stale_state(tmp_path, monkeypatch):
    from hermes_link.hub import hub_worker_once

    fast_fake = make_fake_hermes(tmp_path)
    slow_fake, slow_marker = make_sleepy_fake_hermes(tmp_path)
    server, base, store = start_hub(tmp_path)
    try:
        dave_token = store.register_node("dave-ellie-labs", "Dave Ellie Labs", "poll://dave", {"profiles": ["default"]}, aliases=["dave"])
        ellie_token = store.register_node("ellie-home2", "Ellie Home2", "poll://ellie", {"profiles": ["default"]}, aliases=["ellie"])
        jarvis_token = store.register_node("jarvis", "JARVIS", "poll://jarvis", {"profiles": ["default", "voice"]}, aliases=["windows-box"])

        status, profiles_response = request("GET", base + "/profiles", token=dave_token)
        assert status == 200
        profiles = {profile["id"]: profile for profile in profiles_response["profiles"]}
        assert {"link:dave-ellie-labs/default", "link:ellie-home2/default", "link:jarvis/default", "link:jarvis/voice"}.issubset(profiles)
        assert profiles["link:jarvis/default"]["aliases"] == ["windows-box"]
        assert profiles["link:ellie-home2/default"]["online"] is True

        monkeypatch.setenv("HERMES_LINK_HERMES_BIN", str(fast_fake))
        _status, jarvis_chat = request("POST", base + "/chat", {"target": "link:windows-box/voice", "message": "hello jarvis voice"}, token=dave_token)
        _status, ellie_chat = request("POST", base + "/chat", {"target": "link:ellie/default", "message": "hello ellie"}, token=dave_token)
        assert hub_worker_once(base, jarvis_token, max_timeout_seconds=30) is True
        assert hub_worker_once(base, ellie_token, max_timeout_seconds=30) is True

        _status, jarvis_result = request("GET", base + f"/chat/{jarvis_chat['chat']['task_id']}", token=dave_token)
        _status, ellie_result = request("GET", base + f"/chat/{ellie_chat['chat']['task_id']}", token=dave_token)
        assert jarvis_result["chat"]["status"] == "succeeded"
        assert "--profile voice chat -q hello jarvis voice" in jarvis_result["chat"]["stdout"]
        assert ellie_result["chat"]["status"] == "succeeded"
        assert "--profile default chat -q hello ellie" in ellie_result["chat"]["stdout"]

        stale = (datetime.now(timezone.utc) - timedelta(minutes=10)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        with store._connect() as conn:
            conn.execute("update nodes set last_seen_at=? where node_id='ellie-home2'", (stale,))
        _status, stale_profiles_response = request("GET", base + "/profiles", token=dave_token)
        stale_profiles = {profile["id"]: profile for profile in stale_profiles_response["profiles"]}
        assert stale_profiles["link:ellie-home2/default"]["online"] is False
        request("POST", base + "/heartbeat", {}, token=ellie_token)
        _status, recovered_profiles_response = request("GET", base + "/profiles", token=dave_token)
        recovered_profiles = {profile["id"]: profile for profile in recovered_profiles_response["profiles"]}
        assert recovered_profiles["link:ellie-home2/default"]["online"] is True

        monkeypatch.setenv("HERMES_LINK_HERMES_BIN", str(slow_fake))
        _status, slow_chat = request("POST", base + "/chat", {"target": "link:jarvis/default", "message": "slow cancellable"}, token=dave_token)
        slow_task_id = slow_chat["chat"]["task_id"]
        worker_result: dict[str, bool] = {}
        thread = threading.Thread(target=lambda: worker_result.setdefault("did_work", hub_worker_once(base, jarvis_token, max_timeout_seconds=60)))
        thread.start()
        deadline = time.monotonic() + 5
        while not slow_marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert slow_marker.exists()
        _status, cancelled = request("POST", base + f"/chat/{slow_task_id}/cancel", {}, token=dave_token)
        assert cancelled["chat"]["status"] == "cancelled"
        thread.join(timeout=8)
        assert not thread.is_alive()
        assert worker_result["did_work"] is True
        _status, final = request("GET", base + f"/chat/{slow_task_id}", token=dave_token)
        assert final["chat"]["status"] == "cancelled"
        assert "done" not in final["chat"]["stdout"]
    finally:
        server.shutdown()
