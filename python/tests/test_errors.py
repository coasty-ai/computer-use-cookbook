"""Error envelope parsing -> typed exceptions (pure unit, offline)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from coasty.errors import (
    AuthenticationError,
    CoastyError,
    ConflictError,
    InsufficientCreditsError,
    InsufficientScopeError,
    NotFoundError,
    RateLimitError,
    ServerError,
    ValidationError,
    error_class_for_status,
    error_from_parts,
    error_from_response,
)


def envelope(code: str, error_type: str, **extras: Any) -> dict[str, Any]:
    error: dict[str, Any] = {
        "code": code,
        "message": "boom",
        "type": error_type,
        "request_id": "req_err_1",
    }
    error.update(extras)
    return {"error": error}


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, AuthenticationError),
        (402, InsufficientCreditsError),
        (403, InsufficientScopeError),
        (404, NotFoundError),
        (409, ConflictError),
        (429, RateLimitError),
        (400, ValidationError),
        (413, ValidationError),
        (422, ValidationError),
        (500, ServerError),
        (503, ServerError),
        (504, ServerError),
        (418, CoastyError),
    ],
)
def test_error_class_for_status(status: int, expected: type[CoastyError]) -> None:
    assert error_class_for_status(status) is expected


def test_envelope_fields_preserved() -> None:
    error = error_from_parts(
        422,
        envelope("VALIDATION_ERROR", "validation_error", details=[{"loc": ["screenshot"]}]),
    )
    assert isinstance(error, ValidationError)
    assert error.code == "VALIDATION_ERROR"
    assert error.message == "boom"
    assert error.error_type == "validation_error"
    assert error.request_id == "req_err_1"
    assert error.status_code == 422
    assert error.extras == {"details": [{"loc": ["screenshot"]}]}
    assert error.details == [{"loc": ["screenshot"]}]


def test_insufficient_credits_exposes_required_and_balance() -> None:
    error = error_from_parts(
        402, envelope("INSUFFICIENT_CREDITS", "billing_error", required=25, balance=10)
    )
    assert isinstance(error, InsufficientCreditsError)
    assert error.required == 25
    assert error.balance == 10


def test_insufficient_scope_exposes_scopes() -> None:
    error = error_from_parts(
        403,
        envelope(
            "INSUFFICIENT_SCOPE",
            "auth_error",
            required_scope="machines:write",
            current_scopes=["predict", "ground"],
        ),
    )
    assert isinstance(error, InsufficientScopeError)
    assert error.required_scope == "machines:write"
    assert error.current_scopes == ["predict", "ground"]


def test_conflict_exposes_state_context() -> None:
    error = error_from_parts(
        409,
        envelope(
            "NOT_AWAITING_HUMAN",
            "state_error",
            current_state="running",
            allowed_from=["awaiting_human"],
        ),
    )
    assert isinstance(error, ConflictError)
    assert error.code == "NOT_AWAITING_HUMAN"
    assert error.current_state == "running"
    assert error.allowed_from == ["awaiting_human"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(30, 30.0), (2.5, 2.5), ("7", 7.0), ("soon", None), (True, None), (None, None)],
)
def test_rate_limit_retry_after_coercion(raw: Any, expected: float | None) -> None:
    extras = {} if raw is None else {"retry_after": raw}
    error = error_from_parts(429, envelope("RATE_LIMITED", "rate_limit_error", **extras))
    assert isinstance(error, RateLimitError)
    assert error.retry_after == expected


def test_non_json_body_is_tolerated() -> None:
    error = error_from_parts(500, "<html>Internal Server Error</html>")
    assert isinstance(error, ServerError)
    assert error.code == "INTERNAL_ERROR"  # class default when no envelope
    assert "<html>Internal Server Error</html>" in error.message
    assert error.status_code == 500


def test_empty_body_gets_placeholder_message() -> None:
    error = error_from_parts(504, "")
    assert isinstance(error, ServerError)
    assert "504" in error.message


def test_request_id_falls_back_to_header() -> None:
    error = error_from_parts(
        500, {"error": {"message": "oops"}}, {"X-Coasty-Request-Id": "req_hdr_9"}
    )
    assert error.request_id == "req_hdr_9"


def test_request_id_header_lookup_is_case_insensitive() -> None:
    error = error_from_parts(500, "nope", {"x-coasty-request-id": "req_hdr_lower"})
    assert error.request_id == "req_hdr_lower"


def test_str_includes_code_status_and_request_id() -> None:
    error = error_from_parts(404, envelope("RUN_NOT_FOUND", "not_found_error"))
    text = str(error)
    assert "RUN_NOT_FOUND" in text
    assert "HTTP 404" in text
    assert "request_id=req_err_1" in text


def test_error_from_response_pulls_retry_after_header() -> None:
    response = httpx.Response(429, text="slow down", headers={"Retry-After": "7"})
    error = error_from_response(response)
    assert isinstance(error, RateLimitError)
    assert error.retry_after == 7.0


def test_error_from_response_parses_json_envelope() -> None:
    response = httpx.Response(
        401,
        json=envelope("INVALID_API_KEY", "auth_error"),
        headers={"X-Coasty-Request-Id": "req_hdr"},
    )
    error = error_from_response(response)
    assert isinstance(error, AuthenticationError)
    assert error.code == "INVALID_API_KEY"
    assert error.request_id == "req_err_1"  # body wins over header
