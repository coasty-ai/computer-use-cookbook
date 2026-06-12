"""Tiny hand-rolled validators producing pydantic-style ``details`` lists.

The mock validates request bodies by hand so the error envelopes match the
documented contract exactly (422 VALIDATION_ERROR with ``details`` naming the
field loc) instead of FastAPI's default shapes.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from .errors import ApiError

JsonDict = dict[str, Any]


class Validator:
    def __init__(self) -> None:
        self.details: list[JsonDict] = []

    def add(self, loc: Sequence[str | int], msg: str, type_: str = "value_error") -> None:
        self.details.append({"loc": list(loc), "msg": msg, "type": type_})

    def raise_if_any(self) -> None:
        if self.details:
            raise ApiError(
                "VALIDATION_ERROR",
                "Request validation failed; see details.",
                extras={"details": self.details},
            )


def field_str(
    body: JsonDict,
    name: str,
    vd: Validator,
    *,
    required: bool = False,
    default: str | None = None,
    min_len: int | None = None,
    max_len: int | None = None,
    pattern: re.Pattern[str] | None = None,
    choices: set[str] | None = None,
) -> str | None:
    value = body.get(name)
    if value is None:
        if required:
            vd.add(["body", name], "field required", "missing")
        return default
    if not isinstance(value, str):
        vd.add(["body", name], "expected a string", "type_error")
        return default
    if min_len is not None and len(value) < min_len:
        vd.add(["body", name], f"must be at least {min_len} characters")
        return default
    if max_len is not None and len(value) > max_len:
        vd.add(["body", name], f"must be at most {max_len} characters")
        return default
    if pattern is not None and not pattern.fullmatch(value):
        vd.add(["body", name], f"must match {pattern.pattern}")
        return default
    if choices is not None and value not in choices:
        vd.add(["body", name], f"must be one of {sorted(choices)}")
        return default
    return value


def field_int(
    body: JsonDict,
    name: str,
    vd: Validator,
    *,
    required: bool = False,
    default: int | None = None,
    lo: int | None = None,
    hi: int | None = None,
) -> int | None:
    value = body.get(name)
    if value is None:
        if required:
            vd.add(["body", name], "field required", "missing")
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        vd.add(["body", name], "expected an integer", "type_error")
        return default
    if lo is not None and value < lo:
        vd.add(["body", name], f"must be >= {lo}")
        return default
    if hi is not None and value > hi:
        vd.add(["body", name], f"must be <= {hi}")
        return default
    return value


def field_bool(
    body: JsonDict,
    name: str,
    vd: Validator,
    *,
    required: bool = False,
    default: bool | None = None,
) -> bool | None:
    value = body.get(name)
    if value is None:
        if required:
            vd.add(["body", name], "field required", "missing")
        return default
    if not isinstance(value, bool):
        vd.add(["body", name], "expected a boolean", "type_error")
        return default
    return value


def field_dict(
    body: JsonDict,
    name: str,
    vd: Validator,
    *,
    required: bool = False,
    max_keys: int | None = None,
) -> JsonDict | None:
    value = body.get(name)
    if value is None:
        if required:
            vd.add(["body", name], "field required", "missing")
        return None
    if not isinstance(value, dict):
        vd.add(["body", name], "expected an object", "type_error")
        return None
    if max_keys is not None and len(value) > max_keys:
        vd.add(["body", name], f"must have at most {max_keys} keys")
        return None
    return value


def field_str_list(
    body: JsonDict,
    name: str,
    vd: Validator,
) -> list[str] | None:
    value = body.get(name)
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        vd.add(["body", name], "expected an array of strings", "type_error")
        return None
    return value


def reject_unknown_fields(body: JsonDict, allowed: set[str], vd: Validator) -> None:
    for key in body:
        if key not in allowed:
            vd.add(["body", key], "unknown field", "extra_forbidden")


_B64_RE = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")


def validate_screenshot(value: Any, field: str = "screenshot") -> str:
    """The documented screenshot contract: raw base64, > 100 chars, no data: prefix."""
    if not isinstance(value, str) or not value:
        raise ApiError(
            "VALIDATION_ERROR",
            f"'{field}' is required and must be a base64 string.",
            extras={
                "details": [{"loc": ["body", field], "msg": "field required", "type": "missing"}]
            },
        )
    if len(value) > 10_000_000:
        raise ApiError(
            "PAYLOAD_TOO_LARGE",
            "Screenshot exceeds the 10 MB base64 cap.",
        )
    if value.startswith("data:"):
        raise ApiError(
            "INVALID_SCREENSHOT",
            f"'{field}' must be raw base64 without a data: prefix.",
        )
    if len(value) <= 100 or not _B64_RE.fullmatch(value):
        raise ApiError(
            "INVALID_SCREENSHOT",
            f"'{field}' must be decodable base64 longer than 100 characters "
            "(no whitespace or data: prefix).",
        )
    return value


def parse_limit(raw: str | None, *, default: int) -> int:
    """Query ``limit`` validation -> 400 INVALID_LIMIT outside 1..200."""
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        value = -1
    if value < 1 or value > 200:
        raise ApiError(
            "INVALID_LIMIT",
            f"limit must be between 1 and 200 (got {raw!r}).",
            extras={"actual": raw if value == -1 else value, "min": 1, "max": 200},
        )
    return value
