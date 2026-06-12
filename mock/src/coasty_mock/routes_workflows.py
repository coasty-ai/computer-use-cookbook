"""Workflows: CRUD with version bumps, saved + ad-hoc runs, SSE, approvals.

Mock conventions (documented in mock/README.md):

- Workflow runs execute EAGERLY on create: the create response already shows
  the settled status (``succeeded`` / ``failed`` / ``awaiting_human``); a real
  deployment would return ``queued`` and progress asynchronously.
- Re-using a slug on POST /v1/workflows updates that workflow and bumps its
  version (documented "re-using the same slug bumps its version").
- The static ``/workflows/runs`` subtree is declared before the dynamic
  ``/workflows/{workflow_id}`` routes, exactly as the docs note.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response

from .clock import iso
from .deps import (
    check_idempotency,
    json_body,
    mock_state,
    mode_of,
    request_id,
    store_idempotent,
)
from .errors import ApiError
from .routes_runs import TERMINAL_STATES, validate_webhook_url
from .sse import append_event, cursor_from, parse_drop_after, replay_frames
from .state import TestState
from .validation import Validator, field_dict, field_int, field_str, parse_limit
from .wfdsl import DSL_VERSION, SLUG_RE, validate_definition
from .wfengine import WorkflowEngine, finish_wf_run, notify_wf

JsonDict = dict[str, Any]

router = APIRouter(prefix="/v1")


# --------------------------------------------------------------------- helpers
def _get_workflow(state: TestState, workflow_id: str, mode: str) -> JsonDict:
    workflow = state.workflows.get(workflow_id)
    if workflow is None or workflow["_mode"] != mode:
        raise ApiError("WORKFLOW_NOT_FOUND", f"No workflow {workflow_id!r} for this key.")
    return workflow


def _get_wf_run(state: TestState, run_id: str, mode: str) -> JsonDict:
    run = state.workflow_runs.get(run_id)
    if run is None or run["_mode"] != mode:
        raise ApiError("WORKFLOW_NOT_FOUND", f"No workflow run {run_id!r} for this key.")
    return run


def workflow_public(workflow: JsonDict) -> JsonDict:
    return {k: v for k, v in workflow.items() if not k.startswith("_")}


def wf_run_public(run: JsonDict, *, include_secret: bool = False) -> JsonDict:
    public = {k: v for k, v in run.items() if not k.startswith("_") and k != "webhook_secret"}
    public["webhook_secret"] = run.get("webhook_secret") if include_secret else None
    return public


def _drive(state: TestState, run: JsonDict, reply: JsonDict | None) -> None:
    """Advance the engine generator until it pauses for approval or finishes."""
    gen = state.wf_gens.get(run["id"])
    if gen is None:
        return
    try:
        request = gen.send(reply) if reply is not None else next(gen)
    except StopIteration:
        state.wf_gens.pop(run["id"], None)
        return
    run["status"] = "awaiting_human"
    run["awaiting_step_id"] = request["step_id"]
    run["awaiting_human_reason"] = request["message"]
    log = state.wf_events[run["id"]]
    append_event(log, "status", {"status": "awaiting_human"}, state.clock)
    append_event(
        log,
        "awaiting_human",
        {"step_id": request["step_id"], "message": request["message"]},
        state.clock,
    )
    notify_wf(state, run, "workflow_run.awaiting_human")


async def _start_run(request: Request, workflow: JsonDict | None) -> JsonDict:
    state = mock_state(request)
    body = await json_body(request)
    vd = Validator()
    inputs = field_dict(body, "inputs", vd)
    machine_id = field_str(body, "machine_id", vd, max_len=128)
    budget_cents = field_int(body, "budget_cents", vd, lo=0, hi=10_000_000)
    max_iterations = field_int(body, "max_iterations", vd, lo=1, hi=100_000)
    deadline_seconds = field_int(body, "deadline_seconds", vd, lo=1, hi=86400)
    webhook_url = field_str(body, "webhook_url", vd)
    validate_webhook_url(webhook_url, vd)
    metadata = field_dict(body, "metadata", vd)
    field_dict(body, "inputs_schema", vd)
    vd.raise_if_any()

    if workflow is not None:
        definition: JsonDict = workflow["definition"]
        workflow_id: str | None = str(workflow["id"])
        workflow_version: int | None = int(workflow["version"])
    else:
        raw_definition = body.get("definition")
        validate_definition(raw_definition)
        assert isinstance(raw_definition, dict)
        definition = raw_definition
        workflow_id = None
        workflow_version = None

    cache_key, cached = check_idempotency(request, body, "workflow_runs")
    if cached is not None:
        return cached

    now = state.clock.now()
    run_id = state.next_id("wfrun", "wfr_", 12)
    secret = state.webhook_secret_for(run_id) if webhook_url else None
    run: JsonDict = {
        "id": run_id,
        "object": "workflow.run",
        "status": "queued",
        "workflow_id": workflow_id,
        "workflow_version": workflow_version,
        "machine_id": machine_id,
        "inputs": inputs or {},
        "output": None,
        "error": None,
        "awaiting_human_reason": None,
        "awaiting_step_id": None,
        "iterations_used": 0,
        "spent_cents": 0,
        "budget_cents": budget_cents or 0,
        "webhook_url": webhook_url,
        "webhook_secret": secret,
        "metadata": metadata,
        "created_at": iso(now),
        "started_at": None,
        "finished_at": None,
        "request_id": request_id(request),
        "_mode": mode_of(request),
        "_deadline_seconds": deadline_seconds,
        "_max_iterations": max_iterations,
        "_started_epoch": now,
    }
    state.workflow_runs[run_id] = run
    state.wf_events[run_id] = []
    append_event(state.wf_events[run_id], "status", {"status": "queued"}, state.clock)
    engine = WorkflowEngine(state, run, definition)
    state.wf_gens[run_id] = engine.execute()
    _drive(state, run, None)
    response = wf_run_public(run, include_secret=True)
    store_idempotent(request, cache_key, body, response)
    return response


# ------------------------------------------------------------------- workflows
@router.post("/workflows")
async def create_workflow(request: Request) -> JsonDict:
    state = mock_state(request)
    body = await json_body(request)
    vd = Validator()
    name = field_str(body, "name", vd, required=True, min_len=1, max_len=128)
    slug = field_str(body, "slug", vd, required=True, pattern=SLUG_RE)
    description = field_str(body, "description", vd, max_len=2000)
    inputs_schema = field_dict(body, "inputs_schema", vd)
    metadata = field_dict(body, "metadata", vd)
    if "definition" not in body:
        vd.add(["body", "definition"], "field required", "missing")
    vd.raise_if_any()
    validate_definition(body.get("definition"))
    assert name is not None and slug is not None
    definition = body["definition"]

    mode = mode_of(request)
    existing_id = state.workflow_slugs.get(f"{mode}:{slug}")
    now = iso(state.clock.now())
    if existing_id is not None:
        existing = state.workflows[existing_id]
        existing.update(
            {
                "name": name,
                "definition": definition,
                "inputs_schema": inputs_schema,
                "description": description,
                "metadata": metadata,
                "version": int(existing["version"]) + 1,
                "updated_at": now,
            }
        )
        return {**workflow_public(existing), "request_id": request_id(request)}

    workflow_id = state.next_id("workflow", "wf_", 12)
    workflow: JsonDict = {
        "id": workflow_id,
        "object": "workflow",
        "name": name,
        "slug": slug,
        "version": 1,
        "dsl_version": DSL_VERSION,
        "definition": definition,
        "inputs_schema": inputs_schema,
        "description": description,
        "status": "active",
        "metadata": metadata,
        "created_at": now,
        "updated_at": now,
        "_mode": mode,
    }
    state.workflows[workflow_id] = workflow
    state.workflow_slugs[f"{mode}:{slug}"] = workflow_id
    return {**workflow_public(workflow), "request_id": request_id(request)}


@router.get("/workflows")
def list_workflows(request: Request) -> JsonDict:
    state = mock_state(request)
    limit = parse_limit(request.query_params.get("limit"), default=20)
    mode = mode_of(request)
    workflows = [wf for wf in state.workflows.values() if wf["_mode"] == mode]
    return {
        "object": "list",
        "data": [workflow_public(wf) for wf in workflows[:limit]],
        "has_more": len(workflows) > limit,
        "request_id": request_id(request),
    }


# ---------------------------------------------------- workflow runs (static!)
@router.post("/workflows/runs")
async def start_adhoc_run(request: Request) -> JsonDict:
    return await _start_run(request, None)


@router.get("/workflows/runs")
def list_wf_runs(request: Request) -> JsonDict:
    state = mock_state(request)
    limit = parse_limit(request.query_params.get("limit"), default=20)
    workflow_id = request.query_params.get("workflow_id")
    mode = mode_of(request)
    runs = [run for run in state.workflow_runs.values() if run["_mode"] == mode]
    if workflow_id is not None:
        runs = [run for run in runs if run["workflow_id"] == workflow_id]
    return {
        "object": "list",
        "data": [wf_run_public(run) for run in runs[:limit]],
        "has_more": len(runs) > limit,
        "request_id": request_id(request),
    }


@router.get("/workflows/runs/{run_id}/events")
def wf_run_events(request: Request, run_id: str) -> Response:
    state = mock_state(request)
    _get_wf_run(state, run_id, mode_of(request))
    cursor = cursor_from(request.headers.get("last-event-id"), request.query_params.get("after"))
    drop_after = parse_drop_after(request.query_params.get("drop_after"))
    body = replay_frames(state.wf_events[run_id], cursor, drop_after)
    return Response(
        content=body, media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


@router.get("/workflows/runs/{run_id}")
def get_wf_run(request: Request, run_id: str) -> JsonDict:
    state = mock_state(request)
    run = _get_wf_run(state, run_id, mode_of(request))
    return wf_run_public(run)


@router.post("/workflows/runs/{run_id}/cancel")
def cancel_wf_run(request: Request, run_id: str) -> JsonDict:
    state = mock_state(request)
    run = _get_wf_run(state, run_id, mode_of(request))
    if run["status"] in TERMINAL_STATES:
        raise ApiError(
            "INVALID_STATE",
            f"Workflow run {run_id!r} is already terminal ({run['status']}).",
            extras={
                "current_state": run["status"],
                "allowed_from": ["queued", "running", "awaiting_human"],
            },
        )
    gen = state.wf_gens.pop(run_id, None)
    if gen is not None:
        gen.close()
    finish_wf_run(state, run, "cancelled")
    return wf_run_public(run)


@router.post("/workflows/runs/{run_id}/resume")
async def resume_wf_run(request: Request, run_id: str) -> JsonDict:
    state = mock_state(request)
    run = _get_wf_run(state, run_id, mode_of(request))
    body = await json_body(request)
    vd = Validator()
    note = field_str(body, "note", vd, max_len=2000)
    approved = body.get("approved")
    if not isinstance(approved, bool):
        vd.add(["body", "approved"], "field required: a boolean", "missing")
    vd.raise_if_any()
    assert isinstance(approved, bool)
    if run["status"] in TERMINAL_STATES:
        raise ApiError(
            "RESUME_CONFLICT",
            f"Workflow run {run_id!r} already finished ({run['status']}).",
            extras={"current_state": run["status"], "allowed_from": ["awaiting_human"]},
        )
    if run["status"] != "awaiting_human":
        raise ApiError(
            "NOT_AWAITING_HUMAN",
            f"Workflow run {run_id!r} is {run['status']!r}; resume requires awaiting_human.",
            extras={"current_state": run["status"], "allowed_from": ["awaiting_human"]},
        )
    run["status"] = "running"
    run["awaiting_step_id"] = None
    run["awaiting_human_reason"] = None
    log = state.wf_events[run_id]
    append_event(log, "resumed", {"approved": approved, "note": note}, state.clock)
    append_event(log, "status", {"status": "running"}, state.clock)
    _drive(state, run, {"approved": approved, "note": note})
    return wf_run_public(run)


# --------------------------------------------------- dynamic workflow routes
@router.post("/workflows/{workflow_id}/runs")
async def start_saved_run(request: Request, workflow_id: str) -> JsonDict:
    state = mock_state(request)
    workflow = _get_workflow(state, workflow_id, mode_of(request))
    return await _start_run(request, workflow)


@router.get("/workflows/{workflow_id}")
def get_workflow(request: Request, workflow_id: str) -> JsonDict:
    state = mock_state(request)
    workflow = _get_workflow(state, workflow_id, mode_of(request))
    return {**workflow_public(workflow), "request_id": request_id(request)}


@router.put("/workflows/{workflow_id}")
async def update_workflow(request: Request, workflow_id: str) -> JsonDict:
    state = mock_state(request)
    workflow = _get_workflow(state, workflow_id, mode_of(request))
    body = await json_body(request)
    vd = Validator()
    name = field_str(body, "name", vd, min_len=1, max_len=128)
    description = field_str(body, "description", vd, max_len=2000)
    status = field_str(body, "status", vd, choices={"active", "archived"})
    inputs_schema = field_dict(body, "inputs_schema", vd)
    metadata = field_dict(body, "metadata", vd)
    vd.raise_if_any()
    if "definition" in body:
        validate_definition(body.get("definition"))
        workflow["definition"] = body["definition"]
    if name is not None:
        workflow["name"] = name
    if description is not None:
        workflow["description"] = description
    if status is not None:
        workflow["status"] = status
    if inputs_schema is not None:
        workflow["inputs_schema"] = inputs_schema
    if metadata is not None:
        workflow["metadata"] = metadata
    workflow["version"] = int(workflow["version"]) + 1
    workflow["updated_at"] = iso(state.clock.now())
    return {**workflow_public(workflow), "request_id": request_id(request)}


@router.delete("/workflows/{workflow_id}")
def delete_workflow(request: Request, workflow_id: str) -> JsonDict:
    state = mock_state(request)
    workflow = _get_workflow(state, workflow_id, mode_of(request))
    workflow["status"] = "archived"
    workflow["updated_at"] = iso(state.clock.now())
    return {**workflow_public(workflow), "request_id": request_id(request)}
