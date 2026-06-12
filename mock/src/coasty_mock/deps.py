"""Per-request helpers shared by every router."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from fastapi import Request

from .errors import ApiError
from .state import TestState

JsonDict = dict[str, Any]

_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9_\-:]{1,128}$")


def mock_state(request: Request) -> TestState:
    state = request.app.state.mock
    if not isinstance(state, TestState):  # pragma: no cover - wiring error
        raise RuntimeError("app.state.mock is not a TestState")
    return state


def request_id(request: Request) -> str:
    return str(request.scope["state"]["request_id"])


def api_key(request: Request) -> str:
    return str(request.scope["state"].get("api_key", ""))


def key_kind(request: Request) -> str:
    """'test' | 'live' | 'legacy' (legacy bills like live)."""
    return str(request.scope["state"].get("key_kind", "test"))


def mode_of(request: Request) -> str:
    """Mode isolation bucket: test keys vs everything else."""
    return "test" if key_kind(request) == "test" else "live"


async def json_body(request: Request) -> JsonDict:
    try:
        raw = await request.body()
        data = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ApiError(
            "VALIDATION_ERROR",
            "Request body must be a valid JSON object.",
            extras={"details": [{"loc": ["body"], "msg": str(exc), "type": "json_invalid"}]},
        ) from exc
    if not isinstance(data, dict):
        raise ApiError(
            "VALIDATION_ERROR",
            "Request body must be a JSON object.",
            extras={"details": [{"loc": ["body"], "msg": "expected object", "type": "type_error"}]},
        )
    return data


def charge(request: Request, credits: int, endpoint: str) -> int:
    """Debit the wallet (live/legacy keys) and record billing headers.

    Test keys are validated identically but never billed: charged is 0 and
    ``X-Credits-Charged: 0`` is sent, matching the documented sandbox
    behaviour. Raises 402 INSUFFICIENT_CREDITS when the wallet cannot cover
    a live charge.
    """
    state = mock_state(request)
    charged = 0 if mode_of(request) == "test" else credits
    if charged > state.wallet_balance_cents:
        raise ApiError(
            "INSUFFICIENT_CREDITS",
            f"Operation needs {credits} credits; you have {state.wallet_balance_cents}.",
            extras={"required": credits, "balance": state.wallet_balance_cents},
        )
    state.wallet_balance_cents -= charged
    state.record_usage(endpoint, charged)
    previous = request.scope["state"].get("credits_charged") or 0
    request.scope["state"]["credits_charged"] = int(previous) + charged
    return charged


def debit_wallet(state: TestState, *, mode: str, credits: int, endpoint: str) -> bool:
    """Wallet debit for engine-driven billing (run/workflow steps).

    Unlike :func:`charge` this does not touch the request scope (steps are
    billed while polling, not on the billed POST itself). Test mode never
    debits; returns False when a live wallet cannot cover the charge.
    """
    if mode == "test":
        state.record_usage(endpoint, 0)
        return True
    if state.wallet_balance_cents < credits:
        return False
    state.wallet_balance_cents -= credits
    state.record_usage(endpoint, credits)
    return True


def idempotency_key(request: Request) -> str | None:
    raw = request.headers.get("Idempotency-Key")
    if raw is None:
        return None
    if not _IDEMPOTENCY_RE.fullmatch(raw):
        raise ApiError(
            "VALIDATION_ERROR",
            "Idempotency-Key must be 1-128 chars of [A-Za-z0-9_-:].",
            extras={
                "details": [
                    {"loc": ["header", "Idempotency-Key"], "msg": "invalid format"},
                ]
            },
        )
    return raw


def body_fingerprint(body: JsonDict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def check_idempotency(
    request: Request, body: JsonDict, route: str
) -> tuple[tuple[str, str, str] | None, JsonDict | None]:
    """Return (cache_key, cached_response). Raises on key reuse with new body."""
    idem = idempotency_key(request)
    if idem is None:
        return None, None
    state = mock_state(request)
    cache_key = (api_key(request), route, idem)
    cached = state.idempotency.get(cache_key)
    if cached is None:
        return cache_key, None
    if cached["fingerprint"] != body_fingerprint(body):
        raise ApiError(
            "IDEMPOTENCY_KEY_REUSED",
            "This Idempotency-Key was already used with a different request body.",
        )
    request.scope["state"]["idempotent_replay"] = True
    request.scope["state"]["credits_charged"] = cached.get("credits_charged", 0)
    response: JsonDict = cached["response"]
    return cache_key, response


def store_idempotent(
    request: Request,
    cache_key: tuple[str, str, str] | None,
    body: JsonDict,
    response: JsonDict,
) -> None:
    if cache_key is None:
        return
    state = mock_state(request)
    state.idempotency[cache_key] = {
        "fingerprint": body_fingerprint(body),
        "response": response,
        "credits_charged": request.scope["state"].get("credits_charged", 0) or 0,
    }
