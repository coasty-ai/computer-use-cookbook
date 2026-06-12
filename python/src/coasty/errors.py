"""Typed exceptions mirroring the Coasty error contract.

Every API error carries ``code``, ``message``, ``error_type``, ``request_id``,
``status_code`` and an ``extras`` dict with the code-specific context fields
(``required``, ``balance``, ``required_scope``, ``current_scopes``,
``retry_after``, ``details``, ``current_state``, ``allowed_from``, ...).

Branch on ``error.code`` -- never on ``message`` (the docs list e.g.
IDEMPOTENCY_KEY_REUSED under both 422 and 409, so the code is canonical even
though the exception class is chosen by HTTP status).
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Mapping
from typing import Any

import httpx

_ENVELOPE_CORE_KEYS = frozenset({"code", "message", "type", "request_id"})


class CoastyError(Exception):
    """Base class for every error raised by this client."""

    default_code: str = "UNKNOWN_ERROR"
    default_type: str = "server_error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        error_type: str | None = None,
        request_id: str | None = None,
        status_code: int | None = None,
        extras: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code
        self.error_type = error_type or self.default_type
        self.request_id = request_id
        self.status_code = status_code
        self.extras: dict[str, Any] = dict(extras or {})

    def __str__(self) -> str:
        parts = [f"{self.code}: {self.message}"]
        if self.status_code is not None:
            parts.append(f"(HTTP {self.status_code})")
        if self.request_id:
            parts.append(f"[request_id={self.request_id}]")
        return " ".join(parts)

    def _extra_int(self, key: str) -> int | None:
        value = self.extras.get(key)
        if isinstance(value, bool):
            return None
        return value if isinstance(value, int) else None

    def _extra_str(self, key: str) -> str | None:
        value = self.extras.get(key)
        return value if isinstance(value, str) else None


class AuthenticationError(CoastyError):
    """401 -- missing, malformed, or revoked API key."""

    default_code = "INVALID_API_KEY"
    default_type = "auth_error"


class InsufficientScopeError(CoastyError):
    """403 INSUFFICIENT_SCOPE -- the key lacks the scope this route needs."""

    default_code = "INSUFFICIENT_SCOPE"
    default_type = "auth_error"

    @property
    def required_scope(self) -> str | None:
        return self._extra_str("required_scope")

    @property
    def current_scopes(self) -> list[str] | None:
        value = self.extras.get("current_scopes")
        if isinstance(value, list):
            return [str(scope) for scope in value]
        return None


class InsufficientCreditsError(CoastyError):
    """402 -- the prepaid wallet cannot cover this request. Never retried."""

    default_code = "INSUFFICIENT_CREDITS"
    default_type = "billing_error"

    @property
    def required(self) -> int | None:
        """Credits the operation needs (1 credit = $0.01)."""
        return self._extra_int("required")

    @property
    def balance(self) -> int | None:
        """Current wallet balance in credits/cents."""
        return self._extra_int("balance")


class ValidationError(CoastyError):
    """400/413/422 -- a request field failed validation."""

    default_code = "VALIDATION_ERROR"
    default_type = "validation_error"

    @property
    def details(self) -> Any | None:
        """Field-level details (``loc`` paths) when the server provides them."""
        return self.extras.get("details")


class NotFoundError(CoastyError):
    """404 -- id does not exist in this key's (mode-isolated) namespace."""

    default_code = "NOT_FOUND"
    default_type = "not_found_error"


class ConflictError(CoastyError):
    """409 -- lifecycle conflict (NOT_AWAITING_HUMAN, INVALID_STATE, ...)."""

    default_code = "INVALID_STATE"
    default_type = "state_error"

    @property
    def current_state(self) -> str | None:
        return self._extra_str("current_state")

    @property
    def allowed_from(self) -> list[str] | None:
        value = self.extras.get("allowed_from")
        if isinstance(value, list):
            return [str(state) for state in value]
        return None


class RateLimitError(CoastyError):
    """429 RATE_LIMITED -- honor :attr:`retry_after` before retrying."""

    default_code = "RATE_LIMITED"
    default_type = "rate_limit_error"

    @property
    def retry_after(self) -> float | None:
        value = self.extras.get("retry_after")
        if isinstance(value, bool):
            return None
        if isinstance(value, int | float):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return None
        return None


class ServerError(CoastyError):
    """5xx -- INTERNAL_ERROR / PREDICTION_FAILED / UPSTREAM_* (auto-refunded)."""

    default_code = "INTERNAL_ERROR"
    default_type = "server_error"


class CoastyConnectionError(CoastyError):
    """A transport-level failure (DNS, connect, read) after retries."""

    default_code = "CONNECTION_ERROR"
    default_type = "transport_error"


def error_class_for_status(status_code: int) -> type[CoastyError]:
    """Pick the exception class for an HTTP status (code stays preserved)."""
    mapping: dict[int, type[CoastyError]] = {
        401: AuthenticationError,
        402: InsufficientCreditsError,
        403: InsufficientScopeError,
        404: NotFoundError,
        409: ConflictError,
        429: RateLimitError,
    }
    if status_code in mapping:
        return mapping[status_code]
    if status_code in (400, 413, 422):
        return ValidationError
    if status_code >= 500:
        return ServerError
    return CoastyError


def _header_lookup(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def error_from_parts(
    status_code: int,
    payload: object,
    headers: Mapping[str, str] | None = None,
) -> CoastyError:
    """Build a typed error from a status code plus a (possibly non-JSON) body."""
    headers = headers or {}
    code: str | None = None
    message: str | None = None
    error_type: str | None = None
    request_id: str | None = None
    extras: dict[str, Any] = {}

    if isinstance(payload, Mapping):
        envelope = payload.get("error")
        if isinstance(envelope, Mapping):
            raw_code = envelope.get("code")
            raw_message = envelope.get("message")
            raw_type = envelope.get("type")
            raw_request_id = envelope.get("request_id")
            code = raw_code if isinstance(raw_code, str) else None
            message = raw_message if isinstance(raw_message, str) else None
            error_type = raw_type if isinstance(raw_type, str) else None
            request_id = raw_request_id if isinstance(raw_request_id, str) else None
            extras = {
                str(key): value
                for key, value in envelope.items()
                if str(key) not in _ENVELOPE_CORE_KEYS
            }
    elif isinstance(payload, str) and payload.strip():
        message = payload.strip()[:300]

    if message is None:
        message = f"HTTP {status_code} error with no parseable error envelope"
    if request_id is None:
        request_id = _header_lookup(headers, "X-Coasty-Request-Id")

    cls = error_class_for_status(status_code)
    return cls(
        message,
        code=code,
        error_type=error_type,
        request_id=request_id,
        status_code=status_code,
        extras=extras,
    )


def error_from_response(response: httpx.Response) -> CoastyError:
    """Build a typed error from an ``httpx.Response`` (tolerates non-JSON)."""
    payload: object
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError):
        payload = response.text
    error = error_from_parts(response.status_code, payload, response.headers)
    if isinstance(error, RateLimitError) and "retry_after" not in error.extras:
        header = response.headers.get("Retry-After")
        if header is not None:
            with contextlib.suppress(ValueError):
                error.extras["retry_after"] = float(header)
    return error
