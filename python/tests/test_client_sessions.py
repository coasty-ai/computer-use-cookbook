"""Client contract tests: sessions create / predict / reset / get / list / delete."""

from __future__ import annotations

import json
from typing import Any

import httpx
import respx

from coasty import CoastyClient

BASE_URL = "https://coasty.ai/v1"
SCREENSHOT = "iVBORw0KGgo" * 20

SESSION_INFO = {
    "session_id": "sess_1",
    "cua_version": "v3",
    "screen_size": "1280x720",
    "step_count": 2,
    "created_at": "2026-06-01T12:00:00Z",
    "expires_at": "2026-06-01T13:00:00Z",
    "total_credits_used": 18,
}


def body_of(route: respx.Route) -> dict[str, Any]:
    return json.loads(route.calls.last.request.content)  # type: ignore[no-any-return]


def test_create_session_contract(client: CoastyClient, respx_router: respx.MockRouter) -> None:
    route = respx_router.post(f"{BASE_URL}/sessions").mock(
        return_value=httpx.Response(
            200,
            json={
                "session_id": "sess_1",
                "cua_version": "v3",
                "screen_size": "1280x720",
                "created_at": "2026-06-01T12:00:00Z",
                "expires_at": "2026-06-01T13:00:00Z",
            },
            headers={"X-Credits-Charged": "10"},
        )
    )
    result = client.create_session(
        cua_version="v3",
        screen_width=1280,
        screen_height=720,
        max_trajectory_length=5,
        instructions="be brief",
        metadata={"suite": "tests"},
    )
    assert body_of(route) == {
        "cua_version": "v3",
        "screen_width": 1280,
        "screen_height": 720,
        "max_trajectory_length": 5,
        "instructions": "be brief",
        "metadata": {"suite": "tests"},
    }
    assert result.data["session_id"] == "sess_1"
    assert result.credits_charged == 10


def test_session_predict_contract(
    client: CoastyClient, respx_router: respx.MockRouter, make_predict_response: Any
) -> None:
    payload = make_predict_response(session_id="sess_1", step=3)
    route = respx_router.post(f"{BASE_URL}/sessions/sess_1/predict").mock(
        return_value=httpx.Response(200, json=payload, headers={"X-Credits-Charged": "4"})
    )
    result = client.session_predict(
        "sess_1",
        SCREENSHOT,
        "click next",
        include_reasoning=True,
        idempotency_key="step-3",
    )
    request = route.calls.last.request
    assert request.headers["Idempotency-Key"] == "step-3"
    assert body_of(route) == {
        "screenshot": SCREENSHOT,
        "instruction": "click next",
        "include_reasoning": True,
    }
    assert result.data["session_id"] == "sess_1"
    assert result.data["step"] == 3


def test_reset_session(client: CoastyClient, respx_router: respx.MockRouter) -> None:
    route = respx_router.post(f"{BASE_URL}/sessions/sess_1/reset").mock(
        return_value=httpx.Response(200, json={"status": "ok", "session_id": "sess_1"})
    )
    result = client.reset_session("sess_1")
    assert route.called
    assert result.data == {"status": "ok", "session_id": "sess_1"}


def test_get_session(client: CoastyClient, respx_router: respx.MockRouter) -> None:
    respx_router.get(f"{BASE_URL}/sessions/sess_1").mock(
        return_value=httpx.Response(200, json=SESSION_INFO)
    )
    result = client.get_session("sess_1")
    assert result.data["step_count"] == 2
    assert result.data["total_credits_used"] == 18


def test_list_sessions(client: CoastyClient, respx_router: respx.MockRouter) -> None:
    respx_router.get(f"{BASE_URL}/sessions").mock(
        return_value=httpx.Response(200, json={"sessions": [SESSION_INFO]})
    )
    result = client.list_sessions()
    assert [session["session_id"] for session in result.data["sessions"]] == ["sess_1"]


def test_delete_session_uses_delete_method(
    client: CoastyClient, respx_router: respx.MockRouter
) -> None:
    route = respx_router.delete(f"{BASE_URL}/sessions/sess_1").mock(
        return_value=httpx.Response(200, json={"status": "ok", "session_id": "sess_1"})
    )
    result = client.delete_session("sess_1")
    assert route.calls.last.request.method == "DELETE"
    assert result.data["status"] == "ok"
