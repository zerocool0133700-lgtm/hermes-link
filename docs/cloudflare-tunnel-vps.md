# Hermes Link Cloudflare Tunnel VPS deployment

This guide is for running a Hermes Link receiver on a VPS behind a Cloudflare Tunnel.

Dave's first public mesh rendezvous hostname is:

```text
vps-link.ellie-labs.dev
```

## Threat model

A public Cloudflare hostname is not the same as a trusted LAN. Keep the receiver bound to localhost and let Cloudflare Tunnel provide the only ingress path.

Do not bind Hermes Link directly to a public VPS interface unless you also have a separate firewall and access-control layer.

## 1. Initialize the VPS node

On the VPS, clone or copy the Hermes Link repo, create a virtualenv, then initialize the node with the public Cloudflare hostname as its base URL:

```bash
cd /opt/hermes-link
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'

.venv/bin/python -m hermes_link init \
  --home /var/lib/hermes-link \
  --node-id vps-link \
  --name 'VPS Link' \
  --base-url https://vps-link.ellie-labs.dev \
  --max-task-seconds 600
```

The VPS can be used as the first mesh rendezvous node: home machines pair with `vps-link`, then use signed mesh inventory to see the nodes the VPS knows about.

## 2. Start Hermes Link on localhost only

On the VPS:

```bash
python -m hermes_link serve --host 127.0.0.1 --port 8765
```

For a persistent service, create `/etc/systemd/system/hermes-link.service`:

```ini
[Unit]
Description=Hermes Link receiver
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=hermes-link
WorkingDirectory=/opt/hermes-link
ExecStart=/opt/hermes-link/.venv/bin/python -m hermes_link --home /var/lib/hermes-link serve --host 127.0.0.1 --port 8765
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Then enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now hermes-link
sudo systemctl status hermes-link --no-pager
```

For pairing, prefer manual tokens:

```bash
python -m hermes_link pair-token create --ttl 300
```

Then pair from the other node with the generated token:

```bash
python -m hermes_link pair https://vps-link.ellie-labs.dev --token <token>
```

If you need `/pair/start`, enable it only for a short explicit window:

```bash
python -m hermes_link serve \
  --host 127.0.0.1 \
  --port 8765 \
  --pairing-enabled \
  --pairing-window-seconds 300 \
  --allow-pair-node dave-ellie-labs
```

`/pair/start` is disabled by default. Tokens are one-time and expire.

## 3. Cloudflare Tunnel ingress example

Example `cloudflared` ingress:

```yaml
ingress:
  - hostname: vps-link.ellie-labs.dev
    service: http://127.0.0.1:8765
  - service: http_status:404
```

Recommended:

- Put Cloudflare Access in front of the hostname.
- Restrict allowed users/service tokens to your own devices.
- Keep the VPS firewall closed for TCP `8765` from the public internet.

## 4. Pair home nodes with the VPS

Create a short-lived token on the VPS:

```bash
/opt/hermes-link/.venv/bin/python -m hermes_link --home /var/lib/hermes-link pair-token create --ttl 300
```

From a home node such as `ellie-home2`, pair using that token:

```bash
python -m hermes_link --home /home/ellie/.hermes/profiles/ellie/link pair \
  https://vps-link.ellie-labs.dev \
  --token <token-from-vps>
```

Repeat with a fresh token for each additional node. Tokens are one-time and expire.

## 5. Pairing allowlist

When expecting specific nodes, add `--allow-pair-node`:

```bash
python -m hermes_link serve \
  --host 127.0.0.1 \
  --port 8765 \
  --pairing-enabled \
  --allow-pair-node ellie-home2 \
  --allow-pair-node dave-ellie-labs
```

Any other `node_id` will be rejected during `/pair/complete`.

## 6. Verify mesh inventory

After pairing a home node with the VPS, ask the VPS for its signed mesh inventory:

```bash
python -m hermes_link --home /home/ellie/.hermes/profiles/ellie/link mesh nodes vps-link
```

Expected shape:

```json
{
  "nodes": [
    {
      "node_id": "vps-link",
      "display_name": "VPS Link",
      "base_url": "https://vps-link.ellie-labs.dev",
      "capabilities": {"...": "..."},
      "relationship": "self"
    }
  ]
}
```

`relationship` values:

- `self` — the node answering the request.
- `direct` — a directly paired node known by the answering node.
- `known` — a node record known by the answering node but not currently a direct pairing.

This is mesh visibility, not multi-hop execution yet. Task dispatch still requires a direct trusted pairing.

## 7. Revoke a node

If a pairing should no longer be trusted:

```bash
python -m hermes_link revoke dave-ellie-labs
```

Repeat on the peer if you want both sides to remove the relationship.

## 8. Introspection

Trusted paired nodes can request narrow introspection such as installed Hermes plugins:

```bash
python -m hermes_link plugins dave-ellie-labs
```

Introspection endpoints require signed paired-node requests. Detailed plugin inventory is not exposed on public `/nodes/self`.
