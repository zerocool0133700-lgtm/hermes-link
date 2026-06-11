from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import secrets
import sys
import urllib.error
import urllib.request
from typing import Any

from .config import LinkConfig, default_capabilities, load_config, resolve_paths, save_config
from .crypto import generate_secret, sign_request
from .hub import HubStore, hub_json_request, hub_worker_loop, hub_worker_once, serve_hub
from .models import NodeRecord, PairingRecord
from .protocol import profile_public_dict
from .server import serve
from .store import LinkStore


def _with_home(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("--home", default=argparse.SUPPRESS, help="Hermes Link home directory, defaults to $HERMES_LINK_HOME or $HERMES_HOME/link")
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes_link", description="Hermes Link - pair Hermes boxes and dispatch remote tasks")
    parser.add_argument("--home", help="Hermes Link home directory, defaults to $HERMES_LINK_HOME or $HERMES_HOME/link")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = _with_home(sub.add_parser("init", help="initialize this Link node"))
    p_init.add_argument("--node-id", required=True)
    p_init.add_argument("--name", required=True)
    p_init.add_argument("--base-url", default="http://127.0.0.1:8765")
    p_init.add_argument("--max-task-seconds", type=int, default=600)

    p_serve = _with_home(sub.add_parser("serve", help="run the Link HTTP receiver"))
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.add_argument("--pairing-enabled", action="store_true", help="allow /pair/start to create short-lived pairing tokens")
    p_serve.add_argument("--pairing-window-seconds", type=int, default=300, help="TTL for tokens created by /pair/start")
    p_serve.add_argument("--allow-pair-node", action="append", default=[], help="node_id allowed to complete pairing; repeat for multiple nodes")

    p_pair_token = sub.add_parser("pair-token", help="manage manual pairing tokens")
    pair_token_sub = p_pair_token.add_subparsers(dest="pair_token_command", required=True)
    p_pair_token_create = _with_home(pair_token_sub.add_parser("create", help="create a one-time pairing token"))
    p_pair_token_create.add_argument("--ttl", type=int, default=300, help="token TTL in seconds")

    p_pair = _with_home(sub.add_parser("pair", help="pair with another Link node"))
    p_pair.add_argument("node_url")
    p_pair.add_argument("--token", help="pairing token from remote pair-token create or /pair/start; if omitted, Link requests one")

    p_enroll = _with_home(sub.add_parser("enroll", help="enroll this node with a Hermes Link hub"))
    p_enroll.add_argument("hub_url")
    p_enroll.add_argument("--token", required=True, help="one-time enrollment token from the hub")

    p_worker = _with_home(sub.add_parser("worker", help="poll hub for tasks for this node"))
    p_worker.add_argument("--once", action="store_true")
    p_worker.add_argument("--poll-interval", type=float, default=2.0)

    p_hub_send = _with_home(sub.add_parser("hub-send", help="send a task through the hub relay"))
    p_hub_send.add_argument("node")
    p_hub_send.add_argument("prompt")
    p_hub_send.add_argument("--timeout-seconds", type=int)
    p_hub_send.add_argument("--profile")
    p_hub_send.add_argument("--toolsets")
    p_hub_send.add_argument("--workdir")

    p_hub_status = _with_home(sub.add_parser("hub-status", help="fetch hub task status/result"))
    p_hub_status.add_argument("task_id")

    p_hub = sub.add_parser("hub", help="run a central Hermes Link registry/relay hub")
    hub_sub = p_hub.add_subparsers(dest="hub_command", required=True)
    p_hub_init = _with_home(hub_sub.add_parser("init", help="initialize hub database"))
    p_hub_token = _with_home(hub_sub.add_parser("token", help="create one-time enrollment token"))
    p_hub_token.add_argument("--node-id")
    p_hub_token.add_argument("--alias", action="append", default=[])
    p_hub_token.add_argument("--ttl", type=int, default=600)
    p_hub_serve = _with_home(hub_sub.add_parser("serve", help="serve hub HTTP API"))
    p_hub_serve.add_argument("--host", default="127.0.0.1")
    p_hub_serve.add_argument("--port", type=int, default=8770)

    p_nodes = _with_home(sub.add_parser("nodes", help="list self and paired nodes"))
    p_nodes.add_argument("--probe", action="store_true", help="signed live probe of each paired node")
    p_nodes.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    p_introspect = _with_home(sub.add_parser("introspect", help="signed node/plugin/update introspection for a paired node"))
    p_introspect.add_argument("node")
    p_introspect.add_argument("kind", choices=["node", "plugins", "update"], nargs="?", default="node")

    p_files = sub.add_parser("files", help="send/list/get files through signed Link transfer")
    files_sub = p_files.add_subparsers(dest="files_command", required=True)
    p_files_send = _with_home(files_sub.add_parser("send", help="send a file to a paired node staging area"))
    p_files_send.add_argument("node")
    p_files_send.add_argument("path")
    p_files_send.add_argument("--mime-type", default="application/octet-stream")
    p_files_list = _with_home(files_sub.add_parser("list", help="list files staged on a paired node"))
    p_files_list.add_argument("node")
    p_files_get = _with_home(files_sub.add_parser("get", help="download a staged file from a paired node"))
    p_files_get.add_argument("node")
    p_files_get.add_argument("file_id")
    p_files_get.add_argument("--output")

    p_sessions = sub.add_parser("sessions", help="list or chat with remote Hermes sessions")
    sessions_sub = p_sessions.add_subparsers(dest="sessions_command", required=True)
    p_sessions_list = _with_home(sessions_sub.add_parser("list", help="list sessions available on a paired node"))
    p_sessions_list.add_argument("node")
    p_sessions_chat = _with_home(sessions_sub.add_parser("chat", help="send a prompt to a remote resumed session"))
    p_sessions_chat.add_argument("node")
    p_sessions_chat.add_argument("session_id")
    p_sessions_chat.add_argument("prompt")
    p_sessions_chat.add_argument("--timeout-seconds", type=int)

    p_update_check = _with_home(sub.add_parser("update-check", help="check local or paired-node Hermes/Link update state"))
    p_update_check.add_argument("node", nargs="?")

    p_revoke = _with_home(sub.add_parser("revoke", help="remove a paired node"))
    p_revoke.add_argument("node")

    p_plugins = _with_home(sub.add_parser("plugins", help="list installed Hermes plugins on a paired node"))
    p_plugins.add_argument("node")

    p_mesh = sub.add_parser("mesh", help="inspect signed mesh inventory from a paired node")
    mesh_sub = p_mesh.add_subparsers(dest="mesh_command", required=True)
    p_mesh_nodes = _with_home(mesh_sub.add_parser("nodes", help="list nodes known by a paired mesh node"))
    p_mesh_nodes.add_argument("node")

    p_profiles = sub.add_parser("profiles", help="discover and chat with local or remote mesh profiles")
    profiles_sub = p_profiles.add_subparsers(dest="profiles_command", required=True)
    p_profiles_list = _with_home(profiles_sub.add_parser("list", help="list local and paired remote profiles"))
    p_profiles_list.add_argument("--probe", action="store_true", help="signed live profile discovery from paired nodes")
    p_profiles_list.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    p_profiles_chat = _with_home(profiles_sub.add_parser("chat", help="send a prompt to a remote profile like link:node/profile"))
    p_profiles_chat.add_argument("remote_profile_id")
    p_profiles_chat.add_argument("prompt")
    p_profiles_chat.add_argument("--timeout-seconds", type=int)

    p_send = _with_home(sub.add_parser("send", help="send a task to a paired node"))
    p_send.add_argument("node")
    p_send.add_argument("prompt")
    p_send.add_argument("--timeout-seconds", type=int)
    p_send.add_argument("--profile")
    p_send.add_argument("--toolsets")
    p_send.add_argument("--workdir")

    p_status = _with_home(sub.add_parser("status", help="fetch remote task status"))
    p_status.add_argument("task_id")
    p_status.add_argument("--node")

    p_result = _with_home(sub.add_parser("result", help="fetch remote task result"))
    p_result.add_argument("task_id")
    p_result.add_argument("--node")

    return parser


def _store_and_config(home=None):
    paths = resolve_paths(home)
    store = LinkStore(paths.db_path)
    store.init_schema()
    config = load_config(paths)
    return paths, store, config


def _hub_db_path(paths) -> Path:
    return paths.link_home / "hub.db"


def _hub_config_path(paths) -> Path:
    return paths.link_home / "hub.json"


def _load_hub_config(paths) -> dict[str, Any]:
    path = _hub_config_path(paths)
    if not path.exists():
        raise FileNotFoundError(f"{path}; run enroll first")
    return json.loads(path.read_text())


def _save_hub_config(paths, data: dict[str, Any]) -> None:
    paths.link_home.mkdir(parents=True, exist_ok=True)
    _hub_config_path(paths).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def _json_request(method: str, url: str, data: dict | None = None, headers: dict[str, str] | None = None) -> dict:
    body = None if data is None else json.dumps(data, sort_keys=True).encode()
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _signed_request(config: LinkConfig, pairing: PairingRecord, method: str, path: str, data: dict | None = None) -> dict:
    body = b"" if data is None else json.dumps(data, sort_keys=True).encode()
    headers = sign_request(config.node_id, pairing.shared_secret, method, path, body)
    return _json_request(method, pairing.peer_base_url.rstrip("/") + path, data, headers)


def cmd_init(args) -> int:
    paths = resolve_paths(args.home)
    caps = default_capabilities(max_task_seconds=args.max_task_seconds)
    config = LinkConfig(args.node_id, args.name, args.base_url, caps)
    save_config(paths, config)
    store = LinkStore(paths.db_path)
    store.init_schema()
    store.upsert_node(NodeRecord(config.node_id, config.display_name, config.base_url, config.capabilities or {}))
    print(f"Initialized Hermes Link node {config.node_id} at {paths.link_home}")
    print(f"Start receiver: python -m hermes_link --home {paths.link_home} serve --host 0.0.0.0 --port 8765")
    return 0


def cmd_pair_token_create(args) -> int:
    _paths, store, _config = _store_and_config(args.home)
    token = secrets.token_urlsafe(24)
    store.create_pairing_token(token, args.ttl)
    store.add_audit("pair.token_created", summary="manual pairing token created", details={"token_prefix": token[:4], "ttl_seconds": args.ttl})
    print(token)
    return 0


def cmd_nodes(args) -> int:
    paths, store, config = _store_and_config(args.home)
    pairings = store.list_pairings()
    rows: list[dict[str, Any]] = [{"node_id": config.node_id, "display_name": config.display_name, "base_url": config.base_url, "status": "self", "capabilities": config.capabilities or {}}]
    for p in pairings:
        row = {"node_id": p.peer_node_id, "base_url": p.peer_base_url, "trust_level": p.trust_level, "status": "paired"}
        if args.probe:
            try:
                row["probe"] = _signed_request(config, p, "GET", "/introspect/node")
                row["status"] = "active"
            except Exception as exc:
                row["status"] = "offline"
                row["error"] = str(exc)
        rows.append(row)
    if args.json:
        print(json.dumps({"nodes": rows}, indent=2, sort_keys=True))
        return 0
    print(f"Self: {config.node_id} ({config.display_name}) {config.base_url}")
    peers = rows[1:]
    if not peers:
        print("Paired nodes: none")
    else:
        print("Paired nodes:")
        for row in peers:
            extra = f" status={row['status']}"
            if row.get("probe"):
                node = row["probe"].get("node", {})
                extra += f" link={row['probe'].get('link_version')} name={node.get('display_name', row['node_id'])}"
            if row.get("error"):
                extra += f" error={row['error']}"
            print(f"- {row['node_id']} {row['base_url']} trust={row.get('trust_level')}{extra}")
    return 0


def cmd_introspect(args) -> int:
    _paths, store, config = _store_and_config(args.home)
    pairing = _get_pairing_or_error(store, args.node)
    if not pairing:
        return 2
    path = {"node": "/introspect/node", "plugins": "/introspect/plugins", "update": "/introspect/update"}[args.kind]
    print(json.dumps(_signed_request(config, pairing, "GET", path), indent=2, sort_keys=True))
    return 0


def cmd_files(args) -> int:
    _paths, store, config = _store_and_config(args.home)
    pairing = _get_pairing_or_error(store, args.node)
    if not pairing:
        return 2
    if args.files_command == "send":
        source = Path(args.path).expanduser()
        content = source.read_bytes()
        payload = {
            "filename": source.name,
            "mime_type": args.mime_type,
            "sha256": hashlib.sha256(content).hexdigest(),
            "content_base64": base64.b64encode(content).decode(),
        }
        print(json.dumps(_signed_request(config, pairing, "POST", "/files", payload), indent=2, sort_keys=True))
        return 0
    if args.files_command == "list":
        print(json.dumps(_signed_request(config, pairing, "GET", "/files"), indent=2, sort_keys=True))
        return 0
    if args.files_command == "get":
        data = _signed_request(config, pairing, "GET", f"/files/{args.file_id}")
        content = base64.b64decode(data.pop("content_base64"))
        output = Path(args.output).expanduser() if args.output else Path(data["filename"])
        output.write_bytes(content)
        data["downloaded_to"] = str(output)
        print(json.dumps(data, indent=2, sort_keys=True))
        return 0
    return 2


def cmd_sessions(args) -> int:
    paths, store, config = _store_and_config(args.home)
    pairing = _get_pairing_or_error(store, args.node)
    if not pairing:
        return 2
    if args.sessions_command == "list":
        print(json.dumps(_signed_request(config, pairing, "GET", "/sessions"), indent=2, sort_keys=True))
        return 0
    if args.sessions_command == "chat":
        payload = {"session_id": args.session_id, "prompt": args.prompt, "options": {}}
        if args.timeout_seconds:
            payload["options"]["timeout_seconds"] = args.timeout_seconds
        result = _signed_request(config, pairing, "POST", "/sessions/chat", payload)
        print(result["task_id"])
        print(f"Result: python -m hermes_link --home {paths.link_home} result {result['task_id']} --node {args.node}")
        return 0
    return 2


def cmd_update_check(args) -> int:
    _paths, store, config = _store_and_config(args.home)
    if args.node:
        pairing = _get_pairing_or_error(store, args.node)
        if not pairing:
            return 2
        data = _signed_request(config, pairing, "GET", "/introspect/update")
    else:
        from .introspection import update_check

        data = update_check().data
    print(json.dumps(data, indent=2, sort_keys=True))
    return 0


def cmd_pair(args) -> int:
    paths, store, config = _store_and_config(args.home)
    remote = args.node_url.rstrip("/")
    token = args.token
    if not token:
        start = _json_request("POST", remote + "/pair/start", {})
        token = start["pairing_token"]
    shared_secret = generate_secret()
    payload = {
        "pairing_token": token,
        "node_id": config.node_id,
        "display_name": config.display_name,
        "base_url": config.base_url,
        "capabilities": config.capabilities or {},
        "shared_secret": shared_secret,
    }
    complete = _json_request("POST", remote + "/pair/complete", payload)
    node = complete["node"]
    shared_secret = complete.get("shared_secret", shared_secret)
    store.upsert_pairing(PairingRecord(node["node_id"], remote, shared_secret, "dispatch"))
    store.upsert_node(NodeRecord(node["node_id"], node.get("display_name", node["node_id"]), remote, node.get("capabilities") or {}))
    print(f"Paired with {node['node_id']} at {remote}")
    return 0


def cmd_enroll(args) -> int:
    paths, store, config = _store_and_config(args.home)
    hub = args.hub_url.rstrip("/")
    shared_secret = generate_secret()
    payload = {
        "token": args.token,
        "enrollment_token": args.token,
        "pairing_token": args.token,
        "node_id": config.node_id,
        "display_name": config.display_name,
        "base_url": config.base_url,
        "capabilities": config.capabilities or {},
        "shared_secret": shared_secret,
    }
    complete = _json_request("POST", hub + "/enroll", payload)
    node = complete.get("node") or complete.get("hub") or {}
    hub_node_id = complete.get("hub_node_id") or node.get("node_id") or complete.get("node_id") or "hub"
    hub_display_name = complete.get("hub_display_name") or node.get("display_name") or complete.get("display_name") or hub_node_id
    hub_base_url = complete.get("hub_base_url") or node.get("base_url") or complete.get("base_url") or hub
    if hub_node_id == config.node_id and hub_base_url == config.base_url:
        hub_node_id = "dave-link-hub"
        hub_display_name = "Dave Link Hub"
        hub_base_url = hub
    shared_secret = complete.get("shared_secret") or complete.get("worker_secret") or complete.get("secret") or complete.get("hmac_secret") or shared_secret
    trust_level = complete.get("trust_level", "hub")
    store.upsert_pairing(PairingRecord(hub_node_id, hub_base_url, shared_secret, trust_level))
    store.upsert_node(NodeRecord(hub_node_id, hub_display_name, hub_base_url, complete.get("capabilities") or node.get("capabilities") or {}))
    store.add_audit("hub.enrolled", peer_node_id=hub_node_id, summary="enrolled with hub", details={"hub_url": hub})
    print(f"Enrolled with {hub_node_id} at {hub_base_url}")
    print(f"Worker: python -m hermes_link --home {paths.link_home} worker --hub {hub_node_id}")
    return 0


def _get_pairing_or_error(store: LinkStore, node: str) -> PairingRecord | None:
    pairing = store.get_pairing(node)
    if not pairing:
        print(f"unknown paired node: {node}", file=sys.stderr)
        return None
    return pairing


def cmd_revoke(args) -> int:
    _paths, store, _config = _store_and_config(args.home)
    if not store.delete_pairing(args.node):
        print(f"unknown paired node: {args.node}", file=sys.stderr)
        return 2
    store.add_audit("pair.revoked", peer_node_id=args.node, summary="pairing revoked")
    print(f"Revoked pairing with {args.node}")
    return 0


def cmd_plugins(args) -> int:
    _paths, store, config = _store_and_config(args.home)
    pairing = _get_pairing_or_error(store, args.node)
    if not pairing:
        return 2
    data = _signed_request(config, pairing, "GET", "/introspect/plugins")
    print(json.dumps(data, indent=2, sort_keys=True))
    return 0


def cmd_mesh_nodes(args) -> int:
    _paths, store, config = _store_and_config(args.home)
    pairing = _get_pairing_or_error(store, args.node)
    if not pairing:
        return 2
    data = _signed_request(config, pairing, "GET", "/mesh/nodes")
    print(json.dumps(data, indent=2, sort_keys=True))
    return 0


def _parse_remote_profile_id(remote_profile_id: str) -> tuple[str, str] | None:
    if not remote_profile_id.startswith("link:") or "/" not in remote_profile_id:
        return None
    node, profile = remote_profile_id[len("link:") :].split("/", 1)
    if not node or not profile:
        return None
    return node, profile


def cmd_profiles(args) -> int:
    paths, store, config = _store_and_config(args.home)
    if args.profiles_command == "list":
        self_node = NodeRecord(config.node_id, config.display_name, config.base_url, config.capabilities or {})
        rows: list[dict[str, Any]] = []
        for profile in (config.capabilities or {}).get("profiles") or ["default"]:
            local = profile_public_dict(self_node, str(profile))
            local["remote_profile_id"] = f"local:{profile}"
            local["location"] = "local"
            local["status"] = "self"
            rows.append(local)
        for pairing in store.list_pairings():
            if args.probe:
                try:
                    data = _signed_request(config, pairing, "GET", "/profiles")
                    for row in data.get("profiles", []):
                        row["location"] = "remote"
                        row["status"] = "active"
                        rows.append(row)
                except Exception as exc:
                    rows.append({"remote_profile_id": f"link:{pairing.peer_node_id}/default", "node_id": pairing.peer_node_id, "profile": "default", "display_name": f"{pairing.peer_node_id} / default", "location": "remote", "status": "offline", "error": str(exc)})
            else:
                node = store.get_node(pairing.peer_node_id)
                capabilities = node.capabilities if node else {}
                display_name = node.display_name if node else pairing.peer_node_id
                remote_node = NodeRecord(pairing.peer_node_id, display_name, pairing.peer_base_url, capabilities or {"profiles": ["default"]})
                for profile in (remote_node.capabilities or {}).get("profiles") or ["default"]:
                    row = profile_public_dict(remote_node, str(profile))
                    row["location"] = "remote"
                    row["status"] = "paired"
                    rows.append(row)
        if args.json:
            print(json.dumps({"profiles": rows}, indent=2, sort_keys=True))
        else:
            for row in rows:
                print(f"{row['remote_profile_id']}\t{row['display_name']}\t{row['status']}")
        return 0

    if args.profiles_command == "chat":
        parsed = _parse_remote_profile_id(args.remote_profile_id)
        if not parsed:
            print("remote profile id must look like link:<node>/<profile>", file=sys.stderr)
            return 2
        node, profile = parsed
        pairing = _get_pairing_or_error(store, node)
        if not pairing:
            return 2
        options: dict[str, Any] = {"profile": profile}
        if args.timeout_seconds:
            options["timeout_seconds"] = args.timeout_seconds
        result = _signed_request(config, pairing, "POST", "/tasks", {"prompt": args.prompt, "options": options})
        print(result["task_id"])
        print(f"Result: python -m hermes_link --home {paths.link_home} result {result['task_id']} --node {node}")
        return 0
    return 2


def cmd_send(args) -> int:
    paths, store, config = _store_and_config(args.home)
    pairing = _get_pairing_or_error(store, args.node)
    if not pairing:
        return 2
    options = {k: v for k, v in {"timeout_seconds": args.timeout_seconds, "profile": args.profile, "toolsets": args.toolsets, "workdir": args.workdir}.items() if v is not None}
    result = _signed_request(config, pairing, "POST", "/tasks", {"prompt": args.prompt, "options": options})
    print(result["task_id"])
    print(f"Status: python -m hermes_link --home {paths.link_home} status {result['task_id']} --node {args.node}")
    return 0


def _find_pairing_for_task(store: LinkStore, node: str | None) -> PairingRecord | None:
    if node:
        return store.get_pairing(node)
    pairings = store.list_pairings()
    return pairings[0] if len(pairings) == 1 else None


def cmd_status_or_result(args, include_result: bool) -> int:
    _paths, store, config = _store_and_config(args.home)
    pairing = _find_pairing_for_task(store, args.node)
    if not pairing:
        print("specify --node when zero or multiple pairings exist", file=sys.stderr)
        return 2
    suffix = "/result" if include_result else ""
    data = _signed_request(config, pairing, "GET", f"/tasks/{args.task_id}{suffix}")
    print(json.dumps(data, indent=2, sort_keys=True))
    return 0


def cmd_hub(args) -> int:
    paths = resolve_paths(args.home)
    store = HubStore(_hub_db_path(paths))
    store.init_schema()
    if args.hub_command == "init":
        print(f"Initialized Hermes Link hub at {_hub_db_path(paths)}")
        print(f"Start hub: python -m hermes_link --home {paths.link_home} hub serve --host 127.0.0.1 --port 8770")
        return 0
    if args.hub_command == "token":
        row = store.create_enrollment_token(args.node_id, ttl_seconds=args.ttl, aliases=args.alias or [])
        print(row["token"])
        return 0
    if args.hub_command == "serve":
        print(f"Hermes Link hub serving on {args.host}:{args.port}")
        serve_hub(_hub_db_path(paths), args.host, args.port)
        return 0
    return 2


def cmd_enroll(args) -> int:
    paths, _store, config = _store_and_config(args.home)
    payload = {
        "token": args.token,
        "node_id": config.node_id,
        "display_name": config.display_name,
        "base_url": config.base_url,
        "capabilities": config.capabilities or {},
    }
    data = hub_json_request("POST", args.hub_url.rstrip("/") + "/enroll", payload)
    _save_hub_config(paths, {"hub_url": args.hub_url.rstrip("/"), "node_token": data["node_token"], "node_id": data["node"]["node_id"]})
    print(f"Enrolled {data['node']['node_id']} with hub {args.hub_url.rstrip('/')}")
    print(f"Start worker: python -m hermes_link --home {paths.link_home} worker")
    return 0


def cmd_worker(args) -> int:
    paths = resolve_paths(args.home)
    hub = _load_hub_config(paths)
    if args.once:
        did_work = hub_worker_once(hub["hub_url"], hub["node_token"])
        print("claimed task" if did_work else "no pending task")
        return 0
    hub_worker_loop(hub["hub_url"], hub["node_token"], poll_interval=args.poll_interval)
    return 0


def cmd_hub_send(args) -> int:
    paths = resolve_paths(args.home)
    hub = _load_hub_config(paths)
    options = {k: v for k, v in {"timeout_seconds": args.timeout_seconds, "profile": args.profile, "toolsets": args.toolsets, "workdir": args.workdir}.items() if v is not None}
    result = hub_json_request("POST", hub["hub_url"] + "/tasks", {"to_node_id": args.node, "prompt": args.prompt, "options": options}, token=hub["node_token"])
    print(result["task_id"])
    print(f"Status: python -m hermes_link --home {paths.link_home} hub-status {result['task_id']}")
    return 0


def cmd_hub_status(args) -> int:
    paths = resolve_paths(args.home)
    hub = _load_hub_config(paths)
    data = hub_json_request("GET", hub["hub_url"] + f"/tasks/{args.task_id}/result", token=hub["node_token"])
    print(json.dumps(data, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            return cmd_init(args)
        if args.command == "serve":
            paths, _store, config = _store_and_config(args.home)
            config = LinkConfig(config.node_id, config.display_name, f"http://{args.host}:{args.port}", config.capabilities)
            print(f"Hermes Link serving {config.node_id} on {args.host}:{args.port}")
            serve(
                paths,
                config,
                args.host,
                args.port,
                pairing_enabled=args.pairing_enabled,
                pairing_token_ttl_seconds=args.pairing_window_seconds,
                allowed_pair_nodes=set(args.allow_pair_node or []),
            )
            return 0
        if args.command == "pair-token" and args.pair_token_command == "create":
            return cmd_pair_token_create(args)
        if args.command == "pair":
            return cmd_pair(args)
        if args.command == "hub":
            return cmd_hub(args)
        if args.command == "enroll":
            return cmd_enroll(args)
        if args.command == "worker":
            return cmd_worker(args)
        if args.command == "hub-send":
            return cmd_hub_send(args)
        if args.command == "hub-status":
            return cmd_hub_status(args)
        if args.command == "nodes":
            return cmd_nodes(args)
        if args.command == "introspect":
            return cmd_introspect(args)
        if args.command == "files":
            return cmd_files(args)
        if args.command == "sessions":
            return cmd_sessions(args)
        if args.command == "update-check":
            return cmd_update_check(args)
        if args.command == "revoke":
            return cmd_revoke(args)
        if args.command == "plugins":
            return cmd_plugins(args)
        if args.command == "mesh" and args.mesh_command == "nodes":
            return cmd_mesh_nodes(args)
        if args.command == "profiles":
            return cmd_profiles(args)
        if args.command == "send":
            return cmd_send(args)
        if args.command == "status":
            return cmd_status_or_result(args, include_result=False)
        if args.command == "result":
            return cmd_status_or_result(args, include_result=True)
        if args.command == "worker":
            return cmd_worker(args)
    except FileNotFoundError as exc:
        print(f"missing config; run init first ({exc})", file=sys.stderr)
        return 2
    except urllib.error.HTTPError as exc:
        print(f"http error {exc.code}: {exc.read().decode(errors='replace')}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"network error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
