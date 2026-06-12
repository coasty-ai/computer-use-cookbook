"""Run state machine, pause/resume, idempotency, billing, 409s."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from helpers import LIVE_KEY, auth

JsonDict = dict[str, Any]


def _create(
    client: TestClient,
    task: str = "download the invoice",
    headers: dict[str, str] | None = None,
    **overrides: Any,
) -> JsonDict:
    body: JsonDict = {"machine_id": "mch_test_abc123", "task": task}
    body.update(overrides)
    response = client.post("/v1/runs", json=body, headers=headers)
    assert response.status_code == 200, response.text
    return dict(response.json())


def _poll(client: TestClient, run_id: str, polls: int = 1, **kwargs: Any) -> JsonDict:
    run: JsonDict = {}
    for _ in range(polls):
        response = client.get(f"/v1/runs/{run_id}", **kwargs)
        assert response.status_code == 200
        run = dict(response.json())
    return run


def test_create_returns_queued_run_object(client: TestClient) -> None:
    run = _create(client, metadata={"team": "qa"})
    assert run["object"] == "agent.run"
    assert run["status"] == "queued"
    assert run["id"].startswith("run_")
    assert run["steps_completed"] == 0
    assert run["result"] is None and run["error"] is None
    assert run["webhook_secret"] is None  # only set when webhook_url is given
    assert run["metadata"] == {"team": "qa"}
    assert run["request_id"].startswith("req_")


def test_happy_path_succeeds_after_three_steps(client: TestClient) -> None:
    run = _create(client)
    run_id = run["id"]
    assert _poll(client, run_id)["status"] == "running"  # tick 1: queued -> running
    assert _poll(client, run_id)["steps_completed"] == 1
    assert _poll(client, run_id)["steps_completed"] == 2
    final = _poll(client, run_id)
    assert final["status"] == "succeeded"
    assert final["steps_completed"] == 3
    assert final["result"] == {
        "passed": True,
        "status": "succeeded",
        "summary": "Completed task in 3 steps.",
    }
    assert final["finished_at"] is not None
    # Terminal states are immutable: more polls change nothing.
    assert _poll(client, run_id) == final


def test_fail_marker(client: TestClient) -> None:
    run = _create(client, task="do the impossible [fail]")
    final = _poll(client, run["id"], polls=5)
    assert final["status"] == "failed"
    assert final["result"]["passed"] is False
    assert final["error"]["code"] == "TASK_FAILED"


def test_pause_resume_flow(client: TestClient) -> None:
    run = _create(client, task="login then [pause] for the captcha")
    run_id = run["id"]
    _poll(client, run_id)  # -> running
    paused = _poll(client, run_id)  # step 1 -> awaiting_human
    assert paused["status"] == "awaiting_human"
    assert paused["awaiting_human_reason"]
    assert paused["awaiting_human_since"] is not None
    assert paused["steps_completed"] == 1
    # Paused runs do not advance.
    assert _poll(client, run_id)["status"] == "awaiting_human"

    resumed = client.post(f"/v1/runs/{run_id}/resume", json={"note": "captcha solved"})
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "running"
    final = _poll(client, run_id, polls=3)
    assert final["status"] == "succeeded"
    assert final["steps_completed"] == 3


def test_on_awaiting_human_fail_and_cancel(client: TestClient) -> None:
    for behaviour, expected in (("fail", "failed"), ("cancel", "cancelled")):
        run = _create(client, task="[pause] now", on_awaiting_human=behaviour)
        final = _poll(client, run["id"], polls=3)
        assert final["status"] == expected
        assert final["error"]["code"] == "AWAITING_HUMAN"


def test_resume_conflicts(client: TestClient) -> None:
    run = _create(client)
    run_id = run["id"]
    # Not paused yet -> NOT_AWAITING_HUMAN
    response = client.post(f"/v1/runs/{run_id}/resume", json={})
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "NOT_AWAITING_HUMAN"
    assert error["current_state"] == "queued"
    assert error["allowed_from"] == ["awaiting_human"]
    # Terminal -> RESUME_CONFLICT
    _poll(client, run_id, polls=4)
    response = client.post(f"/v1/runs/{run_id}/resume", json={})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RESUME_CONFLICT"


def test_cancel(client: TestClient) -> None:
    run = _create(client)
    run_id = run["id"]
    cancelled = client.post(f"/v1/runs/{run_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    again = client.post(f"/v1/runs/{run_id}/cancel")
    assert again.status_code == 409
    error = again.json()["error"]
    assert error["code"] == "INVALID_STATE"
    assert error["current_state"] == "cancelled"


def test_deadline_times_out(client: TestClient) -> None:
    run = _create(client, deadline_seconds=1)
    final = _poll(client, run["id"], polls=2)
    assert final["status"] == "timed_out"
    assert final["error"]["code"] == "DEADLINE_EXCEEDED"


def test_idempotency_replay_and_reuse(client: TestClient) -> None:
    body = {"machine_id": "m_9f2c", "task": "reconcile the invoice"}
    headers = {"Idempotency-Key": "order-4821"}
    first = client.post("/v1/runs", json=body, headers=headers)
    assert "X-Coasty-Idempotent-Replay" not in first.headers
    second = client.post("/v1/runs", json=body, headers=headers)
    assert second.headers["X-Coasty-Idempotent-Replay"] == "true"
    assert second.json()["id"] == first.json()["id"]
    # Same key + different body -> IDEMPOTENCY_KEY_REUSED
    conflict = client.post("/v1/runs", json={**body, "task": "something else"}, headers=headers)
    assert conflict.status_code == 422
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_invalid_idempotency_key_format(client: TestClient) -> None:
    response = client.post(
        "/v1/runs",
        json={"machine_id": "m_1", "task": "t"},
        headers={"Idempotency-Key": "bad key with spaces"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_live_billing_per_step(client: TestClient) -> None:
    run = _create(client, headers=auth(LIVE_KEY))
    final = _poll(client, run["id"], polls=4, headers=auth(LIVE_KEY))
    assert final["status"] == "succeeded"
    assert final["credits_charged"] == 15  # 3 steps x 5 credits
    assert final["cost_cents"] == 15
    config = client.get("/__mock__/config").json()
    assert config["wallet_balance_cents"] == 10_000 - 15


def test_v1_engine_step_price(client: TestClient) -> None:
    run = _create(client, cua_version="v1", headers=auth(LIVE_KEY))
    final = _poll(client, run["id"], polls=4, headers=auth(LIVE_KEY))
    assert final["credits_charged"] == 24  # 3 steps x 8 credits


def test_wallet_gate_and_mid_run_exhaustion(client: TestClient) -> None:
    client.post("/__mock__/config", json={"wallet_balance_cents": 4})
    gated = client.post("/v1/runs", json={"machine_id": "m_1", "task": "t"}, headers=auth(LIVE_KEY))
    assert gated.status_code == 402
    assert gated.json()["error"]["code"] == "INSUFFICIENT_CREDITS"

    client.post("/__mock__/config", json={"wallet_balance_cents": 12})
    run = _create(client, headers=auth(LIVE_KEY))
    final = _poll(client, run["id"], polls=4, headers=auth(LIVE_KEY))
    assert final["status"] == "failed"
    assert final["error"]["code"] == "WALLET_EXHAUSTED"
    assert final["steps_completed"] == 2  # only completed steps stay billed
    assert final["credits_charged"] == 10


def test_list_and_filters(client: TestClient) -> None:
    first = _create(client)
    _create(client, task="[fail] quick")
    listed = client.get("/v1/runs").json()
    assert listed["object"] == "list"
    assert len(listed["data"]) == 2
    assert listed["has_more"] is False
    queued = client.get("/v1/runs?status=queued").json()
    assert {run["id"] for run in queued["data"]} >= {first["id"]}
    assert all(run["webhook_secret"] is None for run in listed["data"])

    bad_status = client.get("/v1/runs?status=zombie")
    assert bad_status.status_code == 400
    error = bad_status.json()["error"]
    assert error["code"] == "INVALID_STATUS_FILTER"
    assert "queued" in error["valid_options"]

    bad_limit = client.get("/v1/runs?limit=0")
    assert bad_limit.status_code == 400
    assert bad_limit.json()["error"]["code"] == "INVALID_LIMIT"


def test_run_not_found_and_mode_isolation(client: TestClient) -> None:
    missing = client.get("/v1/runs/run_nope")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "RUN_NOT_FOUND"
    run = _create(client)  # test mode
    live_view = client.get(f"/v1/runs/{run['id']}", headers=auth(LIVE_KEY))
    assert live_view.status_code == 404


def test_unknown_fields_rejected(client: TestClient) -> None:
    response = client.post("/v1/runs", json={"machine_id": "m_1", "task": "t", "bogus_field": 1})
    assert response.status_code == 422
    details = response.json()["error"]["details"]
    assert any(detail["loc"] == ["body", "bogus_field"] for detail in details)


def test_webhook_url_must_be_https_or_loopback(client: TestClient) -> None:
    response = client.post(
        "/v1/runs",
        json={"machine_id": "m_1", "task": "t", "webhook_url": "http://example.com/hook"},
    )
    assert response.status_code == 422
    assert (
        client.post(
            "/v1/runs",
            json={"machine_id": "m_1", "task": "t", "webhook_url": "http://127.0.0.1:9/hook"},
        ).status_code
        == 200
    )
