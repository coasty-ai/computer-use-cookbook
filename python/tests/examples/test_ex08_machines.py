"""ex08: machines -- lifecycle order, finally-cleanup on error, spend gate."""

from __future__ import annotations

import base64
import json
import random
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from coasty import CoastyClient, InsufficientCreditsError, ServerError
from ex08_machines import (
    REMOTE_FILE_CONTENT,
    SpendNotConfirmedError,
    run_lifecycle,
)

BASE_URL = "https://coasty.ai/v1"
MACHINE_ID = "mch_test_a1b2c3d4"
FAKE_LIVE_KEY = "sk-coasty-live-" + "0" * 48  # obviously fake
PNG_BYTES = b"\x89PNG\r\n\x1a\n-fake-cookbook-pixels"


def _fake_clock(values: list[float]) -> Any:
    iterator = iter(values)
    return lambda: next(iterator)


def _mock_machine_endpoints(
    respx_router: respx.MockRouter,
    make_machine: Any,
    make_provision_response: Any,
) -> dict[str, respx.Route]:
    """All happy-path routes: provision starts 'creating' then becomes running."""
    return {
        "provision": respx_router.post(f"{BASE_URL}/machines").mock(
            return_value=httpx.Response(
                201, json=make_provision_response(machine=make_machine(status="creating"))
            )
        ),
        "ttl": respx_router.patch(f"{BASE_URL}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(
                200, json={"machine_id": MACHINE_ID, "ttl_minutes": 30, "request_id": "req_ttl"}
            )
        ),
        "get": respx_router.get(f"{BASE_URL}/machines/{MACHINE_ID}").mock(
            side_effect=[
                httpx.Response(
                    200, json={"machine": make_machine(status="starting"), "request_id": "req_g1"}
                ),
                httpx.Response(
                    200, json={"machine": make_machine(status="running"), "request_id": "req_g2"}
                ),
            ]
        ),
        "screenshot": respx_router.get(f"{BASE_URL}/machines/{MACHINE_ID}/screenshot").mock(
            return_value=httpx.Response(
                200,
                json={
                    "machine_id": MACHINE_ID,
                    "image_b64": base64.b64encode(PNG_BYTES).decode(),
                    "mime_type": "image/png",
                    "width": 1280,
                    "height": 720,
                    "captured_at": "2026-06-01T12:00:30Z",
                    "request_id": "req_shot",
                },
            )
        ),
        "actions": respx_router.post(f"{BASE_URL}/machines/{MACHINE_ID}/actions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "machine_id": MACHINE_ID,
                    "command": "click",
                    "success": True,
                    "result": None,
                    "error": None,
                    "duration_ms": 12,
                    "request_id": "req_act",
                },
            )
        ),
        "batch": respx_router.post(f"{BASE_URL}/machines/{MACHINE_ID}/actions/batch").mock(
            return_value=httpx.Response(
                200,
                json={
                    "machine_id": MACHINE_ID,
                    "results": [{"success": True}] * 3,
                    "completed_count": 3,
                    "failed_count": 0,
                    "aborted": False,
                    "request_id": "req_batch",
                },
            )
        ),
        "terminal": respx_router.post(f"{BASE_URL}/machines/{MACHINE_ID}/terminal").mock(
            return_value=httpx.Response(
                200,
                json={
                    "machine_id": MACHINE_ID,
                    "output": "coasty-cookbook",
                    "exit_code": 0,
                    "request_id": "req_term",
                },
            )
        ),
        "file_write": respx_router.post(f"{BASE_URL}/machines/{MACHINE_ID}/files/write").mock(
            return_value=httpx.Response(200, json={"success": True, "request_id": "req_fw"})
        ),
        "file_read": respx_router.post(f"{BASE_URL}/machines/{MACHINE_ID}/files/read").mock(
            return_value=httpx.Response(
                200, json={"content": REMOTE_FILE_CONTENT, "request_id": "req_fr"}
            )
        ),
        "browser": respx_router.post(f"{BASE_URL}/machines/{MACHINE_ID}/browser/navigate").mock(
            return_value=httpx.Response(200, json={"success": True, "request_id": "req_nav"})
        ),
        "snapshot": respx_router.post(f"{BASE_URL}/machines/{MACHINE_ID}/snapshot").mock(
            return_value=httpx.Response(
                201,
                json={
                    "machine_id": MACHINE_ID,
                    "snapshot_id": "snap_1",
                    "name": "cookbook",
                    "created_at": "2026-06-01T12:01:00Z",
                    "credits_charged": 1,
                    "request_id": "req_snap",
                },
                headers={"X-Coasty-Request-Id": "req_snap", "X-Credits-Charged": "1"},
            )
        ),
        "stop": respx_router.post(f"{BASE_URL}/machines/{MACHINE_ID}/stop").mock(
            return_value=httpx.Response(
                200,
                json={
                    "machine_id": MACHINE_ID,
                    "status": "stopping",
                    "message": "ok",
                    "request_id": "req_stop",
                },
            )
        ),
        "terminate": respx_router.delete(f"{BASE_URL}/machines/{MACHINE_ID}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "machine_id": MACHINE_ID,
                    "status": "terminated",
                    "message": "ok",
                    "request_id": "req_kill",
                },
            )
        ),
    }


def test_lifecycle_happy_path_calls_in_order_and_saves_png(
    client: CoastyClient,
    respx_router: respx.MockRouter,
    make_machine: Any,
    make_provision_response: Any,
    tmp_path: Path,
) -> None:
    routes = _mock_machine_endpoints(respx_router, make_machine, make_provision_response)
    screenshot_path = tmp_path / "shot.png"
    sleeps: list[float] = []
    lines: list[str] = []

    report = run_lifecycle(
        client,
        ttl_minutes=30,
        screenshot_path=screenshot_path,
        poll_interval=0.01,
        max_polls=5,
        clock=_fake_clock([0.0, 1800.0]),  # 30 minutes elapsed
        sleep=sleeps.append,
        printer=lines.append,
    )

    base = f"/v1/machines/{MACHINE_ID}"
    assert [(c.request.method, c.request.url.path) for c in respx_router.calls] == [
        ("POST", "/v1/machines"),
        ("PATCH", base),  # ttl re-armed (spend guard)
        ("GET", base),  # poll: starting
        ("GET", base),  # poll: running
        ("GET", f"{base}/screenshot"),
        ("POST", f"{base}/actions"),  # click
        ("POST", f"{base}/actions"),  # type_text
        ("POST", f"{base}/actions/batch"),
        ("POST", f"{base}/terminal"),
        ("POST", f"{base}/files/write"),
        ("POST", f"{base}/files/read"),
        ("POST", f"{base}/browser/navigate"),
        ("POST", f"{base}/snapshot"),
        ("POST", f"{base}/stop"),  # cleanup: stop first (1 cr/hr)...
        ("DELETE", base),  # ...then terminate (billing ends)
    ]

    # contract details
    provision_body = json.loads(routes["provision"].calls.last.request.content)
    assert provision_body == {
        "display_name": "cookbook-ex08",
        "os_type": "linux",
        "ttl_minutes": 30,
    }
    assert json.loads(routes["ttl"].calls.last.request.content) == {"ttl_minutes": 30}
    batch_body = json.loads(routes["batch"].calls.last.request.content)
    assert batch_body["stop_on_error"] is True
    assert len(batch_body["steps"]) == 3

    # screenshot decoded + saved as PNG bytes
    assert screenshot_path.read_bytes() == PNG_BYTES

    # injected sleeps only (no real waiting) -- two polls before running
    assert sleeps == [0.01, 0.01]

    assert report.machine_id == MACHINE_ID
    assert report.snapshot_id == "snap_1"
    assert report.file_roundtrip_ok is True
    assert report.stopped is True
    assert report.terminated is True
    assert report.elapsed_minutes == pytest.approx(30.0)
    # 30 min of linux running at 5 cr/hr, rounded down -> 2 cr
    assert report.estimated_runtime_credits == 2
    assert any("$0.01" in line or "snapshot" in line.lower() for line in lines)
    assert any("req_snap" in line for line in lines)  # request_id surfaced


def test_lifecycle_error_midway_still_stops_and_terminates(
    client: CoastyClient,
    respx_router: respx.MockRouter,
    make_machine: Any,
    make_provision_response: Any,
    make_error: Any,
) -> None:
    routes = _mock_machine_endpoints(respx_router, make_machine, make_provision_response)
    # machine is running immediately: no polling
    routes["get"].mock(
        return_value=httpx.Response(
            200, json={"machine": make_machine(status="running"), "request_id": "req_g"}
        )
    )
    # the terminal call blows up server-side (unsafe POST: not retried)
    routes["terminal"].mock(
        return_value=httpx.Response(
            500, json=make_error(code="INTERNAL_ERROR", type="server_error", request_id="req_boom")
        )
    )

    with pytest.raises(ServerError) as exc_info:
        run_lifecycle(
            client,
            screenshot_path=None,
            poll_interval=0.0,
            sleep=lambda _: None,
            clock=_fake_clock([0.0, 60.0]),
            printer=lambda _: None,
        )

    # the error surfaced with its code + request_id...
    assert exc_info.value.code == "INTERNAL_ERROR"
    assert exc_info.value.request_id == "req_boom"
    assert routes["terminal"].call_count == 1  # POST without Idempotency-Key: no retry
    # ...and the finally block still cleaned up
    assert routes["stop"].called
    assert routes["terminate"].called
    # nothing after the failing step ran
    assert not routes["snapshot"].called
    assert not routes["browser"].called


def test_spend_gate_blocks_live_key_without_confirm_before_any_http(
    respx_router: respx.MockRouter,
) -> None:
    lines: list[str] = []
    with (
        CoastyClient(api_key=FAKE_LIVE_KEY, base_url=BASE_URL) as live_client,
        pytest.raises(SpendNotConfirmedError, match="--confirm"),
    ):
        run_lifecycle(live_client, confirm_spend=False, printer=lines.append)

    assert len(respx_router.calls) == 0  # blocked BEFORE provisioning
    assert any("cr/hr" in line for line in lines)  # hourly rate printed
    assert any("$0.20" in line for line in lines)  # the wallet >= 20 cr gate


def test_spend_gate_confirmed_live_key_reaches_provisioning(
    respx_router: respx.MockRouter, make_error: Any
) -> None:
    provision = respx_router.post(f"{BASE_URL}/machines").mock(
        return_value=httpx.Response(
            402,
            json=make_error(
                code="INSUFFICIENT_CREDITS",
                type="billing_error",
                required=20,
                balance=3,
                request_id="req_402",
            ),
        )
    )
    with (
        CoastyClient(
            api_key=FAKE_LIVE_KEY, base_url=BASE_URL, sleep=lambda _: None, rng=random.Random(1)
        ) as live_client,
        pytest.raises(InsufficientCreditsError) as exc_info,
    ):
        run_lifecycle(live_client, confirm_spend=True, printer=lambda _: None)

    assert provision.call_count == 1  # the gate let a confirmed spend through
    assert exc_info.value.required == 20
    assert exc_info.value.balance == 3
    assert exc_info.value.request_id == "req_402"
    assert len(respx_router.calls) == 1  # no machine -> no cleanup calls either


def test_sandbox_key_needs_no_confirm(
    client: CoastyClient,
    respx_router: respx.MockRouter,
    make_machine: Any,
    make_provision_response: Any,
) -> None:
    _mock_machine_endpoints(respx_router, make_machine, make_provision_response)
    lines: list[str] = []
    report = run_lifecycle(
        client,  # sandbox key from the fixture; confirm_spend stays False
        screenshot_path=None,
        poll_interval=0.0,
        sleep=lambda _: None,
        clock=_fake_clock([0.0, 0.0]),
        printer=lines.append,
    )
    assert report.terminated is True
    assert any("sandbox" in line for line in lines)  # labeled free
