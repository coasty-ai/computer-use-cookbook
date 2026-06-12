"""ex07: workflows -- full DSL coverage, contract bodies, SSE + approval flow."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

import httpx
import pytest
import respx

from coasty import CoastyClient, dsl
from ex07_workflows import (
    WORKFLOW_NAME,
    WORKFLOW_SLUG,
    SpendNotConfirmedError,
    build_definition,
    count_task_executions,
    ensure_spend_allowed,
    estimate_cost,
    run_workflow,
)

BASE_URL = "https://coasty.ai/v1"
WORKFLOW_ID = "wf_test_1"
RUN_ID = "wfr_test_1"
INPUTS = {"month": "2026-06", "invoice_count": 3}
FAKE_LIVE_KEY = "sk-coasty-live-" + "0" * 48  # obviously fake


def body_of(route: respx.Route) -> dict[str, Any]:
    return json.loads(route.calls.last.request.content)  # type: ignore[no-any-return]


# ── authored definition: validates locally and uses the WHOLE DSL ──────────


def _collect(steps: Sequence[Mapping[str, Any]], types: set[str], ops: set[str]) -> None:
    for step in steps:
        types.add(str(step["type"]))
        for key in ("condition", "while"):
            if key in step:
                _collect_ops(step[key], ops)
        for key in ("then", "else", "body"):
            if key in step:
                _collect(step[key], types, ops)
        for branch in step.get("branches", []):
            _collect(branch, types, ops)


def _collect_ops(condition: Mapping[str, Any], ops: set[str]) -> None:
    ops.add(str(condition["op"]))
    for child in condition.get("conditions", []):
        _collect_ops(child, ops)
    if "condition" in condition:
        _collect_ops(condition["condition"], ops)


def test_definition_passes_validate_and_exercises_all_nine_step_types() -> None:
    definition = build_definition()
    dsl.validate(definition)  # must not raise

    types: set[str] = set()
    ops: set[str] = set()
    _collect(definition["steps"], types, ops)

    assert types == set(dsl.STEP_TYPES)  # task/assert/if/loop/parallel/
    #                                       human_approval/retry/succeed/fail
    assert ops <= dsl.CONDITION_OPS  # only documented condition ops
    assert {"eq", "and", "not", "contains", "truthy", "gte"} <= ops


def test_task_execution_count_and_cost_estimate() -> None:
    definition = build_definition()
    # export(1) + upload(1) + spot_check(1) + reconcile_one x3 + 2 notify = 8
    assert count_task_executions(definition["steps"]) == 8
    estimate = estimate_cost(definition, assumed_agent_steps_per_task=3)
    assert estimate.credits == 8 * 3 * 5  # 120 cr = $1.20 on v3
    assert estimate.usd == pytest.approx(1.20)


# ── end-to-end against mocked endpoints ────────────────────────────────────


def _mock_workflow_endpoints(
    respx_router: respx.MockRouter,
    make_workflow: Any,
    make_workflow_run: Any,
    sse_body: Any,
    *,
    final_status: str = "succeeded",
    spent_cents: int = 40,
) -> dict[str, respx.Route]:
    frames = sse_body(
        [
            (1, "status", '{"status":"running"}'),
            (2, "awaiting_human", '{"step_id":"approve_payouts","message":"Approve?"}'),
            (3, "resumed", "{}"),
            (4, "billing", '{"cost_cents":40}'),
            (5, "status", json.dumps({"status": final_status})),
            (6, "done", "{}"),
        ]
    )
    return {
        "create": respx_router.post(f"{BASE_URL}/workflows").mock(
            return_value=httpx.Response(201, json=make_workflow(id=WORKFLOW_ID))
        ),
        "start": respx_router.post(f"{BASE_URL}/workflows/{WORKFLOW_ID}/runs").mock(
            return_value=httpx.Response(201, json=make_workflow_run(id=RUN_ID, budget_cents=500))
        ),
        "events": respx_router.get(f"{BASE_URL}/workflows/runs/{RUN_ID}/events").mock(
            return_value=httpx.Response(
                200, text=frames, headers={"Content-Type": "text/event-stream"}
            )
        ),
        "resume": respx_router.post(f"{BASE_URL}/workflows/runs/{RUN_ID}/resume").mock(
            return_value=httpx.Response(200, json=make_workflow_run(id=RUN_ID, status="running"))
        ),
        "final": respx_router.get(f"{BASE_URL}/workflows/runs/{RUN_ID}").mock(
            return_value=httpx.Response(
                200,
                json=make_workflow_run(
                    id=RUN_ID, status=final_status, spent_cents=spent_cents, budget_cents=500
                ),
                headers={"X-Coasty-Request-Id": "req_test_wfr"},
            )
        ),
    }


def test_run_workflow_contract_and_approval(
    client: CoastyClient,
    respx_router: respx.MockRouter,
    make_workflow: Any,
    make_workflow_run: Any,
    sse_body: Any,
) -> None:
    routes = _mock_workflow_endpoints(respx_router, make_workflow, make_workflow_run, sse_body)
    lines: list[str] = []

    outcome = run_workflow(
        client,
        inputs=INPUTS,
        budget_cents=500,
        max_iterations=50,
        approve=True,
        approval_note="lgtm",
        reconnect_delay=0.0,
        printer=lines.append,
    )

    create_body = body_of(routes["create"])
    assert create_body["name"] == WORKFLOW_NAME
    assert create_body["slug"] == WORKFLOW_SLUG
    assert create_body["definition"] == build_definition()  # exact authored DSL on the wire

    assert body_of(routes["start"]) == {
        "inputs": INPUTS,
        "budget_cents": 500,
        "max_iterations": 50,
    }
    # the human_approval was approved with the documented body shape
    assert routes["resume"].call_count == 1
    assert body_of(routes["resume"]) == {"approved": True, "note": "lgtm"}

    assert outcome.status == "succeeded"
    assert outcome.spent_cents == 40
    assert outcome.budget_cents == 500
    assert outcome.resumed_step_ids == ("approve_payouts",)
    assert outcome.final_request_id == "req_test_wfr"
    assert any("spent 40 of 500 budget cents" in line for line in lines)


def test_run_workflow_rejection_path_sends_approved_false(
    client: CoastyClient,
    respx_router: respx.MockRouter,
    make_workflow: Any,
    make_workflow_run: Any,
    sse_body: Any,
) -> None:
    routes = _mock_workflow_endpoints(
        respx_router, make_workflow, make_workflow_run, sse_body, final_status="failed"
    )
    outcome = run_workflow(
        client,
        inputs=INPUTS,
        approve=False,  # the --reject path
        reconnect_delay=0.0,
        printer=lambda _: None,
    )
    assert body_of(routes["resume"]) == {"approved": False}  # False must NOT be dropped
    assert outcome.status == "failed"
    assert outcome.approved is False


class _DroppingStream(httpx.SyncByteStream):
    """Yields some bytes then dies with a transport error (mid-stream drop)."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __iter__(self) -> Iterator[bytes]:
        yield self._payload
        raise httpx.ReadError("connection dropped mid-stream")


def test_run_workflow_sse_reconnects_and_resumes_exactly_once(
    client: CoastyClient,
    respx_router: respx.MockRouter,
    make_workflow: Any,
    make_workflow_run: Any,
    sse_body: Any,
) -> None:
    first = sse_body(
        [
            (1, "status", '{"status":"running"}'),
            (2, "awaiting_human", '{"step_id":"approve_payouts"}'),
        ]
    )
    second = sse_body(
        [
            (2, "awaiting_human", '{"step_id":"approve_payouts"}'),  # replay: deduped
            (3, "resumed", "{}"),
            (4, "status", '{"status":"succeeded"}'),
            (5, "done", "{}"),
        ]
    )
    respx_router.post(f"{BASE_URL}/workflows").mock(
        return_value=httpx.Response(201, json=make_workflow(id=WORKFLOW_ID))
    )
    respx_router.post(f"{BASE_URL}/workflows/{WORKFLOW_ID}/runs").mock(
        return_value=httpx.Response(201, json=make_workflow_run(id=RUN_ID))
    )
    events_route = respx_router.get(f"{BASE_URL}/workflows/runs/{RUN_ID}/events").mock(
        side_effect=[
            httpx.Response(
                200,
                stream=_DroppingStream(first.encode()),
                headers={"Content-Type": "text/event-stream"},
            ),
            httpx.Response(200, text=second, headers={"Content-Type": "text/event-stream"}),
        ]
    )
    resume_route = respx_router.post(f"{BASE_URL}/workflows/runs/{RUN_ID}/resume").mock(
        return_value=httpx.Response(200, json=make_workflow_run(id=RUN_ID, status="running"))
    )
    respx_router.get(f"{BASE_URL}/workflows/runs/{RUN_ID}").mock(
        return_value=httpx.Response(
            200, json=make_workflow_run(id=RUN_ID, status="succeeded", spent_cents=10)
        )
    )

    outcome = run_workflow(
        client, inputs=INPUTS, approve=True, reconnect_delay=0.0, printer=lambda _: None
    )

    assert events_route.call_count == 2
    assert "Last-Event-ID" not in events_route.calls[0].request.headers
    assert events_route.calls[1].request.headers["Last-Event-ID"] == "2"  # seq cursor
    assert resume_route.call_count == 1  # the replayed awaiting_human did not double-resume
    assert outcome.status == "succeeded"


# ── spend gate ─────────────────────────────────────────────────────────────


def test_spend_gate_sandbox_key_passes_and_labels_zero_dollars() -> None:
    lines: list[str] = []
    estimate = estimate_cost(build_definition())
    ensure_spend_allowed(
        "sk-coasty-test-" + "0" * 48, estimate, confirm=False, printer=lines.append
    )
    assert any("sandbox" in line for line in lines)


def test_spend_gate_blocks_live_key_without_confirm() -> None:
    estimate = estimate_cost(build_definition())
    with pytest.raises(SpendNotConfirmedError, match="--confirm"):
        ensure_spend_allowed(FAKE_LIVE_KEY, estimate, confirm=False, printer=lambda _: None)
    # explicit consent unblocks
    ensure_spend_allowed(FAKE_LIVE_KEY, estimate, confirm=True, printer=lambda _: None)
