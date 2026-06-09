from __future__ import annotations

import hashlib
import hmac
import secrets
import time

NODE_HEADER = "X-Hermes-Link-Node"
TIMESTAMP_HEADER = "X-Hermes-Link-Timestamp"
NONCE_HEADER = "X-Hermes-Link-Nonce"
SIGNATURE_HEADER = "X-Hermes-Link-Signature"


def generate_secret() -> str:
    return secrets.token_urlsafe(32)


def signature_payload(method: str, path: str, timestamp: str, nonce: str, body: bytes) -> bytes:
    body_hash = hashlib.sha256(body or b"").hexdigest()
    return f"{method.upper()}\n{path}\n{timestamp}\n{nonce}\n{body_hash}".encode()


def sign_request(node_id: str, shared_secret: str, method: str, path: str, body: bytes = b"", timestamp: int | None = None, nonce: str | None = None) -> dict[str, str]:
    timestamp_s = str(int(timestamp if timestamp is not None else time.time()))
    nonce = nonce or secrets.token_hex(16)
    payload = signature_payload(method, path, timestamp_s, nonce, body)
    sig = hmac.new(shared_secret.encode(), payload, hashlib.sha256).hexdigest()
    return {NODE_HEADER: node_id, TIMESTAMP_HEADER: timestamp_s, NONCE_HEADER: nonce, SIGNATURE_HEADER: sig}


def _header(headers: dict[str, str], name: str) -> str | None:
    lowered = {k.lower(): v for k, v in headers.items()}
    return lowered.get(name.lower())


def verify_request_signature(shared_secret: str, method: str, path: str, body: bytes, headers: dict[str, str], now: int | None = None, record_nonce=lambda nonce: True, max_skew_seconds: int = 300) -> bool:
    timestamp = _header(headers, TIMESTAMP_HEADER)
    nonce = _header(headers, NONCE_HEADER)
    signature = _header(headers, SIGNATURE_HEADER)
    if not timestamp or not nonce or not signature:
        return False
    try:
        timestamp_i = int(timestamp)
    except ValueError:
        return False
    now_i = int(now if now is not None else time.time())
    if abs(now_i - timestamp_i) > max_skew_seconds:
        return False
    payload = signature_payload(method, path, timestamp, nonce, body)
    expected = hmac.new(shared_secret.encode(), payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return False
    return bool(record_nonce(nonce))
