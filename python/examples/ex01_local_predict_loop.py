"""Example 01 -- local screen predict-loop: screenshot -> /predict -> execute -> repeat.

Purpose
    Drive YOUR OWN desktop with the stateless ``POST /v1/predict`` endpoint:
    grab a screenshot, ask Coasty for the next GUI action(s), execute them
    locally with pyautogui, and repeat while the model answers
    ``status == "continue"`` (capped by ``--max-steps``).

Flow
    1. Capture the primary monitor with mss and downscale it to 1280x720 with
       Pillow (both ship in the optional ``coasty[local]`` extra and are
       imported lazily, so CI never needs a display).
    2. POST /v1/predict with the downscaled screenshot AND the matching
       ``screen_width``/``screen_height`` -- see the coordinate-scaling
       pitfall explained at :func:`make_local_capture`.
    3. Execute the returned actions through ``coasty.executor.ActionExecutor``
       with :class:`coasty.PyAutoGuiBackend`, scaling coordinates back up to
       the real screen.
    4. Loop while ``status == "continue"``; stop on ``done``/``fail`` or when
       ``--max-steps`` is reached.

Endpoints used
    POST /v1/predict (scope ``predict``)

Estimated cost
    5 credits ($0.05) per /predict call at 1280x720 (exactly 1280x720 is NOT
    HD, so no +1 HD surcharge; no trajectory is attached here -- see ex03 for
    server-side trajectories). Worst case = ``--max-steps`` x 5 cr. The exact
    itemized estimate is COMPUTED via :mod:`coasty.cost` and printed before
    any spend; sandbox keys (``sk-coasty-test-*``) print "$0 (sandbox)".

Safety
    - pyautogui FAILSAFE stays ON (the default): fling the mouse into a
      screen corner to abort the loop instantly.
    - ``raw`` actions are never executed -- the shared executor logs and
      skips them.
    - Idempotency note: per the docs, ``Idempotency-Key`` is only supported
      on POST /runs, /workflows runs, /machines and snapshot -- POST /predict
      does not accept one. That is safe: failed predictions are charged then
      auto-refunded, so the shared client retries them freely. ex03 sends a
      unique per-step ``Idempotency-Key`` on session predicts, which DO
      support it in this cookbook's client.

Run it
    python examples/ex01_local_predict_loop.py "Open the calculator" --max-steps 5 --confirm
"""

from __future__ import annotations

import argparse
import base64
import importlib
import io
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

from coasty import (
    ActionBackend,
    ActionExecutor,
    CoastyClient,
    CoastyError,
    PyAutoGuiBackend,
    cost,
    env,
)
from coasty.types import CuaVersion, PredictStatus

# Send screenshots at 1280x720: the largest size that avoids the +1 cr HD
# surcharge (HD is STRICTLY w > 1280 OR h > 720) while staying readable.
TARGET_WIDTH = 1280
TARGET_HEIGHT = 720

FinalStatus = Literal["done", "fail", "max_steps"]


@dataclass(frozen=True)
class CaptureResult:
    """One captured (and possibly downscaled) screenshot plus its geometry.

    ``sent_*`` is the size of the image actually encoded for the API;
    ``real_*`` is the physical screen size. The scale factors map the
    API's returned coordinates (in SENT space) back onto the REAL screen.
    """

    screenshot_b64: str  # raw base64, no data: prefix
    sent_width: int
    sent_height: int
    real_width: int
    real_height: int

    @property
    def scale_x(self) -> float:
        return self.real_width / self.sent_width

    @property
    def scale_y(self) -> float:
        return self.real_height / self.sent_height


CaptureFn = Callable[[], CaptureResult]
"""Injected screenshot source -- tests swap in a fake; main() uses mss."""


@dataclass(frozen=True)
class StepRecord:
    """What one loop iteration predicted and executed."""

    step: int
    status: PredictStatus
    request_id: str
    executed: list[str]  # action_types handled by the executor, in order
    credits_charged: int


@dataclass(frozen=True)
class LoopResult:
    """Outcome of the whole predict loop."""

    status: FinalStatus
    steps: list[StepRecord]

    @property
    def total_credits(self) -> int:
        return sum(record.credits_charged for record in self.steps)


def make_local_capture(
    target_width: int = TARGET_WIDTH, target_height: int = TARGET_HEIGHT
) -> CaptureFn:
    """Build a real-screen capture function over mss + Pillow.

    Both libraries come from the optional ``coasty[local]`` extra and are
    imported lazily so importing this module never requires a display.

    COORDINATE-SCALING PITFALL: /predict (and /ground) return x/y in the
    pixel space of the screenshot you SENT. If you downscale the screenshot
    you MUST (a) pass the downscaled ``screen_width``/``screen_height`` in
    the request body, and (b) multiply the returned coordinates back up by
    ``real / sent`` per axis before clicking. Mixing spaces -- e.g. sending
    a 1280x720 image while claiming a 2560x1440 screen, or clicking the raw
    coordinates on a larger screen -- lands every click in the wrong place
    (typically the top-left quadrant). ``ActionExecutor(scale_x=, scale_y=)``
    does step (b) for you.
    """
    try:
        # Lazy: mss/Pillow are optional ([local] extra); cast keeps mypy
        # strict happy without needing their type stubs.
        mss_module = cast(Any, importlib.import_module("mss"))
        image_module = cast(Any, importlib.import_module("PIL.Image"))
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise RuntimeError(
            "mss/Pillow are not installed. Install the local-automation extra: "
            'pip install "coasty[local]"'
        ) from exc

    def capture() -> CaptureResult:  # pragma: no cover - needs a real display
        with mss_module.mss() as screen:
            monitor = screen.monitors[1]  # 1 = primary monitor (0 = all merged)
            shot = screen.grab(monitor)
        image = image_module.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        real_width, real_height = int(image.size[0]), int(image.size[1])
        sent_width, sent_height = real_width, real_height
        if real_width > target_width or real_height > target_height:
            # Downscale (never upscale) -- and remember: the request body
            # below must carry THESE dimensions, not the real ones.
            image = image.resize((target_width, target_height))
            sent_width, sent_height = target_width, target_height
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return CaptureResult(
            screenshot_b64=base64.b64encode(buffer.getvalue()).decode("ascii"),
            sent_width=sent_width,
            sent_height=sent_height,
            real_width=real_width,
            real_height=real_height,
        )

    return capture


def spend_gate(
    estimate: cost.CostEstimate,
    *,
    api_key: str,
    confirm: bool,
    title: str = "Estimated cost",
    emit: Callable[[str], None] = print,
) -> bool:
    """Print the itemized estimate; decide whether a billable call may run.

    Sandbox keys (``sk-coasty-test-*``) never bill -- they are labeled
    "$0 (sandbox)" and proceed. Live keys require ``--confirm`` or
    ``COASTY_CONFIRM_SPEND=1``. (Shared by ex02/ex03/ex05.)
    """
    sandbox = env.is_sandbox_key(api_key)
    emit(cost.format_estimate(estimate, title=title, sandbox=sandbox))
    if sandbox:
        emit("$0 (sandbox) -- sandbox keys never bill; proceeding.")
        return True
    if confirm or env.spend_confirmed():
        emit("Spend confirmed (--confirm / COASTY_CONFIRM_SPEND=1); proceeding.")
        return True
    emit("Refusing to spend on a live key: re-run with --confirm or set COASTY_CONFIRM_SPEND=1.")
    return False


def run_predict_loop(
    client: CoastyClient,
    capture: CaptureFn,
    backend: ActionBackend,
    instruction: str,
    *,
    max_steps: int = 10,
    cua_version: CuaVersion = "v3",
    emit: Callable[[str], None] = print,
) -> LoopResult:
    """The pure, testable core: capture -> predict -> execute, until terminal.

    ``capture`` and ``backend`` are injected so tests run headless (a fake
    capture + :class:`coasty.NullBackend`); main() wires up mss + pyautogui.
    """
    if max_steps < 1:
        raise ValueError("max_steps must be >= 1")
    steps: list[StepRecord] = []
    for step_number in range(1, max_steps + 1):
        shot = capture()
        result = client.predict(
            shot.screenshot_b64,
            instruction,
            cua_version=cua_version,
            # MUST match the screenshot actually sent (see make_local_capture).
            screen_width=shot.sent_width,
            screen_height=shot.sent_height,
        )
        prediction = result.data
        status: PredictStatus = prediction["status"]
        # Scale predicted coordinates (sent space) back to the real screen.
        executor = ActionExecutor(backend, scale_x=shot.scale_x, scale_y=shot.scale_y)
        executed = executor.execute_all(prediction["actions"])
        record = StepRecord(
            step=step_number,
            status=status,
            request_id=prediction["request_id"],
            executed=executed,
            credits_charged=prediction["usage"]["credits_charged"],
        )
        steps.append(record)
        emit(
            f"step {step_number}/{max_steps}: status={status} actions={executed} "
            f"({record.credits_charged} cr, request_id={record.request_id})"
        )
        if status != "continue":
            return LoopResult(status=status, steps=steps)
    emit(f"stopping: --max-steps {max_steps} reached before done/fail")
    return LoopResult(status="max_steps", steps=steps)


def build_estimate(max_steps: int, cua_version: CuaVersion) -> cost.CostEstimate:
    """Worst-case itemized estimate: ``max_steps`` predicts at 1280x720."""
    per_step = cost.estimate_predict(
        cua_version=cua_version, screen_width=TARGET_WIDTH, screen_height=TARGET_HEIGHT
    )
    return cost.CostEstimate(
        items=tuple(
            cost.CostItem(f"{item.label} x{max_steps} step(s)", item.credits * max_steps)
            for item in per_step.items
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Drive the local desktop via POST /v1/predict (screenshot loop)."
    )
    parser.add_argument("instruction", help="natural-language task, e.g. 'Open the calculator'")
    parser.add_argument("--max-steps", type=int, default=10, help="hard cap on loop iterations")
    parser.add_argument("--cua-version", choices=("v1", "v3", "v4"), default="v3")
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
    if not spend_gate(estimate, api_key=api_key, confirm=args.confirm, title="ex01 predict loop"):
        return 2

    try:
        capture = make_local_capture()
        # FAILSAFE stays True: moving the mouse into any screen corner raises
        # pyautogui.FailSafeException and aborts mid-action.
        backend = PyAutoGuiBackend(failsafe=True)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        with CoastyClient(api_key) as client:
            outcome = run_predict_loop(
                client,
                capture,
                backend,
                args.instruction,
                max_steps=args.max_steps,
                cua_version=cua_version,
            )
    except CoastyError as exc:
        print(f"error: {exc}", file=sys.stderr)  # str() includes the request_id
        if exc.request_id:
            print(f"request_id: {exc.request_id}", file=sys.stderr)
        return 1

    print(
        f"finished: {outcome.status} after {len(outcome.steps)} step(s); "
        f"{outcome.total_credits} credit(s) charged"
    )
    return 0 if outcome.status == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
