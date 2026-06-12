"""Example 07 -- Workflows DSL end-to-end: author, validate, run, stream, approve.

Purpose
    Author a workflow definition with :mod:`coasty.dsl` that exercises EVERY
    step type (task / assert / if / loop / parallel / human_approval / retry
    plus succeed / fail), validate it locally, create the workflow, start a
    guarded run, stream its events over reconnect-safe SSE, approve (or, with
    ``--reject``, reject) the pending human_approval, and report
    ``spent_cents`` against ``budget_cents``.

Flow
    dsl builders -> dsl.validate() -> POST /v1/workflows (name + slug)
    -> POST /v1/workflows/{id}/runs (inputs + budget_cents + max_iterations)
    -> GET /v1/workflows/runs/{id}/events (SSE, Last-Event-ID reconnect)
    -> on awaiting_human: POST /v1/workflows/runs/{id}/resume
       {"approved": true}  (or {"approved": false} to reject and fail the step)
    -> GET /v1/workflows/runs/{id} -> print spent vs budget.

Endpoints
    POST /v1/workflows, POST /v1/workflows/{id}/runs,
    GET /v1/workflows/runs/{id}/events (SSE),
    POST /v1/workflows/runs/{id}/resume, GET /v1/workflows/runs/{id}

Estimated cost
    Only ``task`` steps bill (5 cr per agent step on v3/v4; control flow is
    free). This definition has 8 task executions on the happy path; assuming
    ~3 agent steps each that is 24 steps x 5 cr = 120 cr = $1.20 -- computed
    at runtime via ``coasty.cost.estimate_workflow_run`` and printed before
    anything billable happens. ``budget_cents`` / ``max_iterations`` are the
    server-side hard guards (breach -> GUARD_EXCEEDED). Sandbox keys: $0.

Run
    python examples/ex07_workflows.py --budget-cents 500 --confirm
    python examples/ex07_workflows.py --reject   # rejection path
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from coasty import CoastyClient, CoastyError, cost, dsl, env

WORKFLOW_NAME = "Invoice reconciliation (cookbook ex07)"
WORKFLOW_SLUG = "cookbook-ex07-invoice-reconcile"
DEFAULT_INPUTS: dict[str, Any] = {"month": "2026-06", "invoice_count": 3}
DEFAULT_BUDGET_CENTS = 500
DEFAULT_MAX_ITERATIONS = 200
ASSUMED_AGENT_STEPS_PER_TASK = 3


class SpendNotConfirmedError(RuntimeError):
    """Raised when a billable run is attempted on a live key without consent."""


# ── authoring: every DSL step type, validated locally ──────────────────────


def build_definition() -> dsl.WorkflowDefinition:
    """An invoice-reconciliation workflow using all 9 step types.

    task / assert / if / loop / parallel / human_approval / retry / succeed /
    fail, with conditions drawn from the documented op set (eq, gte, truthy,
    contains, and, not).
    """
    export = dsl.task(
        "export_invoices",
        "Open the billing portal and export the {{inputs.month}} invoices as invoices.csv",
        save_as="export",
        max_steps=20,
    )
    export_ok = dsl.assert_(
        "export_ok",
        dsl.and_(
            dsl.eq("{{export.status}}", "succeeded"),
            dsl.not_(dsl.contains("{{export.result}}", "ERROR")),
        ),
        message="invoice export must succeed without errors",
    )
    upload = dsl.retry(
        "upload_with_retry",
        [
            dsl.task(
                "upload_csv",
                "Upload invoices.csv to the reconciliation tool and wait for the import banner",
                save_as="upload",
            )
        ],
        max_attempts=3,
    )
    verify = dsl.if_(
        "verify_upload",
        dsl.and_(
            dsl.truthy("{{upload.passed}}"),
            dsl.gte("{{inputs.invoice_count}}", 1),
        ),
        then=[
            dsl.task(
                "spot_check",
                "Open the first imported invoice and confirm the totals match the CSV",
            )
        ],
        else_=[dsl.fail("abort_no_upload", message="upload failed -- nothing to reconcile")],
    )
    reconcile = dsl.loop(
        "reconcile_each",
        [
            dsl.task(
                "reconcile_one",
                "Reconcile the next unreconciled invoice and mark it done",
                save_as="last_reconciled",
            )
        ],
        count=3,
        max_iterations=10,
    )
    notify = dsl.parallel(
        "notify_stakeholders",
        [
            [dsl.task("notify_finance", "Email finance@ the reconciliation summary")],
            [dsl.task("notify_ops", "Post the reconciliation summary to the ops channel")],
        ],
    )
    approval = dsl.human_approval(
        "approve_payouts",
        message="Approve the {{inputs.month}} payout batch?",
        timeout_seconds=3600,
    )
    done = dsl.succeed("all_done", output={"reconciled": "{{last_reconciled.status}}"})
    return dsl.definition(
        [export, export_ok, upload, verify, reconcile, notify, approval, done],
        output={"month": "{{inputs.month}}"},
    )


def count_task_executions(steps: Sequence[Mapping[str, Any]]) -> int:
    """Happy-path count of ``task`` executions (loop bodies x count).

    ``retry`` bodies count once (first attempt succeeds); both ``if``
    branches are summed (an upper bound); ``while`` loops count once.
    """
    total = 0
    for step in steps:
        step_type = step.get("type")
        if step_type == "task":
            total += 1
        elif step_type == "if":
            total += count_task_executions(step.get("then", []))
            total += count_task_executions(step.get("else", []))
        elif step_type == "loop":
            count = step.get("count")
            multiplier = count if isinstance(count, int) and count > 0 else 1
            total += multiplier * count_task_executions(step.get("body", []))
        elif step_type == "retry":
            total += count_task_executions(step.get("body", []))
        elif step_type == "parallel":
            for branch in step.get("branches", []):
                total += count_task_executions(branch)
    return total


def estimate_cost(
    definition: dsl.WorkflowDefinition,
    *,
    assumed_agent_steps_per_task: int = ASSUMED_AGENT_STEPS_PER_TASK,
) -> cost.CostEstimate:
    """Estimate the run via coasty.cost: task executions x assumed agent steps."""
    executions = count_task_executions(definition["steps"])
    return cost.estimate_workflow_run(task_steps=executions * assumed_agent_steps_per_task)


def ensure_spend_allowed(
    api_key: str,
    estimate: cost.CostEstimate,
    *,
    confirm: bool,
    printer: Callable[[str], None] = print,
) -> None:
    """Print the itemized estimate; gate live keys behind explicit consent."""
    sandbox = env.is_sandbox_key(api_key)
    printer(cost.format_estimate(estimate, title="Estimated workflow cost", sandbox=sandbox))
    if sandbox or confirm:
        return
    raise SpendNotConfirmedError(
        "refusing to spend on a live key: pass --confirm or set COASTY_CONFIRM_SPEND=1 "
        "(or use an sk-coasty-test-... sandbox key, which never bills)"
    )


# ── pure-ish core: create -> run -> stream -> resume -> report ─────────────


@dataclass(frozen=True)
class WorkflowOutcome:
    """What the run did, for printing and for tests."""

    workflow_id: str
    run_id: str
    status: str
    spent_cents: int
    budget_cents: int
    approved: bool
    resumed_step_ids: tuple[str, ...]
    create_request_id: str | None
    final_request_id: str | None


def _data_dict(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def run_workflow(
    client: CoastyClient,
    *,
    inputs: Mapping[str, Any],
    budget_cents: int = DEFAULT_BUDGET_CENTS,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    approve: bool = True,
    approval_note: str | None = None,
    reconnect_delay: float = 0.5,
    printer: Callable[[str], None] = print,
) -> WorkflowOutcome:
    """Create the workflow, run it with guards, stream events, handle approval.

    On ``awaiting_human`` the run is resumed with ``approved=approve``:
    ``approved=true`` continues past the human_approval step, while
    ``approved=false`` REJECTS it (the step -- and so the run -- fails).
    """
    definition = build_definition()
    dsl.validate(definition)  # fail fast locally before any network call

    created = client.create_workflow(
        WORKFLOW_NAME,
        WORKFLOW_SLUG,
        definition,
        description="Cookbook ex07: invoice reconciliation exercising every DSL step type",
    )
    workflow_id = created.data["id"]
    printer(
        f"created workflow {workflow_id} v{created.data['version']} "
        f"(request_id={created.request_id})"
    )

    started = client.start_workflow_run(
        workflow_id,
        inputs=inputs,
        budget_cents=budget_cents,
        max_iterations=max_iterations,
    )
    run_id = started.data["id"]
    printer(
        f"started run {run_id} with budget_cents={budget_cents} "
        f"max_iterations={max_iterations} (request_id={started.request_id})"
    )

    resumed_steps: list[str] = []
    awaiting = False
    for event in client.workflow_run_events(run_id, reconnect_delay=reconnect_delay):
        data = _data_dict(event.data)
        if event.event == "status":
            printer(f"[seq {event.id}] status -> {data.get('status')}")
        elif event.event == "billing":
            printer(f"[seq {event.id}] billing: spent {data.get('cost_cents')} cents so far")
        elif event.event == "error":
            printer(f"[seq {event.id}] error event: {event.data}")

        hit_awaiting = event.event == "awaiting_human" or (
            event.event == "status" and data.get("status") == "awaiting_human"
        )
        if hit_awaiting and not awaiting:
            awaiting = True
            step_id = str(data.get("step_id") or data.get("awaiting_step_id") or "<unknown>")
            verb = "approving" if approve else "REJECTING"
            printer(f"[seq {event.id}] awaiting human at step {step_id!r} -- {verb}")
            resumed = client.resume_workflow_run(run_id, approved=approve, note=approval_note)
            printer(
                f"resume accepted: status={resumed.data['status']} "
                f"(request_id={resumed.request_id})"
            )
            resumed_steps.append(step_id)
        elif event.event == "resumed":
            awaiting = False

    final = client.get_workflow_run(run_id)
    spent = final.data["spent_cents"]
    budget = final.data["budget_cents"]
    status = final.data["status"]
    printer(
        f"run {run_id} finished: status={status}, spent {spent} of {budget} budget cents "
        f"(${spent / 100:.2f} of ${budget / 100:.2f}) (request_id={final.request_id})"
    )
    return WorkflowOutcome(
        workflow_id=workflow_id,
        run_id=run_id,
        status=status,
        spent_cents=spent,
        budget_cents=budget,
        approved=approve,
        resumed_step_ids=tuple(resumed_steps),
        create_request_id=created.request_id,
        final_request_id=final.request_id,
    )


# ── CLI ────────────────────────────────────────────────────────────────────


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    parser.add_argument("--inputs", default=json.dumps(DEFAULT_INPUTS), help="run inputs (JSON)")
    parser.add_argument("--budget-cents", type=int, default=DEFAULT_BUDGET_CENTS)
    parser.add_argument("--max-iterations", type=int, default=DEFAULT_MAX_ITERATIONS)
    parser.add_argument(
        "--reject",
        action="store_true",
        help="reject (approved=false) the human_approval instead of approving it",
    )
    parser.add_argument("--note", default=None, help="note to attach to the resume call")
    parser.add_argument("--confirm", action="store_true", help="consent to spend on a live key")
    args = parser.parse_args(argv)

    definition = build_definition()
    dsl.validate(definition)
    estimate = estimate_cost(definition)

    api_key = env.require_api_key()
    try:
        ensure_spend_allowed(api_key, estimate, confirm=args.confirm or env.spend_confirmed())
    except SpendNotConfirmedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        with CoastyClient(api_key=api_key) as client:
            outcome = run_workflow(
                client,
                inputs=json.loads(args.inputs),
                budget_cents=args.budget_cents,
                max_iterations=args.max_iterations,
                approve=not args.reject,
                approval_note=args.note,
            )
    except CoastyError as exc:
        print(
            f"API error {exc.code} (request_id={exc.request_id}): {exc.message}",
            file=sys.stderr,
        )
        return 1
    return 0 if outcome.status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
