"""Client contract tests: workflows CRUD + saved/ad-hoc runs + lifecycle."""

from __future__ import annotations

import json
from typing import Any

import httpx
import respx

from coasty import CoastyClient

BASE_URL = "https://coasty.ai/v1"
DEFINITION = {"steps": [{"id": "t1", "type": "task", "task": "Do the thing"}]}


def body_of(route: respx.Route) -> dict[str, Any]:
    return json.loads(route.calls.last.request.content)  # type: ignore[no-any-return]


def test_create_workflow_contract(
    client: CoastyClient, respx_router: respx.MockRouter, make_workflow: Any
) -> None:
    route = respx_router.post(f"{BASE_URL}/workflows").mock(
        return_value=httpx.Response(201, json=make_workflow(definition=DEFINITION))
    )
    result = client.create_workflow(
        "Invoice reconciliation",
        "invoice-reconcile",
        DEFINITION,
        description="Monthly reconciliation",
        inputs_schema={"type": "object"},
    )
    assert body_of(route) == {
        "name": "Invoice reconciliation",
        "slug": "invoice-reconcile",
        "definition": DEFINITION,
        "inputs_schema": {"type": "object"},
        "description": "Monthly reconciliation",
    }
    assert result.data["slug"] == "invoice-reconcile"
    assert result.data["dsl_version"] == "2026-06-01"


def test_get_and_list_workflows(
    client: CoastyClient, respx_router: respx.MockRouter, make_workflow: Any
) -> None:
    respx_router.get(f"{BASE_URL}/workflows/wf_test_1").mock(
        return_value=httpx.Response(200, json=make_workflow())
    )
    respx_router.get(f"{BASE_URL}/workflows", params={"limit": "10"}).mock(
        return_value=httpx.Response(
            200, json={"object": "list", "data": [make_workflow()], "has_more": False}
        )
    )
    assert client.get_workflow("wf_test_1").data["id"] == "wf_test_1"
    listed = client.list_workflows(limit=10)
    assert [workflow["id"] for workflow in listed.data["data"]] == ["wf_test_1"]


def test_update_workflow_bumps_version(
    client: CoastyClient, respx_router: respx.MockRouter, make_workflow: Any
) -> None:
    route = respx_router.put(f"{BASE_URL}/workflows/wf_test_1").mock(
        return_value=httpx.Response(200, json=make_workflow(version=2))
    )
    result = client.update_workflow("wf_test_1", definition=DEFINITION, description="updated")
    assert route.calls.last.request.method == "PUT"
    assert body_of(route) == {"definition": DEFINITION, "description": "updated"}
    assert result.data["version"] == 2


def test_delete_workflow(client: CoastyClient, respx_router: respx.MockRouter) -> None:
    route = respx_router.delete(f"{BASE_URL}/workflows/wf_test_1").mock(
        return_value=httpx.Response(
            200, json={"id": "wf_test_1", "status": "archived", "request_id": "req_del"}
        )
    )
    result = client.delete_workflow("wf_test_1")
    assert route.called
    assert result.data["status"] == "archived"


def test_start_saved_workflow_run(
    client: CoastyClient, respx_router: respx.MockRouter, make_workflow_run: Any
) -> None:
    route = respx_router.post(f"{BASE_URL}/workflows/wf_test_1/runs").mock(
        return_value=httpx.Response(201, json=make_workflow_run(webhook_secret="whsec_wf"))
    )
    result = client.start_workflow_run(
        "wf_test_1",
        inputs={"month": "2026-05"},
        machine_id="mch_test_a1b2c3d4",
        budget_cents=500,
        max_iterations=100,
        deadline_seconds=3600,
        metadata={"source": "tests"},
        idempotency_key="wf-run-1",
    )
    assert route.calls.last.request.headers["Idempotency-Key"] == "wf-run-1"
    assert body_of(route) == {
        "inputs": {"month": "2026-05"},
        "machine_id": "mch_test_a1b2c3d4",
        "budget_cents": 500,
        "max_iterations": 100,
        "deadline_seconds": 3600,
        "metadata": {"source": "tests"},
    }
    assert result.data["object"] == "workflow.run"
    assert result.data["webhook_secret"] == "whsec_wf"  # returned once


def test_start_adhoc_workflow_run_inlines_definition(
    client: CoastyClient, respx_router: respx.MockRouter, make_workflow_run: Any
) -> None:
    route = respx_router.post(f"{BASE_URL}/workflows/runs").mock(
        return_value=httpx.Response(201, json=make_workflow_run(workflow_id=None))
    )
    client.start_adhoc_workflow_run(
        DEFINITION,
        inputs={"x": 1},
        inputs_schema={"type": "object"},
        budget_cents=200,
    )
    assert body_of(route) == {
        "inputs": {"x": 1},
        "budget_cents": 200,
        "definition": DEFINITION,
        "inputs_schema": {"type": "object"},
    }


def test_get_and_list_workflow_runs(
    client: CoastyClient, respx_router: respx.MockRouter, make_workflow_run: Any
) -> None:
    respx_router.get(f"{BASE_URL}/workflows/runs/wfr_test_1").mock(
        return_value=httpx.Response(200, json=make_workflow_run(status="running"))
    )
    list_route = respx_router.get(
        f"{BASE_URL}/workflows/runs", params={"workflow_id": "wf_test_1", "limit": "3"}
    ).mock(
        return_value=httpx.Response(
            200, json={"object": "list", "data": [make_workflow_run()], "has_more": True}
        )
    )
    assert client.get_workflow_run("wfr_test_1").data["status"] == "running"
    listed = client.list_workflow_runs(workflow_id="wf_test_1", limit=3)
    assert list_route.called
    assert listed.data["has_more"] is True


def test_cancel_workflow_run(
    client: CoastyClient, respx_router: respx.MockRouter, make_workflow_run: Any
) -> None:
    route = respx_router.post(f"{BASE_URL}/workflows/runs/wfr_test_1/cancel").mock(
        return_value=httpx.Response(200, json=make_workflow_run(status="cancelled"))
    )
    result = client.cancel_workflow_run("wfr_test_1")
    assert route.called
    assert result.data["status"] == "cancelled"


def test_resume_workflow_run_approval_and_rejection(
    client: CoastyClient, respx_router: respx.MockRouter, make_workflow_run: Any
) -> None:
    route = respx_router.post(f"{BASE_URL}/workflows/runs/wfr_test_1/resume").mock(
        return_value=httpx.Response(200, json=make_workflow_run(status="running"))
    )
    client.resume_workflow_run("wfr_test_1", approved=True, note="ship it")
    assert body_of(route) == {"approved": True, "note": "ship it"}

    client.resume_workflow_run("wfr_test_1", approved=False)
    assert body_of(route) == {"approved": False}  # False must NOT be dropped
