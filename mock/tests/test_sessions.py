"""Session lifecycle: create / predict / reset / get / list / delete / expiry."""

from __future__ import annotations

from fastapi.testclient import TestClient

from helpers import LIVE_KEY, SCREENSHOT, auth


def _create(client: TestClient) -> str:
    response = client.post("/v1/sessions", json={"screen_width": 1280, "screen_height": 720})
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"].startswith("sess_")
    assert body["screen_size"] == "1280x720"
    assert body["expires_at"] > body["created_at"]
    return str(body["session_id"])


def test_lifecycle(client: TestClient) -> None:
    session_id = _create(client)

    step = {"screenshot": SCREENSHOT, "instruction": "Book a meeting"}
    first = client.post(f"/v1/sessions/{session_id}/predict", json=step).json()
    assert first["session_id"] == session_id
    assert first["step"] == 1
    assert first["status"] == "continue"
    second = client.post(f"/v1/sessions/{session_id}/predict", json=step).json()
    assert second["step"] == 2

    info = client.get(f"/v1/sessions/{session_id}").json()
    assert info["step_count"] == 2
    assert info["total_credits_used"] == 10 + 4 + 6

    listed = client.get("/v1/sessions").json()["sessions"]
    assert [s["session_id"] for s in listed] == [session_id]

    reset = client.post(f"/v1/sessions/{session_id}/reset").json()
    assert reset == {"status": "ok", "session_id": session_id}
    assert client.get(f"/v1/sessions/{session_id}").json()["step_count"] == 0

    deleted = client.delete(f"/v1/sessions/{session_id}")
    assert deleted.json() == {"status": "ok", "session_id": session_id}
    assert client.get(f"/v1/sessions/{session_id}").status_code == 404


def test_session_not_found(client: TestClient) -> None:
    response = client.get("/v1/sessions/sess_nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SESSION_NOT_FOUND"


def test_mode_isolation(client: TestClient) -> None:
    session_id = _create(client)  # test key
    response = client.get(f"/v1/sessions/{session_id}", headers=auth(LIVE_KEY))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SESSION_NOT_FOUND"


def test_session_expiry(client: TestClient) -> None:
    session_id = _create(client)
    # Advance the frozen clock past the 1800s TTL.
    client.post("/__mock__/config", json={"advance_clock_seconds": 1801})
    response = client.get(f"/v1/sessions/{session_id}")
    assert response.status_code == 404
    assert "expired" in response.json()["error"]["message"]
    assert client.get("/v1/sessions").json()["sessions"] == []
    step = {"screenshot": SCREENSHOT, "instruction": "anything"}
    assert client.post(f"/v1/sessions/{session_id}/predict", json=step).status_code == 404


def test_session_done_counter_is_per_session(client: TestClient) -> None:
    first, second = _create(client), _create(client)
    step = {"screenshot": SCREENSHOT, "instruction": "click next"}
    for _ in range(2):
        assert client.post(f"/v1/sessions/{first}/predict", json=step).json()["status"] == (
            "continue"
        )
    # Third call on the SAME session goes done; a fresh session does not.
    assert client.post(f"/v1/sessions/{first}/predict", json=step).json()["status"] == "done"
    assert client.post(f"/v1/sessions/{second}/predict", json=step).json()["status"] == "continue"


def test_session_validation(client: TestClient) -> None:
    response = client.post("/v1/sessions", json={"max_trajectory_length": 21})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
