# Hermes Link Security Checklist

Use this checklist before using Hermes Link beyond a local two-box LAN test.

- [x] Pairing required before dispatch.
- [x] Signed requests required after pairing.
- [x] Nonce replay rejection exists for signed requests.
- [x] Timestamp skew rejection exists for signed requests.
- [x] HMAC signatures use `hmac.compare_digest`.
- [x] Receiver binds to localhost by default.
- [x] LAN bind requires an explicit `--host 0.0.0.0` or interface flag.
- [x] Remote task timeout is enforced.
- [x] Hermes task execution uses argv lists and no shell invocation.
- [x] `/pair/start` is disabled by default.
- [x] Pairing tokens are one-time and expire.
- [x] Manual `pair-token create --ttl <seconds>` exists.
- [x] Optional `--allow-pair-node` limits which node IDs may complete pairing.
- [x] No secrets in audit logs; only token prefixes or prompt hashes are recorded.
- [x] Task prompt logged only as a hash by default; raw prompt logging must be explicit in future versions.
- [x] Dedicated `revoke` CLI exists.
- [x] Signed remote plugin introspection exists for paired nodes.
- [x] Plugin inventory is not exposed on public `/nodes/self`.
- [ ] Per-peer permission levels beyond `dispatch` exist.
- [x] Cloudflare Tunnel deployment guide exists for non-LAN use.

## v0 operational rule

Do not expose the Link receiver directly to the public internet. Use it on a trusted LAN, over a private tunnel/VPN, or behind a reverse proxy such as Cloudflare Tunnel with Access controls. See `docs/cloudflare-tunnel-vps.md`.
