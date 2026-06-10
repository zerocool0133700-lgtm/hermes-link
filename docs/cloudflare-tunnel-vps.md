# Hermes Link Cloudflare Tunnel VPS deployment

This guide is for running a Hermes Link receiver on a VPS behind a Cloudflare Tunnel. It assumes the node has already been initialized with `python -m hermes_link init ...`.

## Threat model

A public Cloudflare hostname is not the same as a trusted LAN. Keep the receiver bound to localhost and let Cloudflare Tunnel provide the only ingress path.

Do not bind Hermes Link directly to a public VPS interface unless you also have a separate firewall and access-control layer.

## 1. Start Hermes Link on localhost only

On the VPS:

```bash
python -m hermes_link serve --host 127.0.0.1 --port 8765
```

For pairing, prefer manual tokens:

```bash
python -m hermes_link pair-token create --ttl 300
```

Then pair from the other node with the generated token:

```bash
python -m hermes_link pair https://vps-link.example.com --token <token>
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

## 2. Cloudflare Tunnel ingress example

Example `cloudflared` ingress:

```yaml
ingress:
  - hostname: vps-link.example.com
    service: http://127.0.0.1:8765
  - service: http_status:404
```

Recommended:

- Put Cloudflare Access in front of the hostname.
- Restrict allowed users/service tokens to your own devices.
- Keep the VPS firewall closed for TCP `8765` from the public internet.

## 3. Pairing allowlist

When expecting one specific node, add `--allow-pair-node`:

```bash
python -m hermes_link serve \
  --host 127.0.0.1 \
  --port 8765 \
  --pairing-enabled \
  --allow-pair-node dave-ellie-labs
```

Any other `node_id` will be rejected during `/pair/complete`.

## 4. Revoke a node

If a pairing should no longer be trusted:

```bash
python -m hermes_link revoke dave-ellie-labs
```

Repeat on the peer if you want both sides to remove the relationship.

## 5. Introspection

Trusted paired nodes can request narrow introspection such as installed Hermes plugins:

```bash
python -m hermes_link plugins dave-ellie-labs
```

Introspection endpoints require signed paired-node requests. Detailed plugin inventory is not exposed on public `/nodes/self`.
