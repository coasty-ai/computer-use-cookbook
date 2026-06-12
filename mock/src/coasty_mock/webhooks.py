"""Signed webhook emission (Coasty-Signature: t=...,v1=...).

Scheme (identical to the shared vectors in docs/API_NOTES.md):

    v1 = hex(HMAC_SHA256(webhook_secret, f"{t}." + raw_body))

Every emission is recorded in ``state.webhook_deliveries`` (inspect via
``GET /__mock__/webhooks``). Actual HTTP POSTs only happen when
``config.deliver_webhooks`` is true AND the URL host is local
(localhost / 127.0.0.1 / ::1) so the mock never touches the network.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import urllib.request
from typing import Any
from urllib.parse import urlsplit

from .clock import iso
from .state import TestState

JsonDict = dict[str, Any]

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def compute_signature(secret: str, t: int, raw_body: bytes) -> str:
    signed_payload = f"{t}.".encode() + raw_body
    return hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()


def is_local_url(url: str) -> bool:
    try:
        host = urlsplit(url).hostname
    except ValueError:  # pragma: no cover - malformed urls rejected upstream
        return False
    return host in _LOCAL_HOSTS


def _post(url: str, raw: bytes, headers: dict[str, str], record: JsonDict) -> None:
    try:
        request = urllib.request.Request(url, data=raw, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=2) as response:  # noqa: S310
            record["delivered"] = True
            record["response_status"] = response.status
    except Exception as exc:  # noqa: BLE001 - delivery is best-effort by design
        record["delivery_error"] = str(exc)


def emit_webhook(state: TestState, *, url: str, secret: str, payload: JsonDict) -> JsonDict:
    """Sign, record, and (for local URLs) deliver a webhook. Returns the record."""
    raw = json.dumps(payload, separators=(",", ":")).encode()
    t = int(state.clock.now())
    signature = compute_signature(secret, t, raw)
    record: JsonDict = {
        "url": url,
        "event": payload.get("event"),
        "body": raw.decode(),
        "headers": {
            "Content-Type": "application/json",
            "Coasty-Signature": f"t={t},v1={signature}",
        },
        "created_at": iso(state.clock.now()),
        "delivered": False,
        "delivery_error": None,
        "response_status": None,
    }
    state.webhook_deliveries.append(record)
    if state.config.deliver_webhooks and is_local_url(url):
        headers = dict(record["headers"])
        thread = threading.Thread(target=_post, args=(url, raw, headers, record), daemon=True)
        thread.start()
    return record
