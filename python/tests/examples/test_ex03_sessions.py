"""ex03: session lifecycle -- per-step idempotency keys, info/reset, DELETE always."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from test_ex01_local_predict_loop import fake_capture

from coasty import CoastyClient, NullBackend, ValidationError
from ex03_sessions import build_estimate, run_session

BASE_URL = "https://coasty.ai/v1"
SESSION_ID = "sess_test_1"


def session_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "session_id": SESSION_ID,
        "cua_version": "v3",
        "screen_size": "1280x720",
        "created_at": "2026-06-01T12:00:00Z",
        "expires_at": "2026-06-01T13:00:00Z",
    }
    payload.update(overrides)
    return payload


def ack_payload() -> dict[str, Any]:
    return {"status": "ok", "session_id": SESSION_ID}


def test_full_lifecycle_with_per_step_idempotency_keys(
    client: CoastyClient, respx_router: respx.MockRouter, make_predict_response: Any
) -> None:
    create_route = respx_router.post(f"{BASE_URL}/sessions").mock(
        return_value=httpx.Response(201, json=session_payload())
    )
    step_base = {"session_id": SESSION_ID}
    predict_route = respx_router.post(f"{BASE_URL}/sessions/{SESSION_ID}/predict").mock(
        side_effect=[
            httpx.Response(
                200,
                json=make_predict_response(**step_base, step=1),  # continue + click(512, 340)
            ),
            httpx.Response(
                200,
                json=make_predict_response(
                    **step_base,
                    step=2,
                    status="done",
                    actions=[{"action_type": "done", "params": {}}],
                ),
            ),
        ]
    )
    info_route = respx_router.get(f"{BASE_URL}/sessions/{SESSION_ID}").mock(
        return_value=httpx.Response(
            200,
            json=session_payload(step_count=2, total_credits_used=18),
        )
    )
    reset_route = respx_router.post(f"{BASE_URL}/sessions/{SESSION_ID}/reset").mock(
        return_value=httpx.Response(200, json=ack_payload())
    )
    delete_route = respx_router.delete(f"{BASE_URL}/sessions/{SESSION_ID}").mock(
        return_value=httpx.Response(200, json=ack_payload())
    )
    backend = NullBackend()

    outcome = run_session(
        client, fake_capture, backend, "fill the form", max_steps=5, emit=lambda _: None
    )

    assert outcome.status == "done"
    assert outcome.session_id == SESSION_ID
    assert [record.step for record in outcome.steps] == [1, 2]
    assert outcome.info is not None
    assert outcome.info["total_credits_used"] == 18

    # create pinned the (downscaled) capture geometry
    create_body = json.loads(create_route.calls.last.request.content)
    assert create_body["screen_width"] == 1280
    assert create_body["screen_height"] == 720
    assert create_body["max_trajectory_length"] == 3

    # one UNIQUE Idempotency-Key per step (safe retries, no double-charging)
    keys = [call.request.headers["Idempotency-Key"] for call in predict_route.calls]
    assert keys == [f"{SESSION_ID}-step-1", f"{SESSION_ID}-step-2"]

    # the predicted click was executed at real-screen (x2) coordinates
    assert backend.calls == [("click", {"x": 1024, "y": 680, "button": "left", "clicks": 1})]

    assert info_route.call_count == 1
    assert reset_route.call_count == 1
    assert delete_route.call_count == 1  # exactly one DELETE


def test_session_is_deleted_even_when_a_step_raises(
    client: CoastyClient, respx_router: respx.MockRouter, make_error: Any
) -> None:
    respx_router.post(f"{BASE_URL}/sessions").mock(
        return_value=httpx.Response(201, json=session_payload())
    )
    respx_router.post(f"{BASE_URL}/sessions/{SESSION_ID}/predict").mock(
        return_value=httpx.Response(
            422,
            json=make_error(
                code="INVALID_SCREENSHOT", type="validation_error", request_id="req_boom"
            ),
        )
    )
    delete_route = respx_router.delete(f"{BASE_URL}/sessions/{SESSION_ID}").mock(
        return_value=httpx.Response(200, json=ack_payload())
    )

    with pytest.raises(ValidationError) as exc_info:
        run_session(client, fake_capture, NullBackend(), "boom", emit=lambda _: None)

    # the original error escapes (with its request_id) AND the finally block
    # still freed the session's concurrency slot
    assert exc_info.value.request_id == "req_boom"
    assert delete_route.call_count == 1


def test_delete_failure_does_not_mask_the_original_error(
    client: CoastyClient, respx_router: respx.MockRouter, make_error: Any
) -> None:
    respx_router.post(f"{BASE_URL}/sessions").mock(
        return_value=httpx.Response(201, json=session_payload())
    )
    respx_router.post(f"{BASE_URL}/sessions/{SESSION_ID}/predict").mock(
        return_value=httpx.Response(
            422, json=make_error(code="INVALID_SCREENSHOT", type="validation_error")
        )
    )
    delete_route = respx_router.delete(f"{BASE_URL}/sessions/{SESSION_ID}").mock(
        return_value=httpx.Response(
            404, json=make_error(code="SESSION_NOT_FOUND", type="not_found_error")
        )
    )
    warnings: list[str] = []

    with pytest.raises(ValidationError) as exc_info:  # NOT the delete's NotFoundError
        run_session(client, fake_capture, NullBackend(), "boom", emit=warnings.append)

    assert exc_info.value.code == "INVALID_SCREENSHOT"
    assert delete_route.call_count == 1
    assert any("failed to delete" in line for line in warnings)


def test_build_estimate_create_plus_steps() -> None:
    estimate = build_estimate(5, "v3")
    assert estimate.credits == 10 + 5 * 4  # create (flat) + 5 steps x 4 cr, SD
