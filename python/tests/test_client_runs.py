"""Client contract tests: task runs (create / get / list / cancel / resume)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from coasty import CoastyClient, ConflictError, NotFoundError

BASE_URL = "https://coasty.ai/v1"


def body_of(route: respx.Route) -> dict[str, Any]:
    return json.loads(route.calls.last.request.content)  # type: ignore[no-any-return]


def test_create_run_contract_with_idempotency_key(
    client: CoastyClient, respx_router: respx.MockRouter, make_run: Any
) -> None:
    payload = make_run(webhook_url="https://example.com/hook", webhook_secret="whsec_once")
    route = respx_router.post(f"{BASE_URL}/runs").mock(
        return_value=httpx.Response(201, json=payload)
    )
    result = client.create_run(
        "mch_test_a1b2c3d4",
        "Open the billing page",
        cua_version="v4",
        instructions="extra guidance",
        max_steps=30,
        deadline_seconds=900,
        on_awaiting_human="pause",
        webhook_url="https://example.com/hook",
        metadata={"ticket": "OPS-7"},
        idempotency_key="run-create-1",
    )
    request = route.calls.last.request
    assert request.headers["Idempotency-Key"] == "run-create-1"
    assert body_of(route) == {
        "machine_id": "mch_test_a1b2c3d4",
        "task": "Open the billing page",
        "cua_version": "v4",
        "instructions": "extra guidance",
        "max_steps": 30,
        "deadline_seconds": 900,
        "on_awaiting_human": "pause",
        "webhook_url": "https://example.com/hook",
        "metadata": {"ticket": "OPS-7"},
    }
    # webhook_secret arrives exactly once, on create
    assert result.data["webhook_secret"] == "whsec_once"
    assert result.data["object"] == "agent.run"


def test_create_run_minimal_body(
    client: CoastyClient, respx_router: respx.MockRouter, make_run: Any
) -> None:
    route = respx_router.post(f"{BASE_URL}/runs").mock(
        return_value=httpx.Response(201, json=make_run())
    )
    client.create_run("mch_test_a1b2c3d4", "do it")
    assert body_of(route) == {"machine_id": "mch_test_a1b2c3d4", "task": "do it"}


def test_get_run(client: CoastyClient, respx_router: respx.MockRouter, make_run: Any) -> None:
    respx_router.get(f"{BASE_URL}/runs/run_test_1").mock(
        return_value=httpx.Response(200, json=make_run(status="running", steps_completed=3))
    )
    result = client.get_run("run_test_1")
    assert result.data["status"] == "running"
    assert result.data["steps_completed"] == 3


def test_get_run_404_raises_not_found_with_request_id(
    client: CoastyClient, respx_router: respx.MockRouter, make_error: Any
) -> None:
    respx_router.get(f"{BASE_URL}/runs/run_missing").mock(
        return_value=httpx.Response(
            404,
            json=make_error(code="RUN_NOT_FOUND", type="not_found_error", request_id="req_404"),
        )
    )
    with pytest.raises(NotFoundError) as exc_info:
        client.get_run("run_missing")
    assert exc_info.value.code == "RUN_NOT_FOUND"
    assert exc_info.value.request_id == "req_404"


def test_list_runs_with_status_and_limit(
    client: CoastyClient, respx_router: respx.MockRouter, make_run: Any
) -> None:
    route = respx_router.get(f"{BASE_URL}/runs", params={"status": "running", "limit": "5"}).mock(
        return_value=httpx.Response(
            200,
            json={
                "object": "list",
                "data": [make_run(status="running")],
                "has_more": False,
                "request_id": "req_list",
            },
        )
    )
    result = client.list_runs(status="running", limit=5)
    assert route.called
    assert result.data["object"] == "list"
    assert result.data["has_more"] is False
    assert [run["id"] for run in result.data["data"]] == ["run_test_1"]


def test_cancel_run(client: CoastyClient, respx_router: respx.MockRouter, make_run: Any) -> None:
    route = respx_router.post(f"{BASE_URL}/runs/run_test_1/cancel").mock(
        return_value=httpx.Response(200, json=make_run(status="cancelled"))
    )
    result = client.cancel_run("run_test_1")
    assert route.called
    assert result.data["status"] == "cancelled"


def test_resume_run_sends_note(
    client: CoastyClient, respx_router: respx.MockRouter, make_run: Any
) -> None:
    route = respx_router.post(f"{BASE_URL}/runs/run_test_1/resume").mock(
        return_value=httpx.Response(200, json=make_run(status="running"))
    )
    client.resume_run("run_test_1", note="solved the captcha")
    assert body_of(route) == {"note": "solved the captcha"}


def test_resume_run_without_note_sends_empty_body(
    client: CoastyClient, respx_router: respx.MockRouter, make_run: Any
) -> None:
    route = respx_router.post(f"{BASE_URL}/runs/run_test_1/resume").mock(
        return_value=httpx.Response(200, json=make_run(status="running"))
    )
    client.resume_run("run_test_1")
    assert body_of(route) == {}


def test_resume_run_409_not_awaiting_human(
    client: CoastyClient, respx_router: respx.MockRouter, make_error: Any
) -> None:
    respx_router.post(f"{BASE_URL}/runs/run_test_1/resume").mock(
        return_value=httpx.Response(
            409,
            json=make_error(
                code="NOT_AWAITING_HUMAN",
                type="state_error",
                current_state="running",
                allowed_from=["awaiting_human"],
            ),
        )
    )
    with pytest.raises(ConflictError) as exc_info:
        client.resume_run("run_test_1")
    error = exc_info.value
    assert error.code == "NOT_AWAITING_HUMAN"
    assert error.current_state == "running"
    assert error.allowed_from == ["awaiting_human"]
    assert error.status_code == 409
