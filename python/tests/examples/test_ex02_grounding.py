"""ex02: grounds an element description, then clicks at REAL (scaled) coords."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from test_ex01_local_predict_loop import FAKE_SHOT, fake_capture

from coasty import CoastyClient, NullBackend, ValidationError
from ex02_grounding import build_estimate, ground_and_click

BASE_URL = "https://coasty.ai/v1"


def test_grounds_then_clicks_at_scaled_coords(
    client: CoastyClient, respx_router: respx.MockRouter, make_usage: Any
) -> None:
    route = respx_router.post(f"{BASE_URL}/ground").mock(
        return_value=httpx.Response(
            200,
            json={"x": 640, "y": 360, "usage": make_usage(credits_charged=3, cost_cents=3)},
            headers={"X-Coasty-Request-Id": "req_ground_1"},
        )
    )
    backend = NullBackend()

    outcome = ground_and_click(
        client, fake_capture, backend, "the blue Login button", emit=lambda _: None
    )

    body = json.loads(route.calls.last.request.content)
    assert body == {
        "screenshot": FAKE_SHOT,
        "element": "the blue Login button",
        "screen_width": 1280,  # matches the downscaled screenshot
        "screen_height": 720,
    }
    # (640, 360) in sent 1280x720 space -> x2 onto the real 2560x1440 screen
    assert backend.calls == [("click", {"x": 1280, "y": 720, "button": "left", "clicks": 1})]
    assert (outcome.sent_x, outcome.sent_y) == (640, 360)
    assert (outcome.real_x, outcome.real_y) == (1280, 720)
    assert outcome.request_id == "req_ground_1"
    assert outcome.credits_charged == 3


def test_ground_error_surfaces_request_id(
    client: CoastyClient, respx_router: respx.MockRouter, make_error: Any
) -> None:
    respx_router.post(f"{BASE_URL}/ground").mock(
        return_value=httpx.Response(
            422,
            json=make_error(
                code="INVALID_SCREENSHOT", type="validation_error", request_id="req_bad_shot"
            ),
        )
    )
    backend = NullBackend()
    with pytest.raises(ValidationError) as exc_info:
        ground_and_click(client, fake_capture, backend, "anything", emit=lambda _: None)
    assert exc_info.value.code == "INVALID_SCREENSHOT"
    assert exc_info.value.request_id == "req_bad_shot"
    assert backend.calls == []  # nothing was clicked


def test_build_estimate_is_sd_ground() -> None:
    estimate = build_estimate()
    assert estimate.credits == 3  # no HD surcharge at exactly 1280x720
