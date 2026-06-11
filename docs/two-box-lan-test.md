# Hermes Link two-box LAN test

This guide verifies Hermes Link with two Hermes boxes on the same LAN.

## Terms

- **Box A**: sender/operator node.
- **Box B**: receiver/worker node.
- **Port**: default `8765`.

## 1. Find LAN IPs

On each box:

```bash
hostname -I
```

Pick the LAN address reachable from the other box, for example `192.168.1.25`.

## 2. Initialize Box B

On Box B:

```bash
cd /home/dave/hermes-link
python -m hermes_link init --node-id box-b --name "Hermes Box B" --base-url "http://<box-b-lan-ip>:8765"
```

## 3. Start receiver on Box B

Local-only receiver is safest:

```bash
python -m hermes_link serve --host 127.0.0.1 --port 8765
```

For a LAN test, explicitly bind to the LAN interface or all interfaces:

```bash
python -m hermes_link serve --host 0.0.0.0 --port 8765
```

Firewall note: allow TCP port `8765` only from Box A's LAN IP. Do not expose this port to the internet.

## 4. Initialize Box A

On Box A:

```bash
cd /home/dave/hermes-link
python -m hermes_link init --node-id box-a --name "Hermes Box A" --base-url "http://<box-a-lan-ip>:8765"
```

## 5. Pair Box A with Box B

Pairing tokens are disabled by default on `/pair/start`. Create a short-lived token on Box B:

```bash
python -m hermes_link pair-token create --ttl 300
```

On Box A:

```bash
python -m hermes_link pair http://<box-b-lan-ip>:8765 --token <token-from-box-b>
python -m hermes_link nodes
```

Expected: `box-b` appears as a paired node.

If you explicitly want the older request-a-token flow for a short LAN-only pairing window, start Box B with:

```bash
python -m hermes_link serve --host 0.0.0.0 --port 8765 --pairing-enabled --pairing-window-seconds 300 --allow-pair-node box-a
```

## 6. Inspect trusted-node plugins

On Box A:

```bash
python -m hermes_link plugins box-b
```

Expected: Box B returns installed Hermes plugin information through signed introspection. This data is not exposed on public `/nodes/self`.

## 6a. Discover mesh profiles

On Box A:

```bash
python -m hermes_link profiles list --probe
python -m hermes_link profiles list --probe --json
```

Expected: Box A lists its own local profiles plus signed remote profiles such as `link:box-b/default`.

To route a chat turn to Box B's `default` Hermes profile:

```bash
python -m hermes_link profiles chat link:box-b/default "Say which Hermes profile handled this turn."
```

The command prints a `linktask_...` task id and a copy-pasteable result command.

## 7. Send a harmless task

On Box A:

```bash
python -m hermes_link send box-b "Run date and hostname, then summarize the result."
```

The command prints a `linktask_...` task id and copy-pasteable status command.

## 8. Poll and fetch result

On Box A:

```bash
python -m hermes_link status <task-id> --node box-b
python -m hermes_link result <task-id> --node box-b
```

Expected:

- Status eventually becomes `succeeded`.
- Result includes Box B's Hermes output.
- Box B audit log contains task created/running/succeeded events.

## 9. Inspect audit log manually

The state DB is under the Link home, defaulting to `~/.hermes/link/link.db`:

```bash
sqlite3 ~/.hermes/link/link.db 'select at,event_type,peer_node_id,task_id,summary from audit order by id desc limit 20;'
```

The prompt summary should be a hash by default, not raw sensitive prompt text.

## 10. Stop receiver

Use Ctrl-C in the terminal running `serve`.

## 11. Revoke a pairing

Remove the pairing on the receiver or sender:

```bash
python -m hermes_link revoke box-a
```

Repeat with the opposite peer id on the other node if needed.
