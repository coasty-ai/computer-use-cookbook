"""Retry policy: backoff + jitter, Retry-After, and the POST safety guard.

The client fixture records sleeps instead of sleeping, so this whole file
runs in milliseconds and is fully deterministic (rng pinned to Random(42)).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from coasty import (
    CoastyClient,
    CoastyConnectionError,
    InsufficientCreditsError,
    RateLimitError,
    ServerError,
    ValidationError,
)

BASE_URL = "https://coasty.ai/v1"
SCREENSHOT = "iVBORw0KGgo" * 20


def test_retry_after_header_is_honored(
    client: CoastyClient,
    respx_router: respx.MockRouter,
    sleep_recorder: list[float],
    make_error: Any,
    make_run: Any,
) -> None:
    route = respx_router.get(f"{BASE_URL}/runs/run_1").mock(
        side_effect=[
            httpx.Response(
                429,
                json=make_error(code="RATE_LIMITED", type="rate_limit_error", retry_after=3),
                headers={"Retry-After": "3"},
            ),
            httpx.Response(200, json=make_run(id="run_1")),
        ]
    )
    result = client.get_run("run_1")
    assert result.data["id"] == "run_1"
    assert route.call_count == 2
    assert sleep_recorder == [3.0]  # exactly the Retry-After, not jittered backoff


def test_retry_after_falls_back_to_error_body(
    client: CoastyClient,
    respx_router: respx.MockRouter,
    sleep_recorder: list[float],
    make_error: Any,
    make_run: Any,
) -> None:
    respx_router.get(f"{BASE_URL}/runs/run_1").mock(
        side_effect=[
            httpx.Response(
                503,
                json=make_error(code="UPSTREAM_UNAVAILABLE", type="server_error", retry_after=2),
            ),
            httpx.Response(200, json=make_run(id="run_1")),
        ]
    )
    client.get_run("run_1")
    assert sleep_recorder == [2.0]


def test_500_get_is_retried_with_jittered_backoff(
    client: CoastyClient,
    respx_router: respx.MockRouter,
    sleep_recorder: list[float],
    make_run: Any,
) -> None:
    route = respx_router.get(f"{BASE_URL}/runs/run_1").mock(
        side_effect=[
            httpx.Response(500, json={"error": {"code": "INTERNAL_ERROR", "message": "x"}}),
            httpx.Response(500, json={"error": {"code": "INTERNAL_ERROR", "message": "x"}}),
            httpx.Response(200, json=make_run(id="run_1")),
        ]
    )
    client.get_run("run_1")
    assert route.call_count == 3
    assert len(sleep_recorder) == 2
    # full jitter: uniform(0, min(8, 0.5 * 2**(attempt-1)))
    assert 0.0 <= sleep_recorder[0] <= 0.5
    assert 0.0 <= sleep_recorder[1] <= 1.0


def test_attempts_are_capped_at_max(
    client: CoastyClient,
    respx_router: respx.MockRouter,
    sleep_recorder: list[float],
    make_error: Any,
) -> None:
    route = respx_router.get(f"{BASE_URL}/runs/run_1").mock(
        return_value=httpx.Response(
            429, json=make_error(code="RATE_LIMITED", type="rate_limit_error", retry_after=1)
        )
    )
    with pytest.raises(RateLimitError) as exc_info:
        client.get_run("run_1")
    assert route.call_count == 4  # max 4 attempts
    assert len(sleep_recorder) == 3
    assert exc_info.value.retry_after == 1.0


def test_402_is_never_retried_and_raises_typed_error(
    client: CoastyClient,
    respx_router: respx.MockRouter,
    sleep_recorder: list[float],
    make_error: Any,
) -> None:
    route = respx_router.post(f"{BASE_URL}/predict").mock(
        return_value=httpx.Response(
            402,
            json=make_error(
                code="INSUFFICIENT_CREDITS",
                message="Top up your wallet.",
                type="billing_error",
                request_id="req_402",
                required=25,
                balance=10,
            ),
        )
    )
    with pytest.raises(InsufficientCreditsError) as exc_info:
        client.predict(SCREENSHOT, "go")
    error = exc_info.value
    assert route.call_count == 1  # no retry on 402
    assert sleep_recorder == []
    assert error.required == 25
    assert error.balance == 10
    assert error.request_id == "req_402"


def test_422_is_never_retried(
    client: CoastyClient,
    respx_router: respx.MockRouter,
    make_error: Any,
) -> None:
    route = respx_router.post(f"{BASE_URL}/predict").mock(
        return_value=httpx.Response(422, json=make_error())
    )
    with pytest.raises(ValidationError):
        client.predict(SCREENSHOT, "go")
    assert route.call_count == 1


def test_safe_post_predict_is_retried_on_500(
    client: CoastyClient,
    respx_router: respx.MockRouter,
    make_predict_response: Any,
) -> None:
    route = respx_router.post(f"{BASE_URL}/predict").mock(
        side_effect=[
            httpx.Response(
                500, json={"error": {"code": "PREDICTION_FAILED", "message": "refunded"}}
            ),
            httpx.Response(200, json=make_predict_response()),
        ]
    )
    result = client.predict(SCREENSHOT, "go")
    assert route.call_count == 2
    assert result.data["status"] == "continue"


def test_unsafe_post_without_idempotency_key_is_not_retried(
    client: CoastyClient,
    respx_router: respx.MockRouter,
    sleep_recorder: list[float],
) -> None:
    route = respx_router.post(f"{BASE_URL}/runs").mock(
        return_value=httpx.Response(
            500, json={"error": {"code": "INTERNAL_ERROR", "message": "boom"}}
        )
    )
    with pytest.raises(ServerError):
        client.create_run("mch_test_1", "task")
    assert route.call_count == 1  # a duplicate run must never be created blindly
    assert sleep_recorder == []


def test_unsafe_post_with_idempotency_key_is_retried(
    client: CoastyClient,
    respx_router: respx.MockRouter,
    make_run: Any,
) -> None:
    route = respx_router.post(f"{BASE_URL}/runs").mock(
        side_effect=[
            httpx.Response(503, json={"error": {"code": "UPSTREAM_UNAVAILABLE", "message": "x"}}),
            httpx.Response(201, json=make_run()),
        ]
    )
    result = client.create_run("mch_test_a1b2c3d4", "task", idempotency_key="run-1")
    assert route.call_count == 2
    for call in route.calls:
        assert call.request.headers["Idempotency-Key"] == "run-1"
    assert result.data["id"] == "run_test_1"


def test_transport_errors_are_retried_then_wrapped(
    client: CoastyClient,
    respx_router: respx.MockRouter,
    sleep_recorder: list[float],
) -> None:
    route = respx_router.get(f"{BASE_URL}/models").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    with pytest.raises(CoastyConnectionError) as exc_info:
        client.models()
    assert route.call_count == 4
    assert len(sleep_recorder) == 3
    assert exc_info.value.code == "CONNECTION_ERROR"
    assert "GET /models" in exc_info.value.message


def test_transport_error_on_unsafe_post_is_not_retried(
    client: CoastyClient,
    respx_router: respx.MockRouter,
    sleep_recorder: list[float],
) -> None:
    route = respx_router.post(f"{BASE_URL}/workflows").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    with pytest.raises(CoastyConnectionError):
        client.create_workflow("n", "slug-1", {"steps": []})
    assert route.call_count == 1
    assert sleep_recorder == []


def test_transport_error_recovers_mid_sequence(
    client: CoastyClient,
    respx_router: respx.MockRouter,
    make_predict_response: Any,
) -> None:
    route = respx_router.post(f"{BASE_URL}/predict").mock(
        side_effect=[
            httpx.ConnectError("blip"),
            httpx.Response(200, json=make_predict_response()),
        ]
    )
    result = client.predict(SCREENSHOT, "go")
    assert route.call_count == 2
    assert result.data["status"] == "continue"


def test_404_is_not_retried(
    client: CoastyClient,
    respx_router: respx.MockRouter,
    sleep_recorder: list[float],
    make_error: Any,
) -> None:
    route = respx_router.get(f"{BASE_URL}/runs/run_x").mock(
        return_value=httpx.Response(
            404, json=make_error(code="RUN_NOT_FOUND", type="not_found_error")
        )
    )
    from coasty import NotFoundError

    with pytest.raises(NotFoundError):
        client.get_run("run_x")
    assert route.call_count == 1
    assert sleep_recorder == []


def test_504_retries_then_raises_server_error(
    client: CoastyClient,
    respx_router: respx.MockRouter,
    make_error: Any,
) -> None:
    route = respx_router.get(f"{BASE_URL}/usage").mock(
        return_value=httpx.Response(
            504, json=make_error(code="UPSTREAM_TIMEOUT", type="server_error")
        )
    )
    with pytest.raises(ServerError) as exc_info:
        client.usage()
    assert route.call_count == 4
    assert exc_info.value.code == "UPSTREAM_TIMEOUT"


def test_non_json_5xx_body_still_raises_server_error_with_request_id(
    client: CoastyClient,
    respx_router: respx.MockRouter,
) -> None:
    respx_router.post(f"{BASE_URL}/workflows").mock(
        return_value=httpx.Response(
            502,
            text="<html>Bad Gateway</html>",
            headers={"X-Coasty-Request-Id": "req_gateway"},
        )
    )
    with pytest.raises(ServerError) as exc_info:
        client.create_workflow("n", "slug-1", {"steps": []})
    assert exc_info.value.request_id == "req_gateway"
    assert "Bad Gateway" in exc_info.value.message
