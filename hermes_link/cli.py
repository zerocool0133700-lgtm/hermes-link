from __future__ import annotations

import argparse
import json
import secrets
import sys
import urllib.error
import urllib.request

from .config import LinkConfig, default_capabilities, load_config, resolve_paths, save_config
from .crypto import generate_secret, sign_request
from .models import NodeRecord, PairingRecord
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

    _with_home(sub.add_parser("nodes", help="list self and paired nodes"))

    p_revoke = _with_home(sub.add_parser("revoke", help="remove a paired node"))
    p_revoke.add_argument("node")

    p_plugins = _with_home(sub.add_parser("plugins", help="list installed Hermes plugins on a paired node"))
    p_plugins.add_argument("node")

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
    print(f"Self: {config.node_id} ({config.display_name}) {config.base_url}")
    pairings = store.list_pairings()
    if not pairings:
        print("Paired nodes: none")
    else:
        print("Paired nodes:")
        for p in pairings:
            print(f"- {p.peer_node_id} {p.peer_base_url} trust={p.trust_level}")
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
        if args.command == "nodes":
            return cmd_nodes(args)
        if args.command == "revoke":
            return cmd_revoke(args)
        if args.command == "plugins":
            return cmd_plugins(args)
        if args.command == "send":
            return cmd_send(args)
        if args.command == "status":
            return cmd_status_or_result(args, include_result=False)
        if args.command == "result":
            return cmd_status_or_result(args, include_result=True)
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
