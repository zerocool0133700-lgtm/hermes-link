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
- [x] Pairing token is one-time and removed after use.
- [x] No secrets in audit logs; only token prefixes or prompt hashes are recorded.
- [x] Task prompt logged only as a hash by default; raw prompt logging must be explicit in future versions.
- [x] Pairing revocation is documented in the two-box guide.
- [ ] Dedicated `revoke` CLI exists.
- [ ] Per-peer permission levels beyond `dispatch` exist.
- [ ] Optional TLS or reverse-proxy deployment guide exists for non-LAN use.

## v0 operational rule

Do not expose the Link receiver to the public internet. Use it on a trusted LAN or over a private tunnel/VPN only.
