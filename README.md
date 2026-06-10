# Hermes Link

Hermes Link connects multiple Hermes Agent systems into a small trusted mesh so one Hermes node can discover, pair with, and dispatch work to another Hermes node on the network.

Initial target: two or three explicitly trusted Hermes boxes on the same LAN, then a Cloudflare-protected VPS rendezvous node.

## Vocabulary

- **Hermes Link**: the feature/system name.
- **Link mesh**: the network of connected Hermes systems.
- **Link node**: one Hermes installation participating in the mesh.
- **Link pairing**: the trust setup between two nodes.
- **Link dispatch**: a remote task sent from one node to another.

## v0 Goal

Build a minimal, secure LAN prototype:

1. Start a receiver on one Hermes box.
2. Pair a second Hermes box with it using a one-time pairing token.
3. Submit a task from the sender.
4. Receiver runs the task with `hermes chat -q` under a configured profile/toolset.
5. Sender polls for status and retrieves the final result.
6. Both sides keep an audit log.

## Planned CLI shape

```bash
hermes link init
hermes link serve
hermes link pair <node-url>
hermes link nodes
hermes link send <node> "<task>"
hermes link status <task-id>
hermes link result <task-id>
hermes link plugins <node>
hermes link mesh nodes <node>
hermes link revoke <node>
```

## Design principle

Start as a small explicit two-node connector, not a magical distributed brain. Pairing, permissions, audit logs, and least-privilege dispatch come before automatic routing.

## Current safety defaults

- The receiver binds to `127.0.0.1` by default.
- `/pair/start` is disabled by default; use `pair-token create --ttl 300` or explicitly start a short pairing window.
- Pairing tokens are one-time and expire.
- Signed requests are required for task dispatch, task results, and remote introspection.
- Signed mesh inventory is available through `mesh nodes <node>` when the remote node has the mesh endpoint.
- Installed plugin inventory is available only through signed introspection (`plugins <node>`), not public `/nodes/self`.

## Pairing direction

The node being paired into creates the one-time token.

Example: if Ellie on `ellie-home2` wants to pair to Jarvis on `windows-box`, Jarvis creates the token and Ellie runs `pair http://<jarvis-ip>:8765 --token <token-from-jarvis>`.

If Jarvis wants to pair back into Ellie, Ellie creates the token and Jarvis runs `pair http://<ellie-ip>:8765 --token <token-from-ellie>`.

Important: create the token with the same `--home` path used by the running receiver service. A token created in a different Link home will be rejected as invalid by the service.

For public VPS usage, see `docs/cloudflare-tunnel-vps.md`.
