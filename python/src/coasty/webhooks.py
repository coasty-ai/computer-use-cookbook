"""Coasty webhook signature verification.

Scheme (run webhooks and trigger webhooks alike):

- Header: ``Coasty-Signature: t=<unix_ts>,v1=<hex>``
- Signed payload: ``b"<t>." + raw_body``
- ``v1 = hex(HMAC_SHA256(secret, signed_payload))``

Verification uses a constant-time compare and rejects timestamps outside the
tolerance window (default +/- 300 s, the documented replay window). A
malformed header returns ``False`` -- this function never raises.
"""

from __future__ import annotations

import hashlib
import hmac
import time

DEFAULT_TOLERANCE_SECONDS = 300


def compute_signature(raw_body: bytes, secret: str, *, timestamp: int) -> str:
    """Return the hex ``v1`` signature for a body + secret + timestamp."""
    signed_payload = f"{timestamp}.".encode() + raw_body
    return hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()


def build_signature_header(raw_body: bytes, secret: str, *, timestamp: int) -> str:
    """Build a ``Coasty-Signature`` header value (useful for tests/mocks)."""
    return f"t={timestamp},v1={compute_signature(raw_body, secret, timestamp=timestamp)}"


def verify_signature(
    raw_body: bytes,
    header: str,
    secret: str,
    *,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
    now: int | None = None,
) -> bool:
    """Verify a ``Coasty-Signature`` header against the raw request body.

    Returns ``True`` only when a ``v1`` signature matches (constant-time)
    AND the ``t`` timestamp is within ``tolerance_seconds`` of ``now``
    (stale *and* future timestamps are rejected). Malformed input returns
    ``False`` -- never raises.
    """
    if not isinstance(raw_body, bytes) or not isinstance(header, str) or not secret:
        return False

    timestamp_raw: str | None = None
    candidates: list[str] = []
    for part in header.split(","):
        key, sep, value = part.strip().partition("=")
        if not sep:
            return False  # malformed part (no '=')
        key = key.strip()
        value = value.strip()
        if key == "t":
            timestamp_raw = value
        elif key == "v1":
            candidates.append(value)
        # unknown keys (e.g. future v2=...) are ignored

    if timestamp_raw is None or not candidates:
        return False
    try:
        timestamp = int(timestamp_raw)
    except ValueError:
        return False

    current = int(time.time()) if now is None else now
    if abs(current - timestamp) > tolerance_seconds:
        return False

    expected = compute_signature(raw_body, secret, timestamp=timestamp)
    return any(hmac.compare_digest(expected, candidate) for candidate in candidates)
