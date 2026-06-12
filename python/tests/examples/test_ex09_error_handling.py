"""ex09: error matrix -- every typed exception, retry counts, recorded sleeps."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

import ex09_error_handling as ex09

BASE_URL = "https://coasty.ai/v1"
KEY = "sk-coasty-test-" + "0" * 48


# ── never-retried 4xx scenarios ────────────────────────────────────────────


def test_401_invalid_api_key(respx_router: respx.MockRouter, make_error: Any) -> None:
    route = respx_router.get(f"{BASE_URL}/models").mock(
        return_value=httpx.Response(
            401, json=make_error(code="INVALID_API_KEY", type="auth_error", request_id="req_401")
        )
    )
    result = ex09.scenario_invalid_api_key(BASE_URL, KEY)
    assert result.outcome == "raised"
    assert result.exception == "AuthenticationError"
    assert result.code == "INVALID_API_KEY"
    assert result.request_id == "req_401"
    assert result.status_code == 401
    assert result.attempts == 1 and route.call_count == 1  # 4xx: never retried
    assert result.slept == ()
    assert result.client_retried is False
    # the scenario used its deliberately-bad key, not ours
    assert route.calls.last.request.headers["X-API-Key"] == ex09.BAD_API_KEY


def test_403_insufficient_scope_shows_required_scope(
    respx_router: respx.MockRouter, make_error: Any
) -> None:
    respx_router.post(f"{BASE_URL}/machines/mch_demo/terminal").mock(
        return_value=httpx.Response(
            403,
            json=make_error(
                code="INSUFFICIENT_SCOPE",
                type="auth_error",
                request_id="req_403",
                required_scope="terminal:exec",
                current_scopes=["predict", "ground"],
            ),
        )
    )
    result = ex09.scenario_insufficient_scope(BASE_URL, KEY)
    assert result.exception == "InsufficientScopeError"
    assert result.code == "INSUFFICIENT_SCOPE"
    assert result.request_id == "req_403"
    assert result.attempts == 1
    assert "terminal:exec" in result.detail
    assert "predict" in result.detail


def test_402_insufficient_credits_prints_required_vs_balance_and_topup(
    respx_router: respx.MockRouter, make_error: Any
) -> None:
    respx_router.post(f"{BASE_URL}/predict").mock(
        return_value=httpx.Response(
            402,
            json=make_error(
                code="INSUFFICIENT_CREDITS",
                type="billing_error",
                request_id="req_402",
                required=120,
                balance=40,
            ),
        )
    )
    result = ex09.scenario_insufficient_credits(BASE_URL, KEY)
    assert result.exception == "InsufficientCreditsError"
    assert result.code == "INSUFFICIENT_CREDITS"
    assert result.request_id == "req_402"
    assert result.attempts == 1  # billing errors are never retried
    assert "required=120" in result.detail
    assert "balance=40" in result.detail
    assert "$0.80" in result.detail  # top-up suggestion: (120-40) cents


def test_422_validation_error(respx_router: respx.MockRouter, make_error: Any) -> None:
    respx_router.post(f"{BASE_URL}/predict").mock(
        return_value=httpx.Response(
            422,
            json=make_error(
                code="VALIDATION_ERROR",
                type="validation_error",
                request_id="req_422",
                details=[{"loc": ["instruction"], "msg": "must not be empty"}],
            ),
        )
    )
    result = ex09.scenario_validation_error(BASE_URL, KEY)
    assert result.exception == "ValidationError"
    assert result.code == "VALIDATION_ERROR"
    assert result.request_id == "req_422"
    assert result.attempts == 1


def test_404_run_not_found(respx_router: respx.MockRouter, make_error: Any) -> None:
    respx_router.get(f"{BASE_URL}/runs/run_does_not_exist").mock(
        return_value=httpx.Response(
            404,
            json=make_error(code="RUN_NOT_FOUND", type="not_found_error", request_id="req_404"),
        )
    )
    result = ex09.scenario_not_found(BASE_URL, KEY)
    assert result.exception == "NotFoundError"
    assert result.code == "RUN_NOT_FOUND"
    assert result.request_id == "req_404"
    assert result.attempts == 1


def test_409_not_awaiting_human(respx_router: respx.MockRouter, make_error: Any) -> None:
    respx_router.post(f"{BASE_URL}/runs/run_demo/resume").mock(
        return_value=httpx.Response(
            409,
            json=make_error(
                code="NOT_AWAITING_HUMAN",
                type="state_error",
                request_id="req_409",
                current_state="running",
                allowed_from=["awaiting_human"],
            ),
        )
    )
    result = ex09.scenario_not_awaiting_human(BASE_URL, KEY)
    assert result.exception == "ConflictError"
    assert result.code == "NOT_AWAITING_HUMAN"
    assert result.request_id == "req_409"
    assert result.attempts == 1
    assert "'running'" in result.detail
    assert "awaiting_human" in result.detail


# ── retried scenarios (sleeps recorded, never real; counts asserted) ───────


def test_429_retry_after_is_honored_then_recovers(
    respx_router: respx.MockRouter, make_error: Any, make_predict_response: Any
) -> None:
    route = respx_router.post(f"{BASE_URL}/predict").mock(
        side_effect=[
            httpx.Response(
                429,
                json=make_error(
                    code="RATE_LIMITED",
                    type="rate_limit_error",
                    request_id="req_429",
                    retry_after=2,
                ),
                headers={"Retry-After": "2"},
            ),
            httpx.Response(
                200,
                json=make_predict_response(),
                headers={"X-Coasty-Request-Id": "req_ok", "X-Credits-Charged": "5"},
            ),
        ]
    )
    result = ex09.scenario_rate_limited_recovers(BASE_URL, KEY)
    assert result.outcome == "recovered"
    assert result.exception is None
    assert route.call_count == 2
    assert result.attempts == 2
    assert result.client_retried is True
    assert result.slept == (2.0,)  # exactly the server's Retry-After, no jitter
    assert "req_ok" in result.detail  # request_id of the recovery surfaced


def test_5xx_retried_with_backoff_then_surfaced(
    respx_router: respx.MockRouter, make_error: Any
) -> None:
    server_error = make_error(code="INTERNAL_ERROR", type="server_error", request_id="req_500")
    route = respx_router.post(f"{BASE_URL}/predict").mock(
        side_effect=[
            httpx.Response(
                503,
                json=make_error(
                    code="UPSTREAM_UNAVAILABLE",
                    type="server_error",
                    request_id="req_503",
                    retry_after=1,
                ),
                headers={"Retry-After": "1"},
            ),
            httpx.Response(500, json=server_error),
            httpx.Response(500, json=server_error),
            httpx.Response(500, json=server_error),
        ]
    )
    result = ex09.scenario_server_errors_surface(BASE_URL, KEY)
    assert result.outcome == "raised"
    assert result.exception == "ServerError"
    assert result.code == "INTERNAL_ERROR"  # the LAST failure is what surfaces
    assert result.request_id == "req_500"
    assert route.call_count == 4  # max_attempts exhausted
    assert result.attempts == 4
    assert len(result.slept) == 3  # one sleep between each attempt
    assert result.slept[0] == 1.0  # 503's Retry-After honored exactly
    for delay in result.slept[1:]:  # then capped, jittered backoff
        assert 0.0 <= delay <= 8.0


def test_500_on_unsafe_post_is_not_retried(respx_router: respx.MockRouter, make_error: Any) -> None:
    route = respx_router.post(f"{BASE_URL}/runs").mock(
        return_value=httpx.Response(
            500,
            json=make_error(code="INTERNAL_ERROR", type="server_error", request_id="req_unsafe"),
        )
    )
    result = ex09.scenario_unsafe_post_not_retried(BASE_URL, KEY)
    assert result.exception == "ServerError"
    assert result.request_id == "req_unsafe"
    assert route.call_count == 1  # POST /runs without Idempotency-Key: NO retry
    assert result.attempts == 1
    assert result.slept == ()
    assert result.client_retried is False


# ── presentation + live-mode safety ────────────────────────────────────────


def test_format_results_renders_each_row_and_retry_marker() -> None:
    rows = [
        ex09.ScenarioResult(
            name="example",
            outcome="raised",
            exception="ServerError",
            code="INTERNAL_ERROR",
            request_id="req_x",
            status_code=500,
            attempts=4,
            slept=(1.0, 0.5, 0.25),
            detail="kaboom",
        )
    ]
    table = ex09.format_results(rows)
    assert "example" in table
    assert "ServerError" in table
    assert "INTERNAL_ERROR" in table
    assert "req_x" in table
    assert "4*" in table  # the retried marker


def test_catalog_covers_every_scenario_with_live_safety() -> None:
    catalog = ex09.format_catalog()
    for scenario in ex09.SCENARIOS:
        assert scenario.name in catalog
    assert "live-safe" in catalog
    assert "mock-only" in catalog


def test_main_against_production_only_prints_the_catalog(
    respx_router: respx.MockRouter, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = ex09.main([])  # default base URL == production
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "NOT fired at production" in out
    assert len(respx_router.calls) == 0  # nothing hit the network
