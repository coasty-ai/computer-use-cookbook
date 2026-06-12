"""Stateful sessions: create / predict / reset / get / list / delete."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from .actions import synthesize_actions, token_usage
from .clock import iso
from .deps import charge, json_body, mock_state, mode_of, request_id
from .errors import ApiError
from .pricing import SESSION_CREATE, session_predict_price
from .routes_core import CUA_VERSIONS
from .state import TestState
from .validation import (
    Validator,
    field_bool,
    field_dict,
    field_int,
    field_str,
    field_str_list,
    validate_screenshot,
)

JsonDict = dict[str, Any]

router = APIRouter(prefix="/v1")


def _active_sessions(state: TestState, mode: str) -> list[JsonDict]:
    now = state.clock.now()
    return [
        session
        for session in state.sessions.values()
        if session["_mode"] == mode and now < session["_expires_epoch"]
    ]


def _get_session(state: TestState, session_id: str, mode: str) -> JsonDict:
    session = state.sessions.get(session_id)
    if session is None or session["_mode"] != mode:
        raise ApiError("SESSION_NOT_FOUND", f"No session {session_id!r} for this key.")
    if state.clock.now() >= session["_expires_epoch"]:
        raise ApiError("SESSION_NOT_FOUND", f"Session {session_id!r} has expired.")
    return session


def _session_info(session: JsonDict) -> JsonDict:
    return {
        "session_id": session["session_id"],
        "cua_version": session["cua_version"],
        "screen_size": session["screen_size"],
        "step_count": session["step_count"],
        "created_at": session["created_at"],
        "expires_at": session["expires_at"],
        "total_credits_used": session["total_credits_used"],
    }


@router.post("/sessions")
async def create_session(request: Request) -> JsonDict:
    state = mock_state(request)
    body = await json_body(request)
    vd = Validator()
    cua_version = field_str(body, "cua_version", vd, default="v3", choices=CUA_VERSIONS)
    width = field_int(body, "screen_width", vd, default=1920, lo=320, hi=3840)
    height = field_int(body, "screen_height", vd, default=1080, lo=240, hi=2160)
    max_trajectory = field_int(body, "max_trajectory_length", vd, default=3, lo=1, hi=20)
    system_prompt = field_str(body, "system_prompt", vd, max_len=32000)
    field_str(body, "instructions", vd, max_len=16000)
    tools = field_str_list(body, "tools", vd)
    metadata = field_dict(body, "metadata", vd)
    vd.raise_if_any()
    assert cua_version is not None and width is not None and height is not None
    assert max_trajectory is not None

    mode = mode_of(request)
    if len(_active_sessions(state, mode)) >= state.config.max_concurrent_sessions:
        raise ApiError(
            "RATE_LIMITED",
            "Session concurrency quota reached; delete a session first.",
            extras={"retry_after": state.config.retry_after_seconds},
            headers={"Retry-After": str(state.config.retry_after_seconds)},
        )

    charge(request, SESSION_CREATE, "sessions.create")
    now = state.clock.now()
    expires = now + state.config.session_ttl_seconds
    session_id = state.next_id("session", "sess_", 12)
    state.sessions[session_id] = {
        "session_id": session_id,
        "cua_version": cua_version,
        "screen_width": width,
        "screen_height": height,
        "screen_size": f"{width}x{height}",
        "max_trajectory_length": max_trajectory,
        "system_prompt": system_prompt,
        "tools": tools,
        "metadata": metadata,
        "step_count": 0,
        "total_credits_used": SESSION_CREATE,
        "created_at": iso(now),
        "expires_at": iso(expires),
        "_expires_epoch": expires,
        "_mode": mode,
    }
    return {
        "session_id": session_id,
        "cua_version": cua_version,
        "screen_size": f"{width}x{height}",
        "created_at": iso(now),
        "expires_at": iso(expires),
    }


@router.post("/sessions/{session_id}/predict")
async def session_predict(request: Request, session_id: str) -> JsonDict:
    state = mock_state(request)
    session = _get_session(state, session_id, mode_of(request))
    body = await json_body(request)
    vd = Validator()
    instruction = field_str(body, "instruction", vd, required=True, min_len=1)
    include_reasoning = field_bool(body, "include_reasoning", vd, default=True)
    include_raw_code = field_bool(body, "include_raw_code", vd, default=True)
    vd.raise_if_any()
    validate_screenshot(body.get("screenshot"))
    assert instruction is not None

    trajectory_screenshots = min(int(session["step_count"]), int(session["max_trajectory_length"]))
    price = session_predict_price(
        width=int(session["screen_width"]),
        height=int(session["screen_height"]),
        trajectory_screenshots=trajectory_screenshots,
        cua_version=str(session["cua_version"]),
        system_prompt=session["system_prompt"],
    )
    charge(request, price, "sessions.predict")

    status, actions, reasoning = synthesize_actions(
        state,
        counter_key=session_id,
        instruction=instruction,
        width=int(session["screen_width"]),
        height=int(session["screen_height"]),
        tools=session["tools"],
        max_actions=5,
    )
    session["step_count"] = int(session["step_count"]) + 1
    session["total_credits_used"] = int(session["total_credits_used"]) + price
    input_tokens, output_tokens = token_usage(instruction, actions)
    raw_code = [str(action["raw_code"]) for action in actions]
    if not include_raw_code:
        actions = [{k: v for k, v in action.items() if k != "raw_code"} for action in actions]
    return {
        "request_id": request_id(request),
        "session_id": session_id,
        "step": session["step_count"],
        "actions": actions,
        "raw_code": raw_code if include_raw_code else [],
        "reasoning": reasoning if include_reasoning else None,
        "status": status,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "credits_charged": price,
            "cost_cents": price,
        },
    }


@router.post("/sessions/{session_id}/reset")
def reset_session(request: Request, session_id: str) -> JsonDict:
    state = mock_state(request)
    session = _get_session(state, session_id, mode_of(request))
    session["step_count"] = 0
    for key in [k for k in state.predict_counts if k[0] == session_id]:
        del state.predict_counts[key]
    return {"status": "ok", "session_id": session_id}


@router.delete("/sessions/{session_id}")
def delete_session(request: Request, session_id: str) -> JsonDict:
    state = mock_state(request)
    _get_session(state, session_id, mode_of(request))
    del state.sessions[session_id]
    for key in [k for k in state.predict_counts if k[0] == session_id]:
        del state.predict_counts[key]
    return {"status": "ok", "session_id": session_id}


@router.get("/sessions")
def list_sessions(request: Request) -> JsonDict:
    state = mock_state(request)
    sessions = _active_sessions(state, mode_of(request))
    return {"sessions": [_session_info(session) for session in sessions]}


@router.get("/sessions/{session_id}")
def get_session(request: Request, session_id: str) -> JsonDict:
    state = mock_state(request)
    session = _get_session(state, session_id, mode_of(request))
    return _session_info(session)
