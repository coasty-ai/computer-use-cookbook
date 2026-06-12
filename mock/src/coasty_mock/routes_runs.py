"""Task runs: state machine, per-step billing, durable SSE events, webhooks.

Mock conventions (documented in mock/README.md):

- The run only progresses when you observe it: every ``GET /v1/runs/{id}``
  advances exactly one tick (queued->running, then one agent step per poll);
  reading ``GET /v1/runs/{id}/events`` advances the run as far as it can go
  (to a terminal state, or to ``awaiting_human``) and then closes the stream.
- A run succeeds after ``config.run_success_steps`` steps (default 3) with
  ``result: {passed, status, summary}``.
- Task markers: ``[pause]`` pauses after step 1 (honoring ``on_awaiting_human``
  pause|fail|cancel); ``[fail]`` makes the final verdict a failure.
- Each step debits the wallet (live keys; test keys bill 0) and appends
  tool_call / tool_result / step / billing events.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response

from .clock import iso
from .deps import (
    check_idempotency,
    debit_wallet,
    json_body,
    mock_state,
    mode_of,
    request_id,
    store_idempotent,
)
from .errors import ApiError
from .pricing import run_step_price
from .routes_core import CUA_VERSIONS
from .sse import append_event, cursor_from, parse_drop_after, replay_frames
from .state import TestState
from .validation import (
    Validator,
    field_dict,
    field_int,
    field_str,
    parse_limit,
    reject_unknown_fields,
)
from .webhooks import emit_webhook, is_local_url

JsonDict = dict[str, Any]

router = APIRouter(prefix="/v1")

TERMINAL_STATES = {"succeeded", "failed", "cancelled", "timed_out"}
RUN_STATUSES = ["queued", "running", "awaiting_human", *sorted(TERMINAL_STATES)]
_ALLOWED_CREATE_FIELDS = {
    "machine_id",
    "task",
    "cua_version",
    "instructions",
    "system_prompt",
    "max_steps",
    "deadline_seconds",
    "on_awaiting_human",
    "awaiting_human_timeout_seconds",
    "webhook_url",
    "metadata",
}


def validate_webhook_url(url: str | None, vd: Validator) -> None:
    """https only — except loopback http URLs, so offline tests can receive posts."""
    if url is None:
        return
    if url.startswith("https://"):
        return
    if url.startswith("http://") and is_local_url(url):
        return
    vd.add(["body", "webhook_url"], "must be an https URL (or a loopback http URL)")


def run_public(run: JsonDict, *, include_secret: bool = False) -> JsonDict:
    public = {k: v for k, v in run.items() if not k.startswith("_") and k != "webhook_secret"}
    public["webhook_secret"] = run.get("webhook_secret") if include_secret else None
    return public


def _get_run(state: TestState, run_id: str, mode: str) -> JsonDict:
    run = state.runs.get(run_id)
    if run is None or run["_mode"] != mode:
        raise ApiError("RUN_NOT_FOUND", f"No run {run_id!r} for this key.")
    return run


def _emit(state: TestState, run: JsonDict, event_type: str, data: JsonDict) -> None:
    append_event(state.run_events[run["id"]], event_type, data, state.clock)


def _notify(state: TestState, run: JsonDict, event: str) -> None:
    url = run.get("webhook_url")
    secret = run.get("webhook_secret")
    if not url or not secret:
        return
    emit_webhook(
        state,
        url=str(url),
        secret=str(secret),
        payload={
            "event": event,
            "run_id": run["id"],
            "status": run["status"],
            "result": run["result"],
            "error": run["error"],
            "awaiting_human_reason": run["awaiting_human_reason"],
            "created_at": iso(state.clock.now()),
        },
    )


def finish_run(
    state: TestState,
    run: JsonDict,
    status: str,
    *,
    result: JsonDict | None = None,
    error: JsonDict | None = None,
) -> None:
    run["status"] = status
    run["result"] = result
    run["error"] = error
    run["finished_at"] = iso(state.clock.now())
    _emit(state, run, "status", {"status": status})
    _emit(state, run, "done", {"status": status, "result": result, "error": error})
    _notify(state, run, f"run.{status}")


def _pause_run(state: TestState, run: JsonDict, reason: str) -> None:
    run["status"] = "awaiting_human"
    run["awaiting_human_reason"] = reason
    run["awaiting_human_since"] = iso(state.clock.now())
    run["_paused_once"] = True
    _emit(state, run, "status", {"status": "awaiting_human"})
    _emit(state, run, "awaiting_human", {"reason": reason})
    _notify(state, run, "run.awaiting_human")


def advance_run(state: TestState, run: JsonDict) -> None:
    """Advance the run state machine by exactly one tick."""
    status = str(run["status"])
    if status in TERMINAL_STATES or status == "awaiting_human":
        return
    state.clock.advance(state.config.run_step_seconds)
    now = state.clock.now()

    if status == "queued":
        run["status"] = "running"
        run["started_at"] = iso(now)
        _emit(state, run, "status", {"status": "running"})
        return

    deadline = run.get("deadline_seconds")
    if deadline is not None and now - float(run["_created_epoch"]) > float(deadline):
        finish_run(
            state,
            run,
            "timed_out",
            error={"code": "DEADLINE_EXCEEDED", "message": "deadline_seconds was breached."},
        )
        return

    step = int(run["steps_completed"]) + 1
    price = int(run["_step_price"])
    if not debit_wallet(state, mode=str(run["_mode"]), credits=price, endpoint="runs.step"):
        finish_run(
            state,
            run,
            "failed",
            error={"code": "WALLET_EXHAUSTED", "message": "API wallet ran dry mid-run."},
        )
        return
    run["steps_completed"] = step
    run["credits_charged"] = int(run["credits_charged"]) + price
    run["cost_cents"] = int(run["cost_cents"]) + price
    task = str(run["task"]).lower()
    _emit(
        state,
        run,
        "tool_call",
        {"step": step, "action_type": "click", "params": {"x": 640, "y": 360}},
    )
    _emit(state, run, "tool_result", {"step": step, "success": True})
    _emit(state, run, "step", {"steps_completed": step})
    _emit(
        state,
        run,
        "billing",
        {"credits_charged": run["credits_charged"], "cost_cents": run["cost_cents"]},
    )

    if "[pause]" in task and step == 1 and not run["_paused_once"]:
        behaviour = str(run["on_awaiting_human"])
        reason = "Task marked [pause]: human takeover required."
        if behaviour == "pause":
            _pause_run(state, run, reason)
        elif behaviour == "fail":
            finish_run(
                state,
                run,
                "failed",
                result={"passed": False, "status": "failed", "summary": reason},
                error={"code": "AWAITING_HUMAN", "message": reason},
            )
        else:  # cancel
            finish_run(state, run, "cancelled", error={"code": "AWAITING_HUMAN", "message": reason})
        return

    success_steps = state.config.run_success_steps
    if step >= int(run["max_steps"]) and step < success_steps:
        finish_run(
            state,
            run,
            "failed",
            result={"passed": False, "status": "failed", "summary": "Ran out of steps."},
            error={"code": "MAX_STEPS_REACHED", "message": "max_steps reached before success."},
        )
        return
    if step >= success_steps:
        if "[fail]" in task:
            summary = f"Mock failure forced by [fail] marker after {step} steps."
            finish_run(
                state,
                run,
                "failed",
                result={"passed": False, "status": "failed", "summary": summary},
                error={"code": "TASK_FAILED", "message": summary},
            )
        else:
            summary = f"Completed task in {step} steps."
            finish_run(
                state,
                run,
                "succeeded",
                result={"passed": True, "status": "succeeded", "summary": summary},
            )


def advance_to_rest(state: TestState, run: JsonDict) -> None:
    """Advance until the run cannot progress further (terminal or paused)."""
    guard = 0
    limit = int(run["max_steps"]) + 8
    while run["status"] not in TERMINAL_STATES and run["status"] != "awaiting_human":
        advance_run(state, run)
        guard += 1
        if guard > limit:  # pragma: no cover - defensive
            raise ApiError("INTERNAL_ERROR", f"Run {run['id']} did not settle after {guard} ticks.")


@router.post("/runs")
async def create_run(request: Request) -> JsonDict:
    state = mock_state(request)
    body = await json_body(request)
    vd = Validator()
    reject_unknown_fields(body, _ALLOWED_CREATE_FIELDS, vd)
    machine_id = field_str(body, "machine_id", vd, required=True, min_len=1, max_len=128)
    task = field_str(body, "task", vd, required=True, min_len=1, max_len=16000)
    cua_version = field_str(body, "cua_version", vd, default="v3", choices=CUA_VERSIONS)
    instructions = field_str(body, "instructions", vd, max_len=16000)
    field_str(body, "system_prompt", vd, max_len=32000)
    max_steps = field_int(body, "max_steps", vd, default=50, lo=1, hi=1000)
    deadline_seconds = field_int(body, "deadline_seconds", vd, lo=1, hi=86400)
    on_awaiting_human = field_str(
        body, "on_awaiting_human", vd, default="pause", choices={"pause", "fail", "cancel"}
    )
    field_int(body, "awaiting_human_timeout_seconds", vd, lo=1, hi=86400)
    webhook_url = field_str(body, "webhook_url", vd)
    validate_webhook_url(webhook_url, vd)
    metadata = field_dict(body, "metadata", vd, max_keys=50)
    vd.raise_if_any()
    assert machine_id is not None and task is not None and cua_version is not None
    assert max_steps is not None and on_awaiting_human is not None

    cache_key, cached = check_idempotency(request, body, "runs")
    if cached is not None:
        return cached

    step_price = run_step_price(cua_version)
    mode = mode_of(request)
    if mode != "test" and state.wallet_balance_cents < step_price:
        raise ApiError(
            "INSUFFICIENT_CREDITS",
            f"Starting a run needs at least {step_price} credits in the wallet.",
            extras={"required": step_price, "balance": state.wallet_balance_cents},
        )

    now = state.clock.now()
    run_id = state.next_id("run", "run_", 12)
    secret = state.webhook_secret_for(run_id) if webhook_url else None
    run: JsonDict = {
        "id": run_id,
        "object": "agent.run",
        "status": "queued",
        "machine_id": machine_id,
        "task": task,
        "cua_version": cua_version,
        "instructions": instructions,
        "max_steps": max_steps,
        "deadline_seconds": deadline_seconds,
        "on_awaiting_human": on_awaiting_human,
        "steps_completed": 0,
        "credits_charged": 0,
        "cost_cents": 0,
        "result": None,
        "error": None,
        "awaiting_human_reason": None,
        "metadata": metadata,
        "webhook_url": webhook_url,
        "webhook_secret": secret,
        "created_at": iso(now),
        "started_at": None,
        "awaiting_human_since": None,
        "finished_at": None,
        "request_id": request_id(request),
        "_mode": mode,
        "_created_epoch": now,
        "_step_price": step_price,
        "_paused_once": False,
    }
    state.runs[run_id] = run
    state.run_events[run_id] = []
    _emit(state, run, "status", {"status": "queued"})
    response = run_public(run, include_secret=True)
    store_idempotent(request, cache_key, body, response)
    return response


@router.get("/runs")
def list_runs(request: Request) -> JsonDict:
    state = mock_state(request)
    status = request.query_params.get("status")
    if status is not None and status not in RUN_STATUSES:
        raise ApiError(
            "INVALID_STATUS_FILTER",
            f"{status!r} is not a valid run status.",
            extras={"valid_options": RUN_STATUSES},
        )
    limit = parse_limit(request.query_params.get("limit"), default=20)
    mode = mode_of(request)
    runs = [run for run in state.runs.values() if run["_mode"] == mode]
    if status is not None:
        runs = [run for run in runs if run["status"] == status]
    return {
        "object": "list",
        "data": [run_public(run) for run in runs[:limit]],
        "has_more": len(runs) > limit,
        "request_id": request_id(request),
    }


@router.get("/runs/{run_id}/events")
def run_events(request: Request, run_id: str) -> Response:
    state = mock_state(request)
    run = _get_run(state, run_id, mode_of(request))
    advance_to_rest(state, run)
    cursor = cursor_from(request.headers.get("last-event-id"), request.query_params.get("after"))
    drop_after = parse_drop_after(request.query_params.get("drop_after"))
    body = replay_frames(state.run_events[run_id], cursor, drop_after)
    return Response(
        content=body,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/runs/{run_id}")
def get_run(request: Request, run_id: str) -> JsonDict:
    state = mock_state(request)
    run = _get_run(state, run_id, mode_of(request))
    advance_run(state, run)
    return run_public(run)


@router.post("/runs/{run_id}/cancel")
def cancel_run(request: Request, run_id: str) -> JsonDict:
    state = mock_state(request)
    run = _get_run(state, run_id, mode_of(request))
    if run["status"] in TERMINAL_STATES:
        raise ApiError(
            "INVALID_STATE",
            f"Run {run_id!r} is already terminal ({run['status']}).",
            extras={
                "current_state": run["status"],
                "allowed_from": ["queued", "running", "awaiting_human"],
            },
        )
    finish_run(state, run, "cancelled")
    return run_public(run)


@router.post("/runs/{run_id}/resume")
async def resume_run(request: Request, run_id: str) -> JsonDict:
    state = mock_state(request)
    run = _get_run(state, run_id, mode_of(request))
    body = await json_body(request)
    vd = Validator()
    note = field_str(body, "note", vd, max_len=2000)
    vd.raise_if_any()
    if run["status"] in TERMINAL_STATES:
        raise ApiError(
            "RESUME_CONFLICT",
            f"Run {run_id!r} already finished ({run['status']}); it cannot be resumed.",
            extras={"current_state": run["status"], "allowed_from": ["awaiting_human"]},
        )
    if run["status"] != "awaiting_human":
        raise ApiError(
            "NOT_AWAITING_HUMAN",
            f"Run {run_id!r} is {run['status']!r}; resume is only valid from awaiting_human.",
            extras={"current_state": run["status"], "allowed_from": ["awaiting_human"]},
        )
    run["status"] = "running"
    run["awaiting_human_reason"] = None
    _emit(state, run, "resumed", {"note": note})
    _emit(state, run, "status", {"status": "running"})
    return run_public(run)
