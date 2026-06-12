"""Example 03 -- stateful sessions: create -> multi-step predict -> info/reset -> DELETE.

Purpose
    Sessions keep the action trajectory SERVER-side (``max_trajectory_length``
    recent steps), so every step only uploads the new screenshot -- cheaper
    and simpler than re-sending a local trajectory to /predict. This example
    runs a multi-step predict loop inside one session, shows the session info
    (``GET /v1/sessions/{id}``), demonstrates a free trajectory reset, and
    ALWAYS deletes the session in a ``finally`` block (deleting frees your
    account's concurrency slot, even when a step blew up).

Flow
    1. POST /v1/sessions pinned to the 1280x720 capture size.
    2. Loop: capture -> POST /v1/sessions/{id}/predict -> execute actions
       (same injected capture/backend pattern as ex01; coordinates scale back
       to the real screen). Each step sends a unique ``Idempotency-Key`` so a
       network retry can never double-charge or double-predict the step.
    3. GET /v1/sessions/{id} -- step_count / total_credits_used.
    4. POST /v1/sessions/{id}/reset -- clears the server trajectory (free).
    5. finally: DELETE /v1/sessions/{id} -- always, error or not (free).

Endpoints used
    POST /v1/sessions, POST /v1/sessions/{id}/predict, GET /v1/sessions/{id},
    POST /v1/sessions/{id}/reset, DELETE /v1/sessions/{id} (scope ``session``)

Estimated cost
    10 credits flat to create the session (no surcharges) + 4 credits per
    step at 1280x720 (the server-side trajectory does NOT add the +2 cr/shot
    /predict surcharge; HD would add +1 but 1280x720 is SD). Info, reset and
    delete are free. The itemized estimate is COMPUTED via :mod:`coasty.cost`
    and printed before any spend; sandbox keys print "$0 (sandbox)".

Run it
    python examples/ex03_sessions.py "Fill in the login form" --max-steps 5 --confirm
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import cast

from coasty import (
    ActionBackend,
    ActionExecutor,
    CoastyClient,
    CoastyError,
    PyAutoGuiBackend,
    cost,
    env,
)
from coasty.types import CuaVersion, PredictStatus, SessionInfo
from ex01_local_predict_loop import (
    TARGET_HEIGHT,
    TARGET_WIDTH,
    CaptureFn,
    FinalStatus,
    StepRecord,
    make_local_capture,
    spend_gate,
)


@dataclass(frozen=True)
class SessionLoopResult:
    """Outcome of one session lifecycle."""

    session_id: str
    status: FinalStatus
    steps: list[StepRecord]
    info: SessionInfo | None  # GET /sessions/{id} snapshot taken after the loop

    @property
    def total_credits(self) -> int:
        return sum(record.credits_charged for record in self.steps)


def run_session(
    client: CoastyClient,
    capture: CaptureFn,
    backend: ActionBackend,
    instruction: str,
    *,
    max_steps: int = 10,
    cua_version: CuaVersion = "v3",
    max_trajectory_length: int = 3,
    inspect_and_reset: bool = True,
    emit: Callable[[str], None] = print,
) -> SessionLoopResult:
    """The pure core: full session lifecycle with a guaranteed DELETE.

    ``capture``/``backend`` are injected (tests: fake capture + NullBackend).
    Whatever happens after create -- predict errors, executor errors -- the
    ``finally`` block deletes the session so the concurrency slot is freed.
    """
    if max_steps < 1:
        raise ValueError("max_steps must be >= 1")

    first_shot = capture()  # the session pins this screen size at create time
    created = client.create_session(
        cua_version=cua_version,
        screen_width=first_shot.sent_width,
        screen_height=first_shot.sent_height,
        max_trajectory_length=max_trajectory_length,
    ).data
    session_id = created["session_id"]
    emit(f"session {session_id} created (screen={created['screen_size']}, 10 cr flat)")

    try:
        steps: list[StepRecord] = []
        final_status: FinalStatus = "max_steps"
        shot = first_shot
        for step_number in range(1, max_steps + 1):
            result = client.session_predict(
                session_id,
                shot.screenshot_b64,
                instruction,
                # Unique per step: a 429/5xx/transport retry replays instead
                # of double-executing (and double-charging) the step.
                idempotency_key=f"{session_id}-step-{step_number}",
            )
            prediction = result.data
            status: PredictStatus = prediction["status"]
            executor = ActionExecutor(backend, scale_x=shot.scale_x, scale_y=shot.scale_y)
            executed = executor.execute_all(prediction["actions"])
            steps.append(
                StepRecord(
                    step=prediction["step"],
                    status=status,
                    request_id=prediction["request_id"],
                    executed=executed,
                    credits_charged=prediction["usage"]["credits_charged"],
                )
            )
            emit(
                f"step {prediction['step']}: status={status} actions={executed} "
                f"({steps[-1].credits_charged} cr, request_id={steps[-1].request_id})"
            )
            if status != "continue":
                final_status = status
                break
            shot = capture()
        else:
            emit(f"stopping: --max-steps {max_steps} reached before done/fail")

        info: SessionInfo | None = None
        if inspect_and_reset:
            info = client.get_session(session_id).data  # free
            emit(
                f"session info: step_count={info['step_count']} "
                f"total_credits_used={info['total_credits_used']} expires_at={info['expires_at']}"
            )
            client.reset_session(session_id)  # free: wipes the server trajectory
            emit("session trajectory reset (free) -- next predict starts fresh")
        return SessionLoopResult(session_id=session_id, status=final_status, steps=steps, info=info)
    finally:
        # ALWAYS delete -- even when a step raised -- to free the concurrency
        # slot. A delete failure is reported but never masks the original error.
        try:
            client.delete_session(session_id)  # free
            emit(f"session {session_id} deleted (concurrency slot freed)")
        except CoastyError as exc:
            emit(f"warning: failed to delete session {session_id}: {exc}")


def build_estimate(max_steps: int, cua_version: CuaVersion) -> cost.CostEstimate:
    """Itemized worst case: one create + ``max_steps`` session predicts."""
    per_step = cost.estimate_session_predict(
        cua_version=cua_version, screen_width=TARGET_WIDTH, screen_height=TARGET_HEIGHT
    )
    scaled_steps = cost.CostEstimate(
        items=tuple(
            cost.CostItem(f"{item.label} x{max_steps} step(s)", item.credits * max_steps)
            for item in per_step.items
        )
    )
    return cost.combine(cost.estimate_session_create(), scaled_steps)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stateful session lifecycle: create -> predict loop -> info/reset -> delete."
    )
    parser.add_argument("instruction", help="natural-language task for the session")
    parser.add_argument("--max-steps", type=int, default=10, help="hard cap on session steps")
    parser.add_argument("--cua-version", choices=("v1", "v3", "v4"), default="v3")
    parser.add_argument(
        "--max-trajectory-length", type=int, default=3, help="server-kept steps (1-20)"
    )
    parser.add_argument(
        "--confirm", action="store_true", help="allow spending on a live (non-sandbox) key"
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    cua_version = cast(CuaVersion, args.cua_version)

    try:
        api_key = env.require_api_key()
    except env.MissingAPIKeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    estimate = build_estimate(args.max_steps, cua_version)
    if not spend_gate(
        estimate, api_key=api_key, confirm=args.confirm, title="ex03 session lifecycle"
    ):
        return 2

    try:
        capture = make_local_capture()
        backend = PyAutoGuiBackend(failsafe=True)  # corner-of-screen abort stays on
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        with CoastyClient(api_key) as client:
            outcome = run_session(
                client,
                capture,
                backend,
                args.instruction,
                max_steps=args.max_steps,
                cua_version=cua_version,
                max_trajectory_length=args.max_trajectory_length,
            )
    except CoastyError as exc:
        print(f"error: {exc}", file=sys.stderr)  # str() includes the request_id
        if exc.request_id:
            print(f"request_id: {exc.request_id}", file=sys.stderr)
        return 1

    print(
        f"finished: {outcome.status} after {len(outcome.steps)} step(s); "
        f"{outcome.total_credits} credit(s) charged on session {outcome.session_id}"
    )
    return 0 if outcome.status == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
