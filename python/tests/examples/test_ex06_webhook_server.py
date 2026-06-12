"""ex06: webhook receiver -- shared HMAC vectors + a real loopback server test.

The vectors come verbatim from docs/API_NOTES.md ("Shared HMAC test vectors")
and are used across every language track.
"""

from __future__ import annotations

import http.client
import json
import threading
import time

from coasty import build_signature_header
from ex06_webhook_server import handle_webhook, make_server

# ── shared vectors (docs/API_NOTES.md) ─────────────────────────────────────

V1_SECRET = "whsec_test_secret_123"
V1_T = 1750000000
V1_BODY = b'{"event":"run.succeeded","run_id":"run_123","status":"succeeded"}'
V1_SIG = "5f70978eab52dbf5838da76e5eb6c6c465560ce8e746ed8e6113c159d8bbb2d4"
V1_HEADER = f"t={V1_T},v1={V1_SIG}"

V2_SECRET = "whsec_other_secret_456"
V2_T = 1750000300
V2_BODY = b'{"event":"run.awaiting_human","run_id":"run_456","reason":"captcha"}'
V2_SIG = "844504f42b7498094a83cedd7e050fc2f7fa32593b0814cc514c4be52a932e63"
V2_HEADER = f"t={V2_T},v1={V2_SIG}"


def _headers(value: str) -> dict[str, str]:
    return {"Coasty-Signature": value, "Content-Type": "application/json"}


# ── valid deliveries ───────────────────────────────────────────────────────


def test_vector1_valid_dispatches_succeeded_handler() -> None:
    status, action = handle_webhook(V1_BODY, _headers(V1_HEADER), V1_SECRET, now=V1_T)
    assert status == 200
    assert "run.succeeded" in action
    assert "run_123" in action


def test_vector2_valid_awaiting_human_says_where_to_resume() -> None:
    status, action = handle_webhook(V2_BODY, _headers(V2_HEADER), V2_SECRET, now=V2_T)
    assert status == 200
    assert action.startswith("resume_required")
    assert "run_456" in action
    assert "captcha" in action
    assert "runs.resume" in action  # points the operator at POST /runs/{id}/resume


def test_header_lookup_is_case_insensitive() -> None:
    headers = {"coasty-signature": V1_HEADER}
    status, _ = handle_webhook(V1_BODY, headers, V1_SECRET, now=V1_T)
    assert status == 200


def test_unknown_event_is_acknowledged_but_ignored() -> None:
    body = b'{"event":"run.started","run_id":"run_123"}'
    header = build_signature_header(body, V1_SECRET, timestamp=V1_T)
    status, action = handle_webhook(body, _headers(header), V1_SECRET, now=V1_T)
    assert status == 200
    assert action.startswith("ignored")


def test_each_terminal_event_has_a_handler() -> None:
    for event in ("run.failed", "run.cancelled", "run.timed_out"):
        body = json.dumps({"event": event, "run_id": "run_77"}).encode()
        header = build_signature_header(body, V1_SECRET, timestamp=V1_T)
        status, action = handle_webhook(body, _headers(header), V1_SECRET, now=V1_T)
        assert status == 200
        assert event in action
        assert "run_77" in action


# ── rejections: tampered / stale / malformed / wrong secret ────────────────


def test_tampered_body_is_rejected_401() -> None:
    tampered = V1_BODY.replace(b"run_123", b"run_999")
    status, action = handle_webhook(tampered, _headers(V1_HEADER), V1_SECRET, now=V1_T)
    assert status == 401
    assert action.startswith("rejected")


def test_stale_timestamp_is_rejected_401() -> None:
    status, _ = handle_webhook(V1_BODY, _headers(V1_HEADER), V1_SECRET, now=V1_T + 301)
    assert status == 401


def test_future_timestamp_is_rejected_401() -> None:
    status, _ = handle_webhook(V1_BODY, _headers(V1_HEADER), V1_SECRET, now=V1_T - 301)
    assert status == 401


def test_timestamp_at_tolerance_edge_is_accepted() -> None:
    status, _ = handle_webhook(V1_BODY, _headers(V1_HEADER), V1_SECRET, now=V1_T + 300)
    assert status == 200


def test_malformed_headers_are_rejected_401() -> None:
    for malformed in (f"t={V1_T}", f"v1={V1_SIG}", "nonsense", "", "t=,v1="):
        status, action = handle_webhook(V1_BODY, _headers(malformed), V1_SECRET, now=V1_T)
        assert status == 401, malformed
        assert action.startswith("rejected")


def test_missing_signature_header_is_rejected_401() -> None:
    status, action = handle_webhook(V1_BODY, {"Content-Type": "application/json"}, V1_SECRET)
    assert status == 401
    assert "Coasty-Signature" in action


def test_signature_from_other_secret_is_rejected_401() -> None:
    # vector 1's body signed with vector 2's secret must not verify
    status, _ = handle_webhook(V1_BODY, _headers(V1_HEADER), V2_SECRET, now=V1_T)
    assert status == 401


def test_valid_signature_over_non_json_body_is_400() -> None:
    body = b"definitely not json"
    header = build_signature_header(body, V1_SECRET, timestamp=V1_T)
    status, action = handle_webhook(body, _headers(header), V1_SECRET, now=V1_T)
    assert status == 400
    assert "not valid JSON" in action


def test_valid_signature_over_json_array_is_400() -> None:
    body = b'["not", "an", "object"]'
    header = build_signature_header(body, V1_SECRET, timestamp=V1_T)
    status, _ = handle_webhook(body, _headers(header), V1_SECRET, now=V1_T)
    assert status == 400


# ── the one real-socket test: loopback 127.0.0.1, ephemeral port ───────────


def _post(port: int, body: bytes, headers: dict[str, str]) -> tuple[int, dict[str, object]]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("POST", "/hooks/coasty", body=body, headers=headers)
        response = conn.getresponse()
        payload = json.loads(response.read())
        assert isinstance(payload, dict)
        return response.status, payload
    finally:
        conn.close()


def test_loopback_server_verifies_real_requests() -> None:
    server = make_server("127.0.0.1", 0, V1_SECRET)  # port 0 = ephemeral
    port = server.server_address[1]
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    thread.start()
    try:
        timestamp = int(time.time())  # the server checks against real time here
        header = build_signature_header(V1_BODY, V1_SECRET, timestamp=timestamp)

        status, payload = _post(port, V1_BODY, _headers(header))
        assert status == 200
        assert payload["received"] is True
        action = payload["action"]
        assert isinstance(action, str) and "run_123" in action

        # same signature over a tampered body must 401
        tampered = V1_BODY.replace(b"succeeded", b"exfiltrated")
        status, payload = _post(port, tampered, _headers(header))
        assert status == 401
        assert payload["received"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
