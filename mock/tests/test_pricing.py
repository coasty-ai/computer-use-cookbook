"""Pricing known-answer tests (unit + endpoint level, incl. billing headers)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from coasty_mock.pricing import (
    ground_price,
    is_hd,
    predict_price,
    run_step_price,
    session_predict_price,
)
from helpers import LIVE_KEY, SCREENSHOT, auth


def test_hd_boundary_is_strict() -> None:
    assert not is_hd(1280, 720)  # exactly 1280x720 is NOT HD
    assert is_hd(1281, 720)
    assert is_hd(1280, 721)
    assert is_hd(1920, 1080)


def test_predict_price_known_answers() -> None:
    assert predict_price(width=1280, height=720) == 5
    assert predict_price(width=1920, height=1080) == 6  # +1 HD (the docs example)
    # +2/trajectory shot, +1 HD on current AND each trajectory shot
    assert predict_price(width=1920, height=1080, trajectory_screenshots=2) == 5 + 4 + 3
    assert predict_price(width=1280, height=720, cua_version="v1") == 8
    assert predict_price(width=1280, height=720, system_prompt="x" * 500) == 5  # exactly 500 free
    assert predict_price(width=1280, height=720, system_prompt="x" * 501) == 6
    assert (
        predict_price(
            width=1920,
            height=1080,
            trajectory_screenshots=2,
            cua_version="v1",
            system_prompt="x" * 501,
        )
        == 5 + 4 + 3 + 3 + 1
    )


def test_ground_and_session_and_run_step_prices() -> None:
    assert ground_price(width=1280, height=720) == 3
    assert ground_price(width=1920, height=1080) == 4
    assert session_predict_price(width=1280, height=720) == 4
    assert session_predict_price(width=1280, height=720, trajectory_screenshots=3) == 10
    assert run_step_price("v3") == 5
    assert run_step_price("v4") == 5
    assert run_step_price("v1") == 8


def _predict_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "screenshot": SCREENSHOT,
        "instruction": "Click the login button",
        "screen_width": 1280,
        "screen_height": 720,
    }
    body.update(overrides)
    return body


def test_live_key_is_billed_with_headers(client: TestClient) -> None:
    response = client.post("/v1/predict", json=_predict_body(), headers=auth(LIVE_KEY))
    assert response.status_code == 200
    assert response.headers["X-Credits-Charged"] == "5"
    assert response.headers["X-Credits-Remaining"] == "9995"
    assert response.json()["usage"]["credits_charged"] == 5
    assert response.json()["usage"]["cost_cents"] == 5


def test_test_key_bills_zero_but_reports_nominal_usage(client: TestClient) -> None:
    response = client.post("/v1/predict", json=_predict_body())
    assert response.headers["X-Credits-Charged"] == "0"
    assert response.headers["X-Credits-Remaining"] == "10000"
    # usage still reports the nominal price so cost estimators can be validated
    assert response.json()["usage"]["credits_charged"] == 5


def test_predict_surcharges_at_endpoint_level(client: TestClient) -> None:
    trajectory = [{"screenshot": SCREENSHOT, "actions": [], "reasoning": "r"}] * 2
    response = client.post(
        "/v1/predict",
        json=_predict_body(
            screen_width=1920,
            screen_height=1080,
            trajectory=trajectory,
            cua_version="v1",
            system_prompt="x" * 501,
        ),
        headers=auth(LIVE_KEY),
    )
    assert response.json()["usage"]["credits_charged"] == 16
    assert response.headers["X-Credits-Charged"] == "16"


def test_ground_hd_surcharge(client: TestClient) -> None:
    response = client.post(
        "/v1/ground",
        json={
            "screenshot": SCREENSHOT,
            "element": "the blue Submit button",
            "screen_width": 1920,
            "screen_height": 1080,
        },
        headers=auth(LIVE_KEY),
    )
    assert response.json()["usage"]["credits_charged"] == 4


def test_session_create_costs_ten_and_steps_add_trajectory(client: TestClient) -> None:
    created = client.post(
        "/v1/sessions",
        json={"screen_width": 1280, "screen_height": 720},
        headers=auth(LIVE_KEY),
    )
    assert created.headers["X-Credits-Charged"] == "10"
    session_id = created.json()["session_id"]
    step_body = {"screenshot": SCREENSHOT, "instruction": "Book a meeting"}
    first = client.post(
        f"/v1/sessions/{session_id}/predict", json=step_body, headers=auth(LIVE_KEY)
    )
    assert first.json()["usage"]["credits_charged"] == 4  # no trajectory yet
    second = client.post(
        f"/v1/sessions/{session_id}/predict", json=step_body, headers=auth(LIVE_KEY)
    )
    assert second.json()["usage"]["credits_charged"] == 6  # +2 for 1 kept screenshot


def test_parse_is_free_no_billing_headers(client: TestClient) -> None:
    response = client.post(
        "/v1/parse", json={"code": "pyautogui.click(1, 2)"}, headers=auth(LIVE_KEY)
    )
    assert response.status_code == 200
    assert "X-Credits-Charged" not in response.headers


def test_insufficient_credits_402(client: TestClient) -> None:
    assert client.post("/__mock__/config", json={"wallet_balance_cents": 3}).status_code == 200
    response = client.post("/v1/predict", json=_predict_body(), headers=auth(LIVE_KEY))
    assert response.status_code == 402
    error = response.json()["error"]
    assert error["code"] == "INSUFFICIENT_CREDITS"
    assert error["required"] == 5
    assert error["balance"] == 3


def test_usage_endpoint_accrues(client: TestClient) -> None:
    client.post("/v1/predict", json=_predict_body(), headers=auth(LIVE_KEY))
    usage = client.get("/v1/usage", headers=auth(LIVE_KEY)).json()
    assert usage["period"] == "2025-06"  # frozen clock epoch
    assert usage["total_credits"] == 5
    assert usage["total_cost_cents"] == 5
    assert usage["breakdown"]["predict"]["credits"] == 5
    assert usage["wallet_balance_cents"] == 9995
    assert usage["wallet_balance_usd"] == 99.95


def test_usage_bad_period_422(client: TestClient) -> None:
    response = client.get("/v1/usage?period=junk")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
