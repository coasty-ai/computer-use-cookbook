"""/v1/predict, /v1/ground, /v1/parse, /v1/models behaviour."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from helpers import SCREENSHOT


def _predict(client: TestClient, instruction: str, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {"screenshot": SCREENSHOT, "instruction": instruction}
    body.update(overrides)
    response = client.post("/v1/predict", json=body)
    assert response.status_code == 200, response.text
    return dict(response.json())


def test_default_action_is_click_center(client: TestClient) -> None:
    result = _predict(client, "Open the settings page")
    assert result["status"] == "continue"
    actions = result["actions"]
    assert isinstance(actions, list) and len(actions) == 1
    assert actions[0]["action_type"] == "click"
    assert actions[0]["params"] == {"x": 960, "y": 540}
    assert actions[0]["raw_code"].startswith("pyautogui.click(")
    assert result["raw_code"] == [actions[0]["raw_code"]]


def test_type_keyword_yields_click_then_type(client: TestClient) -> None:
    result = _predict(client, "type 'hello@example.com' into the email field")
    kinds = [a["action_type"] for a in result["actions"]]
    assert kinds == ["click", "type_text"]
    assert result["actions"][1]["params"]["text"] == "hello@example.com"


def test_scroll_keyword(client: TestClient) -> None:
    result = _predict(client, "scroll down to the footer")
    action = result["actions"][0]
    assert action["action_type"] == "scroll"
    assert action["params"]["direction"] == "down"
    result = _predict(client, "scroll up please")
    assert result["actions"][0]["params"]["direction"] == "up"


def test_done_and_fail_markers(client: TestClient) -> None:
    done = _predict(client, "verify the page loaded [done]")
    assert done["status"] == "done"
    assert done["actions"][0]["action_type"] == "done"
    failed = _predict(client, "[fail] impossible task")
    assert failed["status"] == "fail"
    assert failed["actions"][0]["action_type"] == "fail"


def test_same_instruction_goes_done_after_n_calls(client: TestClient) -> None:
    statuses = [_predict(client, "click the wizard next button")["status"] for _ in range(3)]
    assert statuses == ["continue", "continue", "done"]


def test_tools_filter_and_max_actions(client: TestClient) -> None:
    result = _predict(client, "type 'abc' in the box", tools=["type_text"], max_actions=5)
    assert [a["action_type"] for a in result["actions"]] == ["type_text"]
    result = _predict(client, "type 'abc' in the box", max_actions=1)
    assert len(result["actions"]) == 1


def test_include_flags(client: TestClient) -> None:
    result = _predict(client, "click it", include_reasoning=False, include_raw_code=False)
    assert result["reasoning"] is None
    assert result["raw_code"] == []
    assert all("raw_code" not in action for action in result["actions"])


@pytest.mark.parametrize(
    "screenshot",
    [
        "c2hvcnQ=",  # valid b64 but too short (must be > 100 chars)
        "data:image/png;base64," + SCREENSHOT,  # data: prefix
        "!" * 150,  # not base64
    ],
)
def test_invalid_screenshot_422(client: TestClient, screenshot: str) -> None:
    response = client.post("/v1/predict", json={"screenshot": screenshot, "instruction": "click"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_SCREENSHOT"


def test_missing_screenshot_is_validation_error(client: TestClient) -> None:
    response = client.post("/v1/predict", json={"instruction": "click"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_predict_field_validation_details(client: TestClient) -> None:
    response = client.post(
        "/v1/predict",
        json={"screenshot": SCREENSHOT, "instruction": "", "screen_width": 100},
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    locs = {tuple(detail["loc"]) for detail in error["details"]}
    assert ("body", "instruction") in locs
    assert ("body", "screen_width") in locs


def test_ground_is_stable_and_in_bounds(client: TestClient) -> None:
    body = {
        "screenshot": SCREENSHOT,
        "element": "the blue Submit button",
        "screen_width": 800,
        "screen_height": 600,
    }
    first = client.post("/v1/ground", json=body).json()
    second = client.post("/v1/ground", json=body).json()
    assert first == second
    assert 0 <= first["x"] < 800
    assert 0 <= first["y"] < 600
    other = client.post("/v1/ground", json={**body, "element": "the red Cancel link"}).json()
    assert (other["x"], other["y"]) != (first["x"], first["y"])


def test_parse_round_trip(client: TestClient) -> None:
    code = "\n".join(
        [
            "# login",
            "pyautogui.click(100, 200)",
            "pyautogui.write('user@example.com')",
            "pyautogui.press('tab')",
            "pyautogui.hotkey('ctrl', 'a')",
            "pyautogui.scroll(-3)",
            "pyautogui.moveTo(10, 20)",
            "pyautogui.dragTo(30, 40)",
            "time.sleep(1.5)",
            "unknown_call()",
        ]
    )
    response = client.post("/v1/parse", json={"code": code})
    assert response.status_code == 200
    actions = response.json()["actions"]
    assert [a["action_type"] for a in actions] == [
        "click",
        "type_text",
        "key_press",
        "key_combo",
        "scroll",
        "move",
        "drag",
        "wait",
    ]
    assert actions[0]["params"] == {"x": 100, "y": 200}
    assert actions[4]["params"] == {"x": 100, "y": 200, "direction": "down", "amount": 3}
    assert actions[6]["params"] == {"from_x": 10, "from_y": 20, "to_x": 30, "to_y": 40}
    assert actions[7]["params"] == {"ms": 1500}


def test_parse_validation(client: TestClient) -> None:
    assert client.post("/v1/parse", json={}).status_code == 422
    assert client.post("/v1/parse", json={"code": ""}).status_code == 422
    assert client.post("/v1/parse", json={"code": "x" * 50_001}).status_code == 422


def test_models(client: TestClient) -> None:
    body = client.get("/v1/models").json()
    assert {entry["id"] for entry in body["cua_versions"]} == {"v1", "v3", "v4"}
    assert len(body["action_types"]) == 10
    assert "click" in body["action_types"] and "fail" in body["action_types"]
