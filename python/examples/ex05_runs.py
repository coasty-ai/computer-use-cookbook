"""Example 05 -- task runs end-to-end: create (v3/v4) -> poll or stream SSE -> resume -> bill.

Purpose
    Task runs are server-driven: Coasty operates one of YOUR machines
    (``machine_id``) toward a natural-language ``task``, stepping the agent
    for you. This example creates a run (with an ``Idempotency-Key``), then
    follows it to a terminal state two ways:

    - default: poll ``GET /v1/runs/{id}`` until the status is terminal;
    - ``--events``: consume the durable SSE stream
      ``GET /v1/runs/{id}/events`` via :mod:`coasty.sse`, which reconnects
      automatically with ``Last-Event-ID`` (the seq cursor) so a dropped
      connection never loses or duplicates events.

    Either way, when the run pauses in ``awaiting_human`` (a captcha, a 2FA
    prompt...), you are prompted -- or ``--auto-resume NOTE`` answers
    unattended -- and the example POSTs ``/v1/runs/{id}/resume``. Billing
    events are printed as they stream, and the final summary includes the
    authoritative ``run.cost_cents``.

v3 vs v4 (the ``--v4`` flag)
    ``cua_version=v3`` is the default engine. ``--v4`` switches to the
    autonomous v4 engine with a verifier pass -- NOTE: v4 requires the pro+
    tier; other tiers get ``400 FEATURE_NOT_AVAILABLE``. Both bill the same
    5 credits per run step (only the legacy v1 engine costs 8 cr/step).

Endpoints used
    POST /v1/runs, GET /v1/runs/{id}, GET /v1/runs/{id}/events (SSE),
    POST /v1/runs/{id}/resume (scopes ``runs:read``, ``runs:write``)

Estimated cost
    5 credits ($0.05) per completed run step on v3/v4 (8 cr on v1); run steps
    carry NO surcharges. Worst case = ``--max-steps`` x the per-step rate,
    COMPUTED via :mod:`coasty.cost` and printed before any spend. Charges are
    debited up front and auto-refunded on failure; sandbox keys print
    "$0 (sandbox)". The machine's own runtime bills separately (see ex08).

Run it
    python examples/ex05_runs.py --machine-id mch_test_abc --task "Export the report" --confirm
    python examples/ex05_runs.py --machine-id mch_test_abc --task "..." --events --auto-resume ok
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from coasty import CoastyClient, CoastyError, env
from coasty.cost import CostEstimate, estimate_run
from coasty.types import TERMINAL_RUN_STATUSES, CuaVersion, Run
from ex01_local_predict_loop import spend_gate

ResumeHandler = Callable[[str | None], str | None]
"""Given the ``awaiting_human`` reason, return an optional resume note."""


@dataclass(frozen=True)
class StreamOutcome:
    """What the SSE stream delivered, plus the authoritative final run."""

    final_run: Run
    events_seen: list[str] = field(default_factory=list)  # event types, in order
    billing_events: list[dict[str, Any]] = field(default_factory=list)
    resumed: bool = False


def create_task_run(
    client: CoastyClient,
    *,
    machine_id: str,
    task: str,
    cua_version: CuaVersion = "v3",
    max_steps: int | None = None,
    idempotency_key: str | None = None,
    emit: Callable[[str], None] = print,
) -> Run:
    """POST /v1/runs with an Idempotency-Key (safe to retry, never duplicated)."""
    result = client.create_run(
        machine_id,
        task,
        cua_version=cua_version,
        max_steps=max_steps,
        # pause (the default) is what makes awaiting_human -> /resume possible;
        # "fail"/"cancel" would end the run instead of waiting for a human.
        on_awaiting_human="pause",
        idempotency_key=idempotency_key,
    )
    run = result.data
    replay = " (idempotent replay)" if result.idempotent_replay else ""
    emit(
        f"run {run['id']} created{replay}: status={run['status']} engine={run['cua_version']} "
        f"max_steps={run['max_steps']} (request_id={result.request_id})"
    )
    return run


def poll_run(
    client: CoastyClient,
    run_id: str,
    *,
    on_awaiting_human: ResumeHandler,
    poll_interval: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
    max_polls: int = 10_000,
    emit: Callable[[str], None] = print,
) -> Run:
    """Poll GET /v1/runs/{id} until terminal, resuming through awaiting_human.

    ``sleep`` is injected so tests poll instantly. Raises ``TimeoutError``
    if the run is still not terminal after ``max_polls`` polls.
    """
    for _ in range(max_polls):
        run = client.get_run(run_id).data
        status = run["status"]
        if status in TERMINAL_RUN_STATUSES:
            return run
        emit(f"run {run_id}: status={status} steps_completed={run['steps_completed']}")
        if status == "awaiting_human":
            note = on_awaiting_human(run["awaiting_human_reason"])
            # Only valid from awaiting_human; anything else would be a 409
            # NOT_AWAITING_HUMAN (surfaced as a typed ConflictError).
            client.resume_run(run_id, note=note)
            emit(f"resumed run {run_id}" + (f" with note {note!r}" if note else ""))
        sleep(poll_interval)
    raise TimeoutError(f"run {run_id} was not terminal after {max_polls} polls")


def _event_payload(data: str) -> dict[str, Any]:
    """Tolerantly decode an SSE data payload into a dict (or {})."""
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def stream_run_events(
    client: CoastyClient,
    run_id: str,
    *,
    on_awaiting_human: ResumeHandler,
    last_event_id: int | str | None = None,
    emit: Callable[[str], None] = print,
) -> StreamOutcome:
    """Consume GET /v1/runs/{id}/events to ``done``, resuming when paused.

    ``client.run_events`` (built on :mod:`coasty.sse`) transparently
    reconnects after a drop, sending the last seen seq as ``Last-Event-ID``
    -- events are durable server-side, so nothing is lost or duplicated.
    """
    events_seen: list[str] = []
    billing_events: list[dict[str, Any]] = []
    resumed = False

    for event in client.run_events(run_id, last_event_id=last_event_id):
        events_seen.append(event.event)
        payload = _event_payload(event.data)
        if event.event == "billing":
            billing_events.append(payload)
            emit(f"[billing seq={event.id}] {event.data}")
        elif event.event == "awaiting_human":
            reason = payload.get("reason")
            emit(f"[awaiting_human seq={event.id}] reason={reason!r}")
            note = on_awaiting_human(reason if isinstance(reason, str) else None)
            client.resume_run(run_id, note=note)
            resumed = True
            emit(f"resumed run {run_id}" + (f" with note {note!r}" if note else ""))
        elif event.event == "error":
            emit(f"[error seq={event.id}] {event.data}")
        else:
            emit(f"[{event.event} seq={event.id}] {event.data}")
        # the iterator stops by itself right after the "done" event

    final_run = client.get_run(run_id).data  # authoritative result + billing
    return StreamOutcome(
        final_run=final_run,
        events_seen=events_seen,
        billing_events=billing_events,
        resumed=resumed,
    )


def summarize_run(run: Run, *, emit: Callable[[str], None] = print) -> None:
    """Print the terminal state, result/error, and the authoritative cost."""
    emit(f"final status: {run['status']} ({run['steps_completed']} step(s) completed)")
    result = run["result"]
    if result is not None:
        emit(f"  result: passed={result['passed']} summary={result['summary']!r}")
    error = run["error"]
    if error is not None:
        emit(f"  error: {error['code']}: {error['message']}")
    emit(
        f"  billed: {run['credits_charged']} credit(s) = {run['cost_cents']} cents "
        f"(${run['cost_cents'] / 100:.2f}) [run.cost_cents is authoritative]"
    )


def build_estimate(max_steps: int, cua_version: CuaVersion) -> CostEstimate:
    """Worst case: every allowed step completes (run steps have no surcharges)."""
    return estimate_run(steps=max_steps, cua_version=cua_version)


def make_resume_handler(auto_note: str | None) -> ResumeHandler:
    """Interactive prompt by default; ``--auto-resume NOTE`` answers unattended."""

    def handler(reason: str | None) -> str | None:
        described = reason or "no reason given"
        if auto_note is not None:
            print(f"awaiting_human ({described}): auto-resuming with note {auto_note!r}")
            return auto_note
        print(f"run is awaiting_human: {described}")
        answer = input("resolve it on the machine, then press Enter (optional note): ").strip()
        return answer or None

    return handler


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create a task run, follow it (poll or --events SSE) to a terminal state."
    )
    parser.add_argument("--machine-id", required=True, help="machine to run the task on")
    parser.add_argument("--task", required=True, help="natural-language task (1-16000 chars)")
    parser.add_argument("--max-steps", type=int, default=20, help="server-side step cap (1-1000)")
    parser.add_argument(
        "--v4",
        action="store_true",
        help="use the autonomous v4 engine (verifier pass; REQUIRES the pro+ tier -- "
        "other tiers get 400 FEATURE_NOT_AVAILABLE; still 5 cr/step)",
    )
    parser.add_argument(
        "--events", action="store_true", help="stream SSE events instead of polling"
    )
    parser.add_argument(
        "--auto-resume",
        metavar="NOTE",
        help="on awaiting_human, resume immediately with this note instead of prompting",
    )
    parser.add_argument(
        "--idempotency-key", help="Idempotency-Key for the create (default: generated)"
    )
    parser.add_argument("--poll-interval", type=float, default=2.0, help="seconds between polls")
    parser.add_argument(
        "--confirm", action="store_true", help="allow spending on a live (non-sandbox) key"
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    cua_version: CuaVersion = "v4" if args.v4 else "v3"

    try:
        api_key = env.require_api_key()
    except env.MissingAPIKeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    estimate = build_estimate(args.max_steps, cua_version)
    if not spend_gate(
        estimate, api_key=api_key, confirm=args.confirm, title=f"ex05 task run ({cua_version})"
    ):
        return 2
    if args.v4:
        print("note: v4 is the autonomous engine with a verifier pass (pro+ tier only).")

    idempotency_key = (
        args.idempotency_key if args.idempotency_key is not None else f"ex05-run-{uuid.uuid4().hex}"
    )
    on_awaiting_human = make_resume_handler(cast("str | None", args.auto_resume))

    try:
        with CoastyClient(api_key) as client:
            run = create_task_run(
                client,
                machine_id=args.machine_id,
                task=args.task,
                cua_version=cua_version,
                max_steps=args.max_steps,
                idempotency_key=idempotency_key,
            )
            if args.events:
                outcome = stream_run_events(client, run["id"], on_awaiting_human=on_awaiting_human)
                final_run = outcome.final_run
            else:
                final_run = poll_run(
                    client,
                    run["id"],
                    on_awaiting_human=on_awaiting_human,
                    poll_interval=args.poll_interval,
                )
    except CoastyError as exc:
        print(f"error: {exc}", file=sys.stderr)  # str() includes the request_id
        if exc.request_id:
            print(f"request_id: {exc.request_id}", file=sys.stderr)
        return 1
    except TimeoutError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    summarize_run(final_run)
    return 0 if final_run["status"] == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(main())
