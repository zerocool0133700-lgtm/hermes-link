import time

from hermes_link.crypto import sign_request, verify_request_signature


def test_valid_signature_verifies_with_fresh_nonce():
    seen = set()
    headers = sign_request("node-a", "secret", "POST", "/tasks", b'{"prompt":"hi"}', timestamp=1_700_000_000, nonce="abc")
    ok = verify_request_signature("secret", "POST", "/tasks", b'{"prompt":"hi"}', headers, now=1_700_000_001, record_nonce=lambda n: seen.add(n) is None)
    assert ok is True
    assert "abc" in seen


def test_tampered_body_fails():
    headers = sign_request("node-a", "secret", "POST", "/tasks", b"original", timestamp=1_700_000_000, nonce="abc")
    assert verify_request_signature("secret", "POST", "/tasks", b"tampered", headers, now=1_700_000_001, record_nonce=lambda n: True) is False


def test_stale_timestamp_and_reused_nonce_fail():
    headers = sign_request("node-a", "secret", "GET", "/tasks/1", b"", timestamp=1_700_000_000, nonce="abc")
    assert verify_request_signature("secret", "GET", "/tasks/1", b"", headers, now=1_700_001_000, record_nonce=lambda n: True) is False
    assert verify_request_signature("secret", "GET", "/tasks/1", b"", headers, now=1_700_000_001, record_nonce=lambda n: False) is False
