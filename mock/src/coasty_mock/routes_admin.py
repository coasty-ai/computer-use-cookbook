"""Unauthenticated test-control endpoints under /__mock__ (never part of /v1).

- ``POST /__mock__/reset``    -> pristine state (optional ``{"seed": int}``)
- ``GET  /__mock__/config``   -> current knobs + wallet + frozen-clock time
- ``POST /__mock__/config``   -> tweak knobs (wallet balance, webhook delivery,
  run step counts, ...); ``advance_clock_seconds`` moves the frozen clock
- ``GET  /__mock__/webhooks`` -> every recorded webhook emission (signed)
"""

from __future__ import annotations

from dataclasses import asdict, fields
from typing import Any

from fastapi import APIRouter, Request

from .clock import FrozenClock, iso
from .deps import json_body, mock_state
from .errors import ApiError
from .state import MockConfig

JsonDict = dict[str, Any]

router = APIRouter(prefix="/__mock__")

_CONFIG_FIELDS = {f.name: f.type for f in fields(MockConfig)}
_SPECIAL_KEYS = {"advance_clock_seconds", "set_clock_epoch", "wallet_balance_cents"}


def _config_view(request: Request) -> JsonDict:
    state = mock_state(request)
    return {
        **asdict(state.config),
        "wallet_balance_cents": state.wallet_balance_cents,
        "seed": state.seed,
        "clock_now": state.clock.now(),
        "clock_now_iso": iso(state.clock.now()),
        "clock_frozen": isinstance(state.clock, FrozenClock),
    }


@router.post("/reset")
async def reset(request: Request) -> JsonDict:
    state = mock_state(request)
    body = await json_body(request)
    seed = body.get("seed")
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
        raise ApiError(
            "VALIDATION_ERROR",
            "seed must be an integer.",
            extras={"details": [{"loc": ["body", "seed"], "msg": "expected an integer"}]},
        )
    state.reset(seed)
    return {"status": "ok", "seed": state.seed}


@router.get("/config")
def get_config(request: Request) -> JsonDict:
    return _config_view(request)


@router.post("/config")
async def set_config(request: Request) -> JsonDict:
    state = mock_state(request)
    body = await json_body(request)
    for key, value in body.items():
        if key not in _CONFIG_FIELDS and key not in _SPECIAL_KEYS:
            raise ApiError(
                "VALIDATION_ERROR",
                f"Unknown config key {key!r}.",
                extras={"details": [{"loc": ["body", key], "msg": "unknown config key"}]},
            )
        if key == "advance_clock_seconds":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ApiError("VALIDATION_ERROR", "advance_clock_seconds must be a number.")
            state.clock.advance(float(value))
        elif key == "set_clock_epoch":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ApiError("VALIDATION_ERROR", "set_clock_epoch must be a number.")
            if not isinstance(state.clock, FrozenClock):
                raise ApiError("VALIDATION_ERROR", "set_clock_epoch needs the frozen clock.")
            state.clock.set_to(float(value))
        elif key == "wallet_balance_cents":
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ApiError("VALIDATION_ERROR", "wallet_balance_cents must be an int >= 0.")
            state.config.wallet_balance_cents = value
            state.wallet_balance_cents = value
        elif key == "deliver_webhooks":
            if not isinstance(value, bool):
                raise ApiError("VALIDATION_ERROR", f"{key} must be a boolean.")
            state.config.deliver_webhooks = value
        else:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ApiError("VALIDATION_ERROR", f"{key} must be an int >= 0.")
            setattr(state.config, key, value)
    return _config_view(request)


@router.get("/webhooks")
def list_webhooks(request: Request) -> JsonDict:
    state = mock_state(request)
    return {"deliveries": state.webhook_deliveries}
