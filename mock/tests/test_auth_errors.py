"""Auth middleware + the documented error envelope on every failure."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from helpers import LEGACY_KEY, LIVE_KEY, TEST_KEY, auth


def _assert_envelope(response: object, code: str) -> dict[str, object]:
    assert hasattr(response, "json") and hasattr(response, "headers")
    body = response.json()["error"]  # type: ignore[attr-defined]
    headers = response.headers  # type: ignore[attr-defined]
    assert body["code"] == code
    assert body["message"]
    assert body["type"]
    assert body["request_id"] == headers["X-Coasty-Request-Id"]
    return dict(body)


def test_missing_key_is_401_with_www_authenticate(client: TestClient) -> None:
    del client.headers["X-API-Key"]
    response = client.get("/v1/models")
    assert response.status_code == 401
    assert "WWW-Authenticate" in response.headers
    _assert_envelope(response, "INVALID_API_KEY")


@pytest.mark.parametrize(
    "bad_key",
    ["", "Bearer " + TEST_KEY, "sk-coasty-test-", "sk-other-test-abc", "not-a-key"],
)
def test_malformed_keys_are_401(client: TestClient, bad_key: str) -> None:
    response = client.get("/v1/models", headers={"X-API-Key": bad_key})
    assert response.status_code == 401
    _assert_envelope(response, "INVALID_API_KEY")


def test_bearer_authorization_header_works(client: TestClient) -> None:
    del client.headers["X-API-Key"]
    response = client.get("/v1/models", headers={"Authorization": f"Bearer {TEST_KEY}"})
    assert response.status_code == 200
    assert response.headers["X-Coasty-Key-Kind"] == "test"


def test_test_key_headers(client: TestClient) -> None:
    response = client.get("/v1/models")
    assert response.status_code == 200
    assert response.headers["X-Coasty-Test-Mode"] == "true"
    assert response.headers["X-Coasty-Key-Kind"] == "test"
    assert response.headers["X-Coasty-Request-Id"].startswith("req_")


def test_live_and_legacy_keys_have_no_test_mode(client: TestClient) -> None:
    for key, kind in ((LIVE_KEY, "live"), (LEGACY_KEY, "legacy")):
        response = client.get("/v1/models", headers=auth(key))
        assert response.status_code == 200
        assert response.headers["X-Coasty-Key-Kind"] == kind
        assert "X-Coasty-Test-Mode" not in response.headers


def test_every_response_has_request_id_and_ids_are_unique(client: TestClient) -> None:
    ids = set()
    for path in ("/v1/models", "/v1/usage", "/v1/nope", "/__mock__/config"):
        response = client.get(path)
        request_id = response.headers["X-Coasty-Request-Id"]
        assert request_id.startswith("req_")
        ids.add(request_id)
    assert len(ids) == 4


def test_unknown_route_is_not_found_envelope(client: TestClient) -> None:
    response = client.get("/v1/nope")
    assert response.status_code == 404
    _assert_envelope(response, "NOT_FOUND")


def test_invalid_json_body_is_validation_error(client: TestClient) -> None:
    response = client.post(
        "/v1/parse", content=b"{not json", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    _assert_envelope(response, "VALIDATION_ERROR")


FORCED = [
    ("INVALID_API_KEY", 401),
    ("INSUFFICIENT_SCOPE", 403),
    ("INSUFFICIENT_CREDITS", 402),
    ("VALIDATION_ERROR", 422),
    ("INVALID_SCREENSHOT", 422),
    ("IDEMPOTENCY_KEY_REUSED", 422),
    ("NOT_FOUND", 404),
    ("MACHINE_NOT_FOUND", 404),
    ("NOT_AWAITING_HUMAN", 409),
    ("INVALID_STATE", 409),
    ("RATE_LIMITED", 429),
    ("INTERNAL_ERROR", 500),
    ("UPSTREAM_UNAVAILABLE", 503),
    ("UPSTREAM_TIMEOUT", 504),
]


@pytest.mark.parametrize(("code", "status"), FORCED)
def test_force_error_header(client: TestClient, code: str, status: int) -> None:
    response = client.get("/v1/models", headers={"X-Mock-Force-Error": code})
    assert response.status_code == status
    error = _assert_envelope(response, code)
    if code == "INSUFFICIENT_SCOPE":
        assert error["required_scope"] == "predict"
        assert isinstance(error["current_scopes"], list)
    if code == "INSUFFICIENT_CREDITS":
        assert "required" in error and "balance" in error
    if code == "INVALID_STATE":
        assert "current_state" in error and "allowed_from" in error
    if code in {"RATE_LIMITED", "UPSTREAM_UNAVAILABLE"}:
        assert int(response.headers["Retry-After"]) >= 0
        assert "retry_after" in error
    if code == "INTERNAL_ERROR":
        assert error["type"] == "server_error"


def test_force_error_scope_reflects_route(client: TestClient) -> None:
    response = client.post(
        "/v1/runs", json={}, headers={"X-Mock-Force-Error": "INSUFFICIENT_SCOPE"}
    )
    assert response.json()["error"]["required_scope"] == "runs:write"


def test_force_error_unknown_code_is_validation_error(client: TestClient) -> None:
    response = client.get("/v1/models", headers={"X-Mock-Force-Error": "NOT_A_CODE"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_admin_routes_need_no_auth(client: TestClient) -> None:
    del client.headers["X-API-Key"]
    assert client.get("/__mock__/config").status_code == 200
    assert client.post("/__mock__/reset").status_code == 200
