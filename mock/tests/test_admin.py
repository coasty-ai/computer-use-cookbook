"""/__mock__ control endpoints: reset determinism, config knobs, clock."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_reset_restores_pristine_deterministic_state(client: TestClient) -> None:
    body = {"machine_id": "m_1", "task": "t"}
    first = client.post("/v1/runs", json=body).json()["id"]
    reset = client.post("/__mock__/reset", json={"seed": 1234})
    assert reset.json() == {"status": "ok", "seed": 1234}
    assert client.get("/v1/runs").json()["data"] == []
    second = client.post("/v1/runs", json=body).json()["id"]
    assert first == second  # same seed -> same id sequence


def test_reset_with_new_seed_changes_ids(client: TestClient) -> None:
    body = {"machine_id": "m_1", "task": "t"}
    first = client.post("/v1/runs", json=body).json()["id"]
    client.post("/__mock__/reset", json={"seed": 999})
    second = client.post("/v1/runs", json=body).json()["id"]
    assert first != second


def test_config_roundtrip(client: TestClient) -> None:
    config = client.get("/__mock__/config").json()
    assert config["wallet_balance_cents"] == 10_000
    assert config["run_success_steps"] == 3
    assert config["clock_frozen"] is True

    updated = client.post(
        "/__mock__/config", json={"wallet_balance_cents": 42, "run_success_steps": 1}
    ).json()
    assert updated["wallet_balance_cents"] == 42
    assert updated["run_success_steps"] == 1

    # run_success_steps=1 means a run succeeds after a single step.
    run_id = client.post("/v1/runs", json={"machine_id": "m", "task": "t"}).json()["id"]
    client.get(f"/v1/runs/{run_id}")
    final = client.get(f"/v1/runs/{run_id}").json()
    assert final["status"] == "succeeded"
    assert final["steps_completed"] == 1


def test_clock_advance(client: TestClient) -> None:
    before = client.get("/__mock__/config").json()["clock_now"]
    client.post("/__mock__/config", json={"advance_clock_seconds": 3600})
    after = client.get("/__mock__/config").json()["clock_now"]
    assert after == before + 3600


def test_unknown_config_key_422(client: TestClient) -> None:
    response = client.post("/__mock__/config", json={"bogus": 1})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_bad_config_values_422(client: TestClient) -> None:
    assert client.post("/__mock__/config", json={"wallet_balance_cents": -1}).status_code == 422
    assert client.post("/__mock__/config", json={"deliver_webhooks": "yes"}).status_code == 422
    assert client.post("/__mock__/reset", json={"seed": "abc"}).status_code == 422
