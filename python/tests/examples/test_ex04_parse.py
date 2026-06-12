"""ex04: /parse (free) -> structured actions, pretty-printed + NullBackend dry-run."""

from __future__ import annotations

import json
from typing import Any

import httpx
import respx

from coasty import CoastyClient, NullBackend
from coasty.cost import estimate_parse
from ex04_parse import DEFAULT_CODE, parse_and_dry_run

BASE_URL = "https://coasty.ai/v1"

PARSED_ACTIONS: list[dict[str, Any]] = [
    {"action_type": "click", "params": {"x": 640, "y": 360}},
    {"action_type": "type_text", "params": {"text": "hello coasty"}},
    {"action_type": "key_press", "params": {"key": "enter"}},  # canonical {key} shape
    {"action_type": "scroll", "params": {"clicks": -3}},  # alt signed-clicks shape
]


def test_parses_and_dry_runs_round_trip(
    client: CoastyClient, respx_router: respx.MockRouter
) -> None:
    route = respx_router.post(f"{BASE_URL}/parse").mock(
        return_value=httpx.Response(
            200,
            json={"actions": PARSED_ACTIONS},
            headers={"X-Coasty-Request-Id": "req_parse_1"},
        )
    )
    backend = NullBackend()
    emitted: list[str] = []

    outcome = parse_and_dry_run(client, DEFAULT_CODE, backend=backend, emit=emitted.append)

    assert json.loads(route.calls.last.request.content) == {"code": DEFAULT_CODE}
    assert outcome.request_id == "req_parse_1"
    assert outcome.executed == ["click", "type_text", "key_press", "scroll"]
    # the dry-run recorded the calls (no scaling: parse coords are real-screen);
    # the executor normalized BOTH documented param shapes
    assert outcome.backend_calls == [
        ("click", {"x": 640, "y": 360, "button": "left", "clicks": 1}),
        ("type_text", {"text": "hello coasty"}),
        ("key_press", {"keys": ["enter"]}),
        ("scroll", {"amount": 3, "direction": "down", "x": None, "y": None}),
    ]
    # the structured actions were pretty-printed
    assert json.loads(emitted[0]) == PARSED_ACTIONS


def test_dry_run_stops_after_done_marker(
    client: CoastyClient, respx_router: respx.MockRouter
) -> None:
    respx_router.post(f"{BASE_URL}/parse").mock(
        return_value=httpx.Response(
            200,
            json={
                "actions": [
                    {"action_type": "done", "params": {}},
                    {"action_type": "click", "params": {"x": 1, "y": 2}},  # unreachable
                ]
            },
        )
    )
    outcome = parse_and_dry_run(client, "pyautogui.click(1, 2)", emit=lambda _: None)
    assert outcome.executed == ["done"]
    assert outcome.backend_calls == []


def test_parse_is_free() -> None:
    estimate = estimate_parse()
    assert estimate.credits == 0
    assert estimate.usd == 0.0
