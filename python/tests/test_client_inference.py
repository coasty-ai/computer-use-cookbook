"""Client contract tests: predict / ground / parse / models / usage (respx)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from coasty import CoastyClient, ValidationError

BASE_URL = "https://coasty.ai/v1"
FAKE_API_KEY = "sk-coasty-test-" + "0" * 48
SCREENSHOT = "iVBORw0KGgo" * 20  # >100 chars of fake base64, no data: prefix


def body_of(route: respx.Route) -> dict[str, Any]:
    request = route.calls.last.request
    return json.loads(request.content)  # type: ignore[no-any-return]


def test_predict_contract_and_result_headers(
    client: CoastyClient,
    respx_router: respx.MockRouter,
    make_predict_response: Any,
) -> None:
    route = respx_router.post(f"{BASE_URL}/predict").mock(
        return_value=httpx.Response(
            200,
            json=make_predict_response(),
            headers={
                "X-Coasty-Request-Id": "req_abc",
                "X-Credits-Charged": "6",
                "X-Credits-Remaining": "994",
            },
        )
    )
    result = client.predict(
        SCREENSHOT,
        "click the login button",
        cua_version="v3",
        screen_width=1280,
        screen_height=720,
        max_actions=3,
        system_prompt="Be precise.",
    )

    request = route.calls.last.request
    assert request.headers["X-API-Key"] == FAKE_API_KEY
    assert "Authorization" not in request.headers  # X-API-Key auth only
    assert "Idempotency-Key" not in request.headers
    assert body_of(route) == {
        "screenshot": SCREENSHOT,
        "instruction": "click the login button",
        "cua_version": "v3",
        "system_prompt": "Be precise.",
        "screen_width": 1280,
        "screen_height": 720,
        "max_actions": 3,
    }
    assert result.data["status"] == "continue"
    assert result.data["actions"][0]["action_type"] == "click"
    assert result.data["usage"]["credits_charged"] == 5
    assert result.request_id == "req_abc"
    assert result.credits_charged == 6
    assert result.credits_remaining == 994
    assert result.idempotent_replay is False


def test_predict_minimal_body_omits_optional_fields(
    client: CoastyClient,
    respx_router: respx.MockRouter,
    make_predict_response: Any,
) -> None:
    route = respx_router.post(f"{BASE_URL}/predict").mock(
        return_value=httpx.Response(200, json=make_predict_response())
    )
    client.predict(SCREENSHOT, "do nothing")
    assert body_of(route) == {"screenshot": SCREENSHOT, "instruction": "do nothing"}


@pytest.mark.parametrize("version", ["v1", "v3", "v4"])
def test_predict_passes_cua_version_literals(
    client: CoastyClient,
    respx_router: respx.MockRouter,
    make_predict_response: Any,
    version: str,
) -> None:
    route = respx_router.post(f"{BASE_URL}/predict").mock(
        return_value=httpx.Response(200, json=make_predict_response())
    )
    client.predict(SCREENSHOT, "go", cua_version=version)  # type: ignore[arg-type]
    assert body_of(route)["cua_version"] == version


def test_predict_sends_trajectory_and_tools(
    client: CoastyClient,
    respx_router: respx.MockRouter,
    make_predict_response: Any,
) -> None:
    route = respx_router.post(f"{BASE_URL}/predict").mock(
        return_value=httpx.Response(200, json=make_predict_response())
    )
    trajectory = [
        {
            "screenshot": SCREENSHOT,
            "actions": [{"action_type": "click", "params": {"x": 1, "y": 2}}],
            "reasoning": "clicked",
        }
    ]
    client.predict(
        SCREENSHOT,
        "continue",
        trajectory=trajectory,  # type: ignore[arg-type]
        tools=["click", "type_text"],
        include_reasoning=False,
        include_raw_code=False,
    )
    body = body_of(route)
    assert body["trajectory"] == trajectory
    assert body["tools"] == ["click", "type_text"]
    assert body["include_reasoning"] is False
    assert body["include_raw_code"] is False


def test_predict_validation_error_carries_request_id(
    client: CoastyClient,
    respx_router: respx.MockRouter,
    make_error: Any,
) -> None:
    respx_router.post(f"{BASE_URL}/predict").mock(
        return_value=httpx.Response(
            422,
            json=make_error(
                code="INVALID_SCREENSHOT",
                message="screenshot is not valid base64",
                request_id="req_bad_shot",
            ),
        )
    )
    with pytest.raises(ValidationError) as exc_info:
        client.predict("short", "go")
    error = exc_info.value
    assert error.code == "INVALID_SCREENSHOT"
    assert error.request_id == "req_bad_shot"
    assert error.status_code == 422


def test_ground_contract(
    client: CoastyClient, respx_router: respx.MockRouter, make_usage: Any
) -> None:
    route = respx_router.post(f"{BASE_URL}/ground").mock(
        return_value=httpx.Response(
            200,
            json={"x": 640, "y": 360, "usage": make_usage(credits_charged=4, cost_cents=4)},
            headers={"X-Credits-Charged": "4"},
        )
    )
    result = client.ground(
        SCREENSHOT, "the blue Submit button", screen_width=1920, screen_height=1080
    )
    assert body_of(route) == {
        "screenshot": SCREENSHOT,
        "element": "the blue Submit button",
        "screen_width": 1920,
        "screen_height": 1080,
    }
    assert (result.data["x"], result.data["y"]) == (640, 360)
    assert result.credits_charged == 4


def test_parse_contract(client: CoastyClient, respx_router: respx.MockRouter) -> None:
    route = respx_router.post(f"{BASE_URL}/parse").mock(
        return_value=httpx.Response(
            200,
            json={"actions": [{"action_type": "click", "params": {"x": 5, "y": 6}}]},
            headers={"X-Credits-Charged": "0"},
        )
    )
    result = client.parse("pyautogui.click(5, 6)")
    assert body_of(route) == {"code": "pyautogui.click(5, 6)"}
    assert result.data["actions"][0]["params"] == {"x": 5, "y": 6}
    assert result.credits_charged == 0  # parse is free


def test_models(client: CoastyClient, respx_router: respx.MockRouter) -> None:
    respx_router.get(f"{BASE_URL}/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "models": [{"id": "coasty-cua-v3"}],
                "cua_versions": [{"id": "v3", "default": True}],
                "action_types": ["click", "type_text"],
            },
        )
    )
    result = client.models()
    assert result.data["action_types"] == ["click", "type_text"]


def test_usage_with_period_param(client: CoastyClient, respx_router: respx.MockRouter) -> None:
    route = respx_router.get(f"{BASE_URL}/usage", params={"period": "2026-06"}).mock(
        return_value=httpx.Response(
            200,
            json={
                "period": "2026-06",
                "total_requests": 12,
                "total_credits": 60,
                "total_cost_cents": 60,
                "breakdown": {"predict": {"requests": 12, "credits": 60}},
                "balance": 940,
                "wallet_balance_cents": 940,
                "wallet_balance_usd": 9.40,
            },
        )
    )
    result = client.usage(period="2026-06")
    assert route.called
    assert result.data["total_credits"] == 60
    assert result.data["wallet_balance_usd"] == 9.40


def test_idempotent_replay_header_surfaces(
    client: CoastyClient, respx_router: respx.MockRouter, make_run: Any
) -> None:
    respx_router.post(f"{BASE_URL}/runs").mock(
        return_value=httpx.Response(
            200,
            json=make_run(),
            headers={"X-Coasty-Idempotent-Replay": "true"},
        )
    )
    result = client.create_run("mch_test_1", "task", idempotency_key="key-1")
    assert result.idempotent_replay is True


def test_client_context_manager_and_repr_masks_key(coasty_env: str) -> None:
    with CoastyClient(api_key=coasty_env) as managed:
        text = repr(managed)
        assert coasty_env not in text
        assert "sk-coasty-test" in text  # only a prefix is shown
        assert managed.is_sandbox is True


def test_client_requires_an_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from coasty import MissingAPIKeyError

    monkeypatch.delenv("COASTY_API_KEY", raising=False)
    with pytest.raises(MissingAPIKeyError):
        CoastyClient()


def test_base_url_trailing_slash_normalized(coasty_env: str) -> None:
    with CoastyClient(api_key=coasty_env, base_url="http://127.0.0.1:8787/v1/") as instance:
        assert instance.base_url == "http://127.0.0.1:8787/v1"
