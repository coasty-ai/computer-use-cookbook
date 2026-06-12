"""ex01: predict loop executes scaled actions, stops on done/fail, caps steps."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from coasty import CoastyClient, NullBackend, cost
from ex01_local_predict_loop import CaptureResult, build_estimate, run_predict_loop, spend_gate

BASE_URL = "https://coasty.ai/v1"
FAKE_SHOT = "QUJDREVGR0hJSktM" * 8  # >100 chars of plausible base64
FAKE_LIVE_KEY = "sk-coasty-live-" + "0" * 48  # obviously fake
FAKE_SANDBOX_KEY = "sk-coasty-test-" + "0" * 48


def fake_capture() -> CaptureResult:
    """A 2560x1440 screen downscaled to 1280x720 -> scale factor 2.0 each axis."""
    return CaptureResult(
        screenshot_b64=FAKE_SHOT,
        sent_width=1280,
        sent_height=720,
        real_width=2560,
        real_height=1440,
    )


def test_loop_executes_scaled_actions_and_stops_on_done(
    client: CoastyClient, respx_router: respx.MockRouter, make_predict_response: Any
) -> None:
    first = make_predict_response()  # status=continue, click(512, 340)
    second = make_predict_response(
        request_id="req_test_2",
        status="done",
        actions=[{"action_type": "done", "params": {}}],
    )
    route = respx_router.post(f"{BASE_URL}/predict").mock(
        side_effect=[httpx.Response(200, json=first), httpx.Response(200, json=second)]
    )
    backend = NullBackend()

    outcome = run_predict_loop(
        client, fake_capture, backend, "log in", max_steps=10, emit=lambda _: None
    )

    assert outcome.status == "done"
    assert [record.status for record in outcome.steps] == ["continue", "done"]
    assert [record.request_id for record in outcome.steps] == ["req_test_1", "req_test_2"]
    assert outcome.total_credits == 10
    assert route.call_count == 2
    # click(512, 340) was predicted in SENT (1280x720) space; the executor
    # scales it x2 onto the real 2560x1440 screen.
    assert backend.calls == [("click", {"x": 1024, "y": 680, "button": "left", "clicks": 1})]
    body = json.loads(route.calls[0].request.content)
    assert body["screenshot"] == FAKE_SHOT
    assert body["instruction"] == "log in"
    # the request advertises the DOWNSCALED size, matching the screenshot
    assert body["screen_width"] == 1280
    assert body["screen_height"] == 720


def test_loop_stops_on_fail(
    client: CoastyClient, respx_router: respx.MockRouter, make_predict_response: Any
) -> None:
    respx_router.post(f"{BASE_URL}/predict").mock(
        return_value=httpx.Response(
            200,
            json=make_predict_response(
                status="fail",
                actions=[{"action_type": "fail", "params": {"reason": "element not found"}}],
            ),
        )
    )
    backend = NullBackend()
    outcome = run_predict_loop(
        client, fake_capture, backend, "log in", max_steps=5, emit=lambda _: None
    )
    assert outcome.status == "fail"
    assert len(outcome.steps) == 1
    assert backend.calls == []  # the fail marker is a no-op, never executed


def test_loop_respects_max_steps(
    client: CoastyClient, respx_router: respx.MockRouter, make_predict_response: Any
) -> None:
    route = respx_router.post(f"{BASE_URL}/predict").mock(
        return_value=httpx.Response(200, json=make_predict_response())  # always "continue"
    )
    backend = NullBackend()
    outcome = run_predict_loop(
        client, fake_capture, backend, "log in", max_steps=3, emit=lambda _: None
    )
    assert outcome.status == "max_steps"
    assert len(outcome.steps) == 3
    assert route.call_count == 3
    assert len(backend.calls) == 3  # one click per step


def test_loop_rejects_non_positive_max_steps(client: CoastyClient) -> None:
    with pytest.raises(ValueError, match="max_steps"):
        run_predict_loop(client, fake_capture, NullBackend(), "x", max_steps=0)


def test_build_estimate_scales_with_max_steps() -> None:
    estimate = build_estimate(4, "v3")
    assert estimate.credits == 4 * cost.PREDICT_BASE_CREDITS  # 1280x720 is NOT HD
    assert estimate.usd == pytest.approx(0.20)


# ── spend gate (shared helper used by ex01/ex02/ex03/ex05) ─────────────────


def test_spend_gate_sandbox_proceeds_and_labels_zero_dollars() -> None:
    lines: list[str] = []
    allowed = spend_gate(
        build_estimate(2, "v3"), api_key=FAKE_SANDBOX_KEY, confirm=False, emit=lines.append
    )
    assert allowed is True
    assert any("$0 (sandbox)" in line for line in lines)


def test_spend_gate_blocks_live_key_without_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COASTY_CONFIRM_SPEND", raising=False)
    lines: list[str] = []
    allowed = spend_gate(
        build_estimate(2, "v3"), api_key=FAKE_LIVE_KEY, confirm=False, emit=lines.append
    )
    assert allowed is False
    assert any("predict base" in line for line in lines)  # itemized estimate was shown


def test_spend_gate_allows_live_key_with_confirm_flag() -> None:
    allowed = spend_gate(
        build_estimate(2, "v3"), api_key=FAKE_LIVE_KEY, confirm=True, emit=lambda _: None
    )
    assert allowed is True


def test_spend_gate_allows_live_key_with_env_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COASTY_CONFIRM_SPEND", "1")
    allowed = spend_gate(
        build_estimate(2, "v3"), api_key=FAKE_LIVE_KEY, confirm=False, emit=lambda _: None
    )
    assert allowed is True
