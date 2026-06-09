# Hermes Link

Hermes Link connects multiple Hermes Agent systems into a small trusted mesh so one Hermes node can discover, pair with, and dispatch work to another Hermes node on the network.

Initial target: two Hermes boxes on the same LAN.

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
```

## Design principle

Start as a small explicit two-node connector, not a magical distributed brain. Pairing, permissions, audit logs, and least-privilege dispatch come before automatic routing.
