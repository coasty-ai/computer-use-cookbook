"""Webhook signing: shared HMAC vectors, recorded deliveries, live loopback POST."""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from fastapi.testclient import TestClient

from coasty_mock.webhooks import compute_signature, is_local_url

JsonDict = dict[str, Any]

# Shared HMAC test vectors from docs/API_NOTES.md (identical in all languages).
VECTOR_1 = {
    "secret": "whsec_test_secret_123",
    "t": 1750000000,
    "body": b'{"event":"run.succeeded","run_id":"run_123","status":"succeeded"}',
    "v1": "5f70978eab52dbf5838da76e5eb6c6c465560ce8e746ed8e6113c159d8bbb2d4",
}
VECTOR_2 = {
    "secret": "whsec_other_secret_456",
    "t": 1750000300,
    "body": b'{"event":"run.awaiting_human","run_id":"run_456","reason":"captcha"}',
    "v1": "844504f42b7498094a83cedd7e050fc2f7fa32593b0814cc514c4be52a932e63",
}


def test_shared_hmac_vectors() -> None:
    for vector in (VECTOR_1, VECTOR_2):
        assert (
            compute_signature(str(vector["secret"]), int(vector["t"]), bytes(vector["body"]))
            == vector["v1"]
        )


def test_tampered_body_and_wrong_secret_reject() -> None:
    good = compute_signature(str(VECTOR_1["secret"]), int(VECTOR_1["t"]), bytes(VECTOR_1["body"]))
    tampered = bytes(VECTOR_1["body"]).replace(b"succeeded", b"failed", 1)
    assert compute_signature(str(VECTOR_1["secret"]), int(VECTOR_1["t"]), tampered) != good
    assert (
        compute_signature(str(VECTOR_2["secret"]), int(VECTOR_1["t"]), bytes(VECTOR_1["body"]))
        != good
    )


def test_is_local_url() -> None:
    assert is_local_url("http://127.0.0.1:9999/hook")
    assert is_local_url("http://localhost/hook")
    assert not is_local_url("https://example.com/hook")


def _verify(record: JsonDict, secret: str) -> bool:
    """Recompute the HMAC exactly as a webhook consumer would."""
    header = str(record["headers"]["Coasty-Signature"])
    parts = dict(part.split("=", 1) for part in header.split(","))
    signed = f"{parts['t']}.".encode() + str(record["body"]).encode()
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, parts["v1"])


def test_run_webhooks_signed_on_pause_and_terminal(client: TestClient) -> None:
    created = client.post(
        "/v1/runs",
        json={
            "machine_id": "m_1",
            "task": "do a thing [pause]",
            "webhook_url": "https://example.com/hooks/coasty",
        },
    ).json()
    secret = created["webhook_secret"]
    assert isinstance(secret, str) and secret.startswith("whsec_")
    run_id = created["id"]
    # Secret is returned exactly once: GET shows null.
    assert client.get(f"/v1/runs/{run_id}").json()["webhook_secret"] is None

    client.get(f"/v1/runs/{run_id}/events")  # advance to awaiting_human
    client.post(f"/v1/runs/{run_id}/resume", json={})
    client.get(f"/v1/runs/{run_id}/events")  # advance to terminal

    deliveries = client.get("/__mock__/webhooks").json()["deliveries"]
    events = [record["event"] for record in deliveries]
    assert events == ["run.awaiting_human", "run.succeeded"]
    for record in deliveries:
        assert record["url"] == "https://example.com/hooks/coasty"
        assert _verify(record, secret)
        payload = json.loads(record["body"])
        assert payload["run_id"] == run_id
    assert json.loads(deliveries[-1]["body"])["result"]["passed"] is True


def test_webhook_secret_is_deterministic_per_seed(client: TestClient) -> None:
    body = {
        "machine_id": "m_1",
        "task": "t",
        "webhook_url": "https://example.com/h",
    }
    first = client.post("/v1/runs", json=body).json()
    client.post("/__mock__/reset", json={"seed": 1234})
    second = client.post("/v1/runs", json=body).json()
    assert first["id"] == second["id"]
    assert first["webhook_secret"] == second["webhook_secret"]


class _Receiver(BaseHTTPRequestHandler):
    received: list[JsonDict] = []
    event = threading.Event()

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length", "0"))
        type(self).received.append(
            {
                "body": self.rfile.read(length),
                "signature": self.headers.get("Coasty-Signature", ""),
            }
        )
        self.send_response(200)
        self.end_headers()
        type(self).event.set()

    def log_message(self, fmt: str, *args: object) -> None:  # silence
        return


def test_loopback_delivery_actually_posts(client: TestClient) -> None:
    _Receiver.received = []
    _Receiver.event = threading.Event()
    server = HTTPServer(("127.0.0.1", 0), _Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        created = client.post(
            "/v1/runs",
            json={
                "machine_id": "m_1",
                "task": "notify me",
                "webhook_url": f"http://127.0.0.1:{port}/hook",
            },
        ).json()
        client.get(f"/v1/runs/{created['id']}/events")  # drive to terminal
        assert _Receiver.event.wait(timeout=5), "webhook was never delivered"
        record = _Receiver.received[0]
        parts = dict(part.split("=", 1) for part in record["signature"].split(","))
        signed = f"{parts['t']}.".encode() + record["body"]
        expected = hmac.new(
            str(created["webhook_secret"]).encode(), signed, hashlib.sha256
        ).hexdigest()
        assert hmac.compare_digest(expected, parts["v1"])
        assert json.loads(record["body"])["event"] == "run.succeeded"
        # The recorded delivery flips to delivered=True once the POST returns.
        deadline = time.monotonic() + 2
        delivered = False
        while time.monotonic() < deadline:
            deliveries = client.get("/__mock__/webhooks").json()["deliveries"]
            if deliveries and deliveries[0]["delivered"]:
                delivered = True
                break
            time.sleep(0.01)
        assert delivered
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_deliver_webhooks_can_be_disabled(client: TestClient) -> None:
    client.post("/__mock__/config", json={"deliver_webhooks": False})
    created = client.post(
        "/v1/runs",
        json={
            "machine_id": "m_1",
            "task": "t",
            "webhook_url": "http://127.0.0.1:1/hook",  # nothing listens; must not be hit
        },
    ).json()
    client.get(f"/v1/runs/{created['id']}/events")
    deliveries = client.get("/__mock__/webhooks").json()["deliveries"]
    assert len(deliveries) == 1  # still recorded
    assert deliveries[0]["delivered"] is False
    assert deliveries[0]["delivery_error"] is None  # no attempt was made
