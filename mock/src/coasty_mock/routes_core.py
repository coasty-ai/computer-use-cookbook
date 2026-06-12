"""Core inference routes: /v1/predict, /v1/ground, /v1/parse, /v1/models, /v1/usage."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Request

from .actions import ground_point, synthesize_actions, token_usage
from .clock import period_of
from .deps import api_key, charge, json_body, mock_state, request_id
from .errors import ApiError
from .pricing import ground_price, predict_price
from .pyparse import parse_pyautogui
from .validation import (
    Validator,
    field_bool,
    field_int,
    field_str,
    field_str_list,
    validate_screenshot,
)

JsonDict = dict[str, Any]

router = APIRouter(prefix="/v1")

CUA_VERSIONS = {"v1", "v3", "v4"}
ACTION_TYPES = [
    "click",
    "type_text",
    "key_press",
    "key_combo",
    "scroll",
    "drag",
    "move",
    "wait",
    "done",
    "fail",
]
_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _usage_block(instruction: str, actions: list[JsonDict], credits: int) -> JsonDict:
    input_tokens, output_tokens = token_usage(instruction, actions)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "credits_charged": credits,
        "cost_cents": credits,
    }


def _strip_raw_code(actions: list[JsonDict]) -> list[JsonDict]:
    return [{k: v for k, v in action.items() if k != "raw_code"} for action in actions]


@router.post("/predict")
async def predict(request: Request) -> JsonDict:
    state = mock_state(request)
    body = await json_body(request)
    vd = Validator()
    instruction = field_str(body, "instruction", vd, required=True, min_len=1)
    cua_version = field_str(body, "cua_version", vd, default="v3", choices=CUA_VERSIONS)
    system_prompt = field_str(body, "system_prompt", vd, max_len=32000)
    field_str(body, "instructions", vd, max_len=16000)
    width = field_int(body, "screen_width", vd, default=1920, lo=320, hi=3840)
    height = field_int(body, "screen_height", vd, default=1080, lo=240, hi=2160)
    max_actions = field_int(body, "max_actions", vd, default=5, lo=1, hi=10)
    tools = field_str_list(body, "tools", vd)
    include_reasoning = field_bool(body, "include_reasoning", vd, default=True)
    include_raw_code = field_bool(body, "include_raw_code", vd, default=True)
    trajectory = body.get("trajectory", [])
    if not isinstance(trajectory, list) or not all(isinstance(t, dict) for t in trajectory):
        vd.add(["body", "trajectory"], "expected an array of step objects", "type_error")
        trajectory = []
    vd.raise_if_any()
    validate_screenshot(body.get("screenshot"))
    assert instruction is not None  # guaranteed by raise_if_any
    assert cua_version is not None and width is not None
    assert height is not None and max_actions is not None

    status, actions, reasoning = synthesize_actions(
        state,
        counter_key=f"predict:{api_key(request)}",
        instruction=instruction,
        width=width,
        height=height,
        tools=tools,
        max_actions=max_actions,
    )
    price = predict_price(
        width=width,
        height=height,
        trajectory_screenshots=len(trajectory),
        cua_version=cua_version,
        system_prompt=system_prompt,
    )
    charge(request, price, "predict")
    raw_code = [str(action["raw_code"]) for action in actions]
    return {
        "request_id": request_id(request),
        "status": status,
        "reasoning": reasoning if include_reasoning else None,
        "actions": actions if include_raw_code else _strip_raw_code(actions),
        "raw_code": raw_code if include_raw_code else [],
        "usage": _usage_block(instruction, actions, price),
    }


@router.post("/ground")
async def ground(request: Request) -> JsonDict:
    body = await json_body(request)
    vd = Validator()
    element = field_str(body, "element", vd, required=True, min_len=1)
    width = field_int(body, "screen_width", vd, default=1920, lo=320, hi=3840)
    height = field_int(body, "screen_height", vd, default=1080, lo=240, hi=2160)
    vd.raise_if_any()
    validate_screenshot(body.get("screenshot"))
    assert element is not None and width is not None and height is not None

    x, y = ground_point(element, width, height)
    price = ground_price(width=width, height=height)
    charge(request, price, "ground")
    return {"x": x, "y": y, "usage": _usage_block(element, [], price)}


@router.post("/parse")
async def parse(request: Request) -> JsonDict:
    body = await json_body(request)
    vd = Validator()
    code = field_str(body, "code", vd, required=True, min_len=1, max_len=49_999)
    vd.raise_if_any()
    assert code is not None
    return {"actions": parse_pyautogui(code)}


@router.get("/models")
def models(request: Request) -> JsonDict:
    return {
        "models": [
            {"id": "default", "description": "Default model - balanced performance and cost"}
        ],
        "cua_versions": [
            {
                "id": "v1",
                "description": "Baseline - single action per call, reflection enabled, "
                "8-screenshot trajectory",
                "avg_step_time": "9-10s",
                "features": ["reflection", "single_action"],
            },
            {
                "id": "v3",
                "description": "Lean - multi-action per call, no reflection, "
                "aggressive compaction",
                "avg_step_time": "3.5-4s",
                "features": ["multi_action", "compaction"],
            },
            {
                "id": "v4",
                "description": "Autonomous + verifier - pass/fail verification, recovery, "
                "cost governor",
                "avg_step_time": "varies",
                "features": ["verifier", "autonomous"],
            },
        ],
        "action_types": ACTION_TYPES,
    }


@router.get("/usage")
def usage(request: Request) -> JsonDict:
    state = mock_state(request)
    period = request.query_params.get("period")
    if period is None:
        period = period_of(state.clock.now())
    elif not _PERIOD_RE.fullmatch(period):
        raise ApiError(
            "VALIDATION_ERROR",
            f"period must be YYYY-MM (got {period!r}).",
            extras={"details": [{"loc": ["query", "period"], "msg": "must match YYYY-MM"}]},
        )
    return state.usage_for(period)
