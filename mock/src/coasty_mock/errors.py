"""The documented Coasty error envelope and full error catalog.

Every error, on every endpoint, is ``{"error": {code, message, type,
request_id, suggestion?, docs_url?, support?, ...context}}`` with the HTTP
status from the catalog below. Clients must branch on ``code``, never on
``message``.
"""

from __future__ import annotations

from typing import Any

JsonDict = dict[str, Any]

DOCS_URL = "https://coasty.ai/api-docs#errors"
SUPPORT = "founders@coasty.ai"

#: code -> (http_status, error.type)
#:
#: Note: the upstream docs list IDEMPOTENCY_KEY_REUSED under both 422 (runs
#: section) and 409 (error catalog). Per docs/API_NOTES.md the CODE is
#: canonical, not the status; this mock uses 422 as documented in the runs
#: section.
ERROR_CATALOG: dict[str, tuple[int, str]] = {
    "INVALID_API_KEY": (401, "auth_error"),
    "INVALID_SIGNATURE": (401, "auth_error"),
    "INSUFFICIENT_SCOPE": (403, "auth_error"),
    "INSUFFICIENT_CREDITS": (402, "billing_error"),
    "WALLET_EXHAUSTED": (402, "billing_error"),
    "VALIDATION_ERROR": (422, "validation_error"),
    "INVALID_SCREENSHOT": (422, "validation_error"),
    "IDEMPOTENCY_KEY_REUSED": (422, "validation_error"),
    "PAYLOAD_TOO_LARGE": (413, "validation_error"),
    "INVALID_LIMIT": (400, "validation_error"),
    "INVALID_STATUS_FILTER": (400, "validation_error"),
    "FEATURE_NOT_AVAILABLE": (400, "validation_error"),
    "EMPTY_UPDATE": (400, "validation_error"),
    "NOT_FOUND": (404, "not_found_error"),
    "MACHINE_NOT_FOUND": (404, "not_found_error"),
    "RUN_NOT_FOUND": (404, "not_found_error"),
    "WORKFLOW_NOT_FOUND": (404, "not_found_error"),
    "SESSION_NOT_FOUND": (404, "not_found_error"),
    "NOT_AWAITING_HUMAN": (409, "state_error"),
    "RESUME_CONFLICT": (409, "state_error"),
    "INVALID_STATE": (409, "state_error"),
    "GUARD_EXCEEDED": (409, "state_error"),
    "RATE_LIMITED": (429, "rate_limit_error"),
    "INTERNAL_ERROR": (500, "server_error"),
    "PREDICTION_FAILED": (500, "server_error"),
    "GROUNDING_FAILED": (500, "server_error"),
    "UPSTREAM_UNAVAILABLE": (503, "server_error"),
    "UPSTREAM_TIMEOUT": (504, "server_error"),
}

_SUGGESTIONS: dict[str, str] = {
    "INVALID_API_KEY": (
        "Send a raw sk-coasty-live-/sk-coasty-test- key in X-API-Key, "
        "or Authorization: Bearer <key>."
    ),
    "INSUFFICIENT_CREDITS": (
        "Top up at https://coasty.ai/credits, or switch to a sandbox key "
        "'sk-coasty-test-...' for free testing."
    ),
    "INSUFFICIENT_SCOPE": "Re-mint the key with the missing scope.",
    "RATE_LIMITED": "Honor the Retry-After header before retrying.",
    "UPSTREAM_UNAVAILABLE": "Retry with backoff; check https://status.coasty.ai.",
    "VALIDATION_ERROR": "Fix the field named in error.details and retry.",
    "INVALID_SCREENSHOT": "Strip the data: prefix and whitespace; send raw base64.",
    "IDEMPOTENCY_KEY_REUSED": (
        "Resend the original body to get the cached result, or use a new key."
    ),
    "NOT_AWAITING_HUMAN": "Re-GET the run; resume only while status == 'awaiting_human'.",
}


class ApiError(Exception):
    """Raise anywhere inside a route; the app handler renders the envelope."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        extras: JsonDict | None = None,
        headers: dict[str, str] | None = None,
        status: int | None = None,
        error_type: str | None = None,
    ) -> None:
        super().__init__(message)
        catalog_status, catalog_type = ERROR_CATALOG.get(code, (400, "validation_error"))
        self.code = code
        self.message = message
        self.status = status if status is not None else catalog_status
        self.error_type = error_type if error_type is not None else catalog_type
        self.extras: JsonDict = extras or {}
        self.headers: dict[str, str] = headers or {}
        if code == "INVALID_API_KEY":
            self.headers.setdefault("WWW-Authenticate", "Bearer")


def error_body(
    code: str,
    message: str,
    error_type: str,
    request_id: str,
    extras: JsonDict | None = None,
) -> JsonDict:
    """Build the documented envelope; context extras are merged in."""
    error: JsonDict = {
        "code": code,
        "message": message,
        "type": error_type,
        "request_id": request_id,
        "docs_url": DOCS_URL,
    }
    suggestion = _SUGGESTIONS.get(code)
    if suggestion is not None:
        error["suggestion"] = suggestion
    if error_type == "server_error":
        error["support"] = SUPPORT
    if extras:
        error.update(extras)
    return {"error": error}


def scope_for_path(path: str, method: str) -> str:
    """Best-effort scope name for a route (used for forced 403 envelopes)."""
    write = method.upper() not in {"GET", "HEAD"}
    if path.startswith("/v1/predict"):
        return "predict"
    if path.startswith("/v1/sessions"):
        return "session"
    if path.startswith("/v1/ground"):
        return "ground"
    if path.startswith("/v1/parse"):
        return "parse"
    if path.startswith("/v1/usage"):
        return "usage"
    if path.startswith("/v1/runs"):
        return "runs:write" if write else "runs:read"
    if path.startswith("/v1/workflows"):
        return "workflows:write" if write else "workflows:read"
    if "/terminal" in path:
        return "terminal:exec"
    if "/files/" in path:
        return "files:write" if write else "files:read"
    if "/snapshot" in path:
        return "snapshots:write"
    if "/connection" in path:
        return "connection:read"
    if "/actions" in path or "/browser/" in path:
        return "actions:exec"
    if path.startswith("/v1/machines"):
        return "machines:write" if write else "machines:read"
    return "predict"


def forced_error(
    code: str,
    *,
    path: str,
    method: str,
    wallet_balance_cents: int,
    retry_after_seconds: int,
) -> ApiError:
    """Build the error forced by the ``X-Mock-Force-Error`` header.

    Each documented code is returned with its catalog status and realistic
    context extras so the error-matrix examples see complete envelopes.
    """
    if code not in ERROR_CATALOG:
        return ApiError(
            "VALIDATION_ERROR",
            f"Unknown X-Mock-Force-Error code {code!r}; see README for the catalog.",
            extras={"details": [{"loc": ["header", "X-Mock-Force-Error"], "msg": "unknown code"}]},
        )
    extras: JsonDict = {}
    headers: dict[str, str] = {}
    if code == "INSUFFICIENT_SCOPE":
        extras = {
            "required_scope": scope_for_path(path, method),
            "current_scopes": ["predict", "session", "ground", "parse"],
        }
    elif code == "INSUFFICIENT_CREDITS":
        extras = {"required": 5, "balance": wallet_balance_cents}
    elif code == "VALIDATION_ERROR":
        extras = {
            "details": [
                {"loc": ["body", "field"], "msg": "forced by X-Mock-Force-Error", "type": "forced"}
            ]
        }
    elif code == "INVALID_LIMIT":
        extras = {"actual": 0, "min": 1, "max": 200}
    elif code == "INVALID_STATUS_FILTER":
        extras = {
            "valid_options": [
                "queued",
                "running",
                "awaiting_human",
                "succeeded",
                "failed",
                "cancelled",
                "timed_out",
            ]
        }
    elif code == "INVALID_STATE":
        extras = {"current_state": "terminated", "allowed_from": ["running"]}
    elif code in {"RATE_LIMITED", "UPSTREAM_UNAVAILABLE"}:
        extras = {"retry_after": retry_after_seconds}
        headers["Retry-After"] = str(retry_after_seconds)
    return ApiError(
        code,
        f"Forced {code} via X-Mock-Force-Error.",
        extras=extras,
        headers=headers,
    )
