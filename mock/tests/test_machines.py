"""Machines: sandbox provisioning, lifecycle 409s, actions, files, terminal."""

from __future__ import annotations

import base64
from typing import Any

from fastapi.testclient import TestClient

from helpers import LIVE_KEY, auth

JsonDict = dict[str, Any]


def _provision(client: TestClient, **overrides: Any) -> JsonDict:
    body: JsonDict = {"display_name": "invoice-bot"}
    body.update(overrides)
    response = client.post("/v1/machines", json=body)
    assert response.status_code == 200, response.text
    return dict(response.json())


def test_sandbox_provision_is_instant(client: TestClient) -> None:
    result = _provision(client, os_type="linux", desktop_enabled=True)
    machine = result["machine"]
    assert machine["id"].startswith("mch_test_")
    assert machine["status"] == "running"  # instant, no AWS
    assert machine["is_test"] is True
    assert machine["cpu_cores"] == 2 and machine["memory_gb"] == 4.0
    connection = result["connection"]
    assert connection["ssh_port"] == 22 and connection["has_ssh_key"] is True
    assert "ssh_private_key_pem" not in connection  # secrets only via /connection


def test_live_provision_gets_uuid_and_wallet_gate(client: TestClient) -> None:
    live = client.post(
        "/v1/machines", json={"display_name": "live-bot"}, headers=auth(LIVE_KEY)
    ).json()
    live_id = live["machine"]["id"]
    assert not live_id.startswith("mch_test_")
    assert len(live_id) == 36 and live_id.count("-") == 4  # UUID-shaped
    assert live["machine"]["is_test"] is False

    client.post("/__mock__/config", json={"wallet_balance_cents": 19})
    gated = client.post("/v1/machines", json={"display_name": "poor-bot"}, headers=auth(LIVE_KEY))
    assert gated.status_code == 402
    error = gated.json()["error"]
    assert error["code"] == "INSUFFICIENT_CREDITS"
    assert error["required"] == 20 and error["balance"] == 19


def test_mode_isolation(client: TestClient) -> None:
    machine_id = _provision(client)["machine"]["id"]
    response = client.get(f"/v1/machines/{machine_id}", headers=auth(LIVE_KEY))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "MACHINE_NOT_FOUND"


def test_machine_not_found(client: TestClient) -> None:
    response = client.get("/v1/machines/mch_test_nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "MACHINE_NOT_FOUND"


def test_lifecycle_and_invalid_state(client: TestClient) -> None:
    machine_id = _provision(client)["machine"]["id"]

    # start while running -> 409 INVALID_STATE with context
    response = client.post(f"/v1/machines/{machine_id}/start")
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "INVALID_STATE"
    assert error["current_state"] == "running"
    assert error["allowed_from"] == ["stopped"]

    assert client.post(f"/v1/machines/{machine_id}/restart").json()["status"] == "running"
    assert client.post(f"/v1/machines/{machine_id}/stop").json()["status"] == "stopped"
    assert client.post(f"/v1/machines/{machine_id}/stop").status_code == 409
    assert client.post(f"/v1/machines/{machine_id}/restart").status_code == 409
    assert client.post(f"/v1/machines/{machine_id}/start").json()["status"] == "running"

    terminated = client.delete(f"/v1/machines/{machine_id}")
    assert terminated.json()["status"] == "terminated"
    assert client.delete(f"/v1/machines/{machine_id}").status_code == 409
    assert client.post(f"/v1/machines/{machine_id}/start").status_code == 409
    assert client.get(f"/v1/machines/{machine_id}").json()["machine"]["status"] == "terminated"


def test_patch_ttl(client: TestClient) -> None:
    machine_id = _provision(client)["machine"]["id"]
    set_ttl = client.patch(f"/v1/machines/{machine_id}", json={"ttl_minutes": 60})
    assert set_ttl.json()["ttl_minutes"] == 60
    cleared = client.patch(f"/v1/machines/{machine_id}", json={"ttl_minutes": 0})
    assert cleared.json()["ttl_minutes"] is None
    assert client.patch(f"/v1/machines/{machine_id}", json={"ttl_minutes": 3}).status_code == 422
    assert client.patch(f"/v1/machines/{machine_id}", json={}).status_code == 422


def test_screenshot_is_a_real_png(client: TestClient) -> None:
    machine_id = _provision(client)["machine"]["id"]
    body = client.get(f"/v1/machines/{machine_id}/screenshot").json()
    raw = base64.b64decode(body["image_b64"])
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    assert body["mime_type"] == "image/png"
    assert body["width"] == 64 and body["height"] == 36
    assert len(body["image_b64"]) > 100  # reusable as a /v1/predict screenshot


def test_screenshot_requires_running(client: TestClient) -> None:
    machine_id = _provision(client)["machine"]["id"]
    client.post(f"/v1/machines/{machine_id}/stop")
    response = client.get(f"/v1/machines/{machine_id}/screenshot")
    assert response.status_code == 409
    assert response.json()["error"]["allowed_from"] == ["running"]


def test_actions_single_and_unknown(client: TestClient) -> None:
    machine_id = _provision(client)["machine"]["id"]
    ok = client.post(
        f"/v1/machines/{machine_id}/actions",
        json={"command": "click", "parameters": {"x": 512, "y": 340}},
    ).json()
    assert ok["success"] is True
    assert ok["result"] == {"success": True, "x": 512, "y": 340}
    assert ok["error"] is None and ok["duration_ms"] > 0
    assert ok["screenshot"] is None

    shot = client.post(f"/v1/machines/{machine_id}/actions", json={"command": "screenshot"})
    assert shot.json()["screenshot"] is not None

    unknown = client.post(f"/v1/machines/{machine_id}/actions", json={"command": "explode"})
    assert unknown.json()["success"] is False
    assert "Unknown command" in unknown.json()["error"]

    bad_timeout = client.post(
        f"/v1/machines/{machine_id}/actions", json={"command": "click", "timeout_ms": 1}
    )
    assert bad_timeout.status_code == 422


def test_batch_stop_on_error_and_limits(client: TestClient) -> None:
    machine_id = _provision(client)["machine"]["id"]
    batch = client.post(
        f"/v1/machines/{machine_id}/actions/batch",
        json={
            "steps": [
                {"command": "click", "parameters": {"x": 1, "y": 2}},
                {"command": "fail"},
                {"command": "type", "parameters": {"text": "never"}},
            ],
            "stop_on_error": True,
        },
    ).json()
    assert batch["completed_count"] == 1
    assert batch["failed_count"] == 1
    assert batch["aborted"] is True
    assert len(batch["results"]) == 2  # third step skipped

    keep_going = client.post(
        f"/v1/machines/{machine_id}/actions/batch",
        json={"steps": [{"command": "fail"}, {"command": "click"}], "stop_on_error": False},
    ).json()
    assert keep_going["completed_count"] == 1
    assert keep_going["failed_count"] == 1
    assert keep_going["aborted"] is False

    too_many = client.post(
        f"/v1/machines/{machine_id}/actions/batch",
        json={"steps": [{"command": "click"}] * 51},
    )
    assert too_many.status_code == 422


def test_terminal_echo(client: TestClient) -> None:
    machine_id = _provision(client, os_type="windows")["machine"]["id"]
    result = client.post(
        f"/v1/machines/{machine_id}/terminal", json={"command": "Get-ChildItem C:\\"}
    ).json()
    assert result["success"] is True and result["exit_code"] == 0
    assert "Get-ChildItem C:\\" in result["output"]
    assert "powershell" in result["output"]
    assert result["session_id"].startswith("term_")
    assert client.post(f"/v1/machines/{machine_id}/terminal", json={}).status_code == 422


def test_files_ops(client: TestClient) -> None:
    machine_id = _provision(client)["machine"]["id"]
    base = f"/v1/machines/{machine_id}/files"
    write = client.post(
        f"{base}/write", json={"parameters": {"path": "/tmp/a.txt", "content": "hello"}}
    ).json()
    assert write["success"] is True
    client.post(f"{base}/append", json={"parameters": {"path": "/tmp/a.txt", "content": " world"}})
    read = client.post(f"{base}/read", json={"parameters": {"path": "/tmp/a.txt"}}).json()
    assert read["result"]["content"] == "hello world"
    exists = client.post(f"{base}/exists", json={"parameters": {"path": "/tmp/a.txt"}}).json()
    assert exists["result"]["exists"] is True
    listing = client.post(f"{base}/list", json={"parameters": {"path": "/tmp"}}).json()
    assert listing["result"]["entries"] == ["/tmp/a.txt"]
    download = client.post(f"{base}/download", json={"parameters": {"path": "/tmp/a.txt"}}).json()
    assert base64.b64decode(download["result"]["content_b64"]) == b"hello world"
    deleted = client.post(f"{base}/delete", json={"parameters": {"path": "/tmp/a.txt"}}).json()
    assert deleted["success"] is True
    missing = client.post(f"{base}/read", json={"parameters": {"path": "/tmp/a.txt"}}).json()
    assert missing["success"] is False and "not found" in missing["error"]
    unknown_op = client.post(f"{base}/transmogrify", json={"parameters": {}})
    assert unknown_op.status_code == 404


def test_browser_ops(client: TestClient) -> None:
    machine_id = _provision(client)["machine"]["id"]
    nav = client.post(
        f"/v1/machines/{machine_id}/browser/navigate",
        json={"parameters": {"url": "https://example.com"}},
    ).json()
    assert nav["success"] is True
    assert nav["result"]["url"] == "https://example.com"
    tabs = client.post(f"/v1/machines/{machine_id}/browser/list-tabs", json={}).json()
    assert tabs["result"]["tabs"]
    unknown = client.post(f"/v1/machines/{machine_id}/browser/teleport", json={})
    assert unknown.status_code == 404


def test_snapshot_costs_one_credit_and_idempotency(client: TestClient) -> None:
    machine_id = client.post(
        "/v1/machines", json={"display_name": "snap-bot"}, headers=auth(LIVE_KEY)
    ).json()["machine"]["id"]
    headers = {**auth(LIVE_KEY), "Idempotency-Key": "snap-1"}
    first = client.post(f"/v1/machines/{machine_id}/snapshot", json={}, headers=headers)
    assert first.json()["snapshot_id"].startswith("snap_")
    assert first.json()["credits_charged"] == 1
    assert first.headers["X-Credits-Charged"] == "1"
    replay = client.post(f"/v1/machines/{machine_id}/snapshot", json={}, headers=headers)
    assert replay.headers["X-Coasty-Idempotent-Replay"] == "true"
    assert replay.json()["snapshot_id"] == first.json()["snapshot_id"]


def test_connection_returns_secrets_with_no_store(client: TestClient) -> None:
    machine_id = _provision(client)["machine"]["id"]
    response = client.get(f"/v1/machines/{machine_id}/connection")
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert "OPENSSH PRIVATE KEY" in body["ssh_private_key_pem"]
    assert body["vnc_password"]
    assert body["websocket_url"].startswith("ws://")


def test_pricing_endpoint(client: TestClient) -> None:
    body = client.get("/v1/machines/pricing").json()
    assert body["rates"]["running_linux"] == 5
    assert body["rates"]["running_windows"] == 9
    assert body["rates"]["stopped"] == 1
    assert body["rates"]["terminated"] == 0
    assert body["snapshot_credits"] == 1
    assert body["provision_gate_credits"] == 20


def test_list_machines(client: TestClient) -> None:
    first = _provision(client)["machine"]["id"]
    second = _provision(client, display_name="bot-2")["machine"]["id"]
    listed = client.get("/v1/machines").json()
    assert [machine["id"] for machine in listed["data"]] == [first, second]
    assert client.get("/v1/machines?limit=999").status_code == 400


def test_provision_idempotency(client: TestClient) -> None:
    headers = {"Idempotency-Key": "provision-bot-001"}
    body = {"display_name": "invoice-bot"}
    first = client.post("/v1/machines", json=body, headers=headers)
    replay = client.post("/v1/machines", json=body, headers=headers)
    assert replay.headers["X-Coasty-Idempotent-Replay"] == "true"
    assert replay.json()["machine"]["id"] == first.json()["machine"]["id"]
    conflict = client.post("/v1/machines", json={"display_name": "other-bot"}, headers=headers)
    assert conflict.status_code == 422
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
