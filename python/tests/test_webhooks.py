"""HMAC webhook verification -- the EXACT shared vectors from docs/API_NOTES.md.

The same vectors are used by every language track so a signing bug in any
implementation fails loudly everywhere.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from coasty.webhooks import build_signature_header, compute_signature, verify_signature

# Vector 1 (valid)
V1_SECRET = "whsec_test_secret_123"
V1_T = 1750000000
V1_BODY = b'{"event":"run.succeeded","run_id":"run_123","status":"succeeded"}'
V1_SIG = "5f70978eab52dbf5838da76e5eb6c6c465560ce8e746ed8e6113c159d8bbb2d4"
V1_HEADER = f"t={V1_T},v1={V1_SIG}"

# Vector 2 (valid, second key)
V2_SECRET = "whsec_other_secret_456"
V2_T = 1750000300
V2_BODY = b'{"event":"run.awaiting_human","run_id":"run_456","reason":"captcha"}'
V2_SIG = "844504f42b7498094a83cedd7e050fc2f7fa32593b0814cc514c4be52a932e63"
V2_HEADER = f"t={V2_T},v1={V2_SIG}"


def test_compute_signature_matches_vector_1() -> None:
    assert compute_signature(V1_BODY, V1_SECRET, timestamp=V1_T) == V1_SIG


def test_compute_signature_matches_vector_2() -> None:
    assert compute_signature(V2_BODY, V2_SECRET, timestamp=V2_T) == V2_SIG


def test_vector_1_verifies() -> None:
    assert verify_signature(V1_BODY, V1_HEADER, V1_SECRET, now=V1_T) is True


def test_vector_2_verifies() -> None:
    assert verify_signature(V2_BODY, V2_HEADER, V2_SECRET, now=V2_T) is True


def test_tampered_body_rejected() -> None:
    tampered = V1_BODY.replace(b"run_123", b"run_124")
    assert verify_signature(tampered, V1_HEADER, V1_SECRET, now=V1_T) is False


def test_stale_timestamp_rejected_with_pinned_now() -> None:
    # signature is valid, but t is outside +/-300 s of "now"
    assert verify_signature(V1_BODY, V1_HEADER, V1_SECRET, now=V1_T + 301) is False


def test_future_timestamp_rejected() -> None:
    assert verify_signature(V1_BODY, V1_HEADER, V1_SECRET, now=V1_T - 301) is False


def test_tolerance_boundary_is_inclusive() -> None:
    assert verify_signature(V1_BODY, V1_HEADER, V1_SECRET, now=V1_T + 300) is True
    assert verify_signature(V1_BODY, V1_HEADER, V1_SECRET, now=V1_T - 300) is True


def test_custom_tolerance_window() -> None:
    assert (
        verify_signature(V1_BODY, V1_HEADER, V1_SECRET, tolerance_seconds=10, now=V1_T + 11)
        is False
    )
    assert (
        verify_signature(V1_BODY, V1_HEADER, V1_SECRET, tolerance_seconds=10, now=V1_T + 10) is True
    )


@pytest.mark.parametrize(
    "header",
    [
        "",
        "garbage",
        f"t={V1_T}",  # missing v1=
        f"v1={V1_SIG}",  # missing t=
        f"t=notanumber,v1={V1_SIG}",  # non-integer t
        f"t={V1_T} v1={V1_SIG}",  # wrong separator
    ],
)
def test_malformed_header_rejected(header: str) -> None:
    assert verify_signature(V1_BODY, header, V1_SECRET, now=V1_T) is False


def test_wrong_secret_rejected() -> None:
    # signature computed with vector 2's secret against vector 1's body
    wrong = build_signature_header(V1_BODY, V2_SECRET, timestamp=V1_T)
    assert verify_signature(V1_BODY, wrong, V1_SECRET, now=V1_T) is False


def test_empty_secret_rejected() -> None:
    assert verify_signature(V1_BODY, V1_HEADER, "", now=V1_T) is False


def test_non_bytes_body_rejected_without_raising() -> None:
    body_as_str = cast(bytes, V1_BODY.decode())
    assert verify_signature(body_as_str, V1_HEADER, V1_SECRET, now=V1_T) is False


def test_non_string_header_rejected_without_raising() -> None:
    assert verify_signature(V1_BODY, cast(str, cast(Any, None)), V1_SECRET, now=V1_T) is False


def test_any_matching_v1_candidate_passes() -> None:
    header = f"t={V1_T},v1={'0' * 64},v1={V1_SIG}"
    assert verify_signature(V1_BODY, header, V1_SECRET, now=V1_T) is True


def test_unknown_scheme_keys_ignored() -> None:
    header = f"t={V1_T},v2=deadbeef,v1={V1_SIG}"
    assert verify_signature(V1_BODY, header, V1_SECRET, now=V1_T) is True


def test_build_signature_header_round_trips() -> None:
    header = build_signature_header(V2_BODY, V2_SECRET, timestamp=V2_T)
    assert header == V2_HEADER
    assert verify_signature(V2_BODY, header, V2_SECRET, now=V2_T) is True
