"""Example 02 -- grounding: describe a UI element, get its (x, y), click it.

Purpose
    ``POST /v1/ground`` turns a natural-language element description (e.g.
    "the blue Login button") into screen coordinates. This example grounds
    one element on the current screen and clicks it via the shared executor
    -- the same capture/backend injection pattern as ex01, so tests run
    headless with a fake capture and ``NullBackend``.

Flow
    1. Capture + downscale the screen to 1280x720 (reusing ex01's
       :func:`make_local_capture`; lazy mss/Pillow from ``coasty[local]``).
    2. POST /v1/ground with the screenshot, the element description and the
       MATCHING ``screen_width``/``screen_height``.
    3. The response's ``{x, y}`` is in the SENT image's pixel space --
       ``ActionExecutor(scale_x=, scale_y=)`` multiplies it back up to the
       real screen before the click (the same coordinate-scaling pitfall
       documented in ex01).

Endpoints used
    POST /v1/ground (scope ``ground``)

Estimated cost
    3 credits ($0.03) per /ground call at 1280x720 (+1 cr only for HD images,
    i.e. strictly w > 1280 or h > 720 -- avoided here by construction). The
    itemized estimate is COMPUTED via :mod:`coasty.cost` and printed before
    any spend; sandbox keys print "$0 (sandbox)".

Run it
    python examples/ex02_grounding.py "the blue Login button" --confirm
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from coasty import ActionBackend, ActionExecutor, CoastyClient, CoastyError, PyAutoGuiBackend, env
from coasty.cost import CostEstimate, estimate_ground

# Shared helpers from ex01 (examples are sibling modules on sys.path).
from ex01_local_predict_loop import (
    TARGET_HEIGHT,
    TARGET_WIDTH,
    CaptureFn,
    make_local_capture,
    spend_gate,
)


@dataclass(frozen=True)
class GroundOutcome:
    """Where the element was found, in both coordinate spaces."""

    element: str
    sent_x: int  # as returned by the API (sent-image space)
    sent_y: int
    real_x: int  # scaled back to the physical screen (what got clicked)
    real_y: int
    request_id: str | None
    credits_charged: int


def ground_and_click(
    client: CoastyClient,
    capture: CaptureFn,
    backend: ActionBackend,
    element: str,
    *,
    emit: Callable[[str], None] = print,
) -> GroundOutcome:
    """The pure core: ground ``element`` on a fresh screenshot, then click it."""
    shot = capture()
    result = client.ground(
        shot.screenshot_b64,
        element,
        # MUST match the (downscaled) screenshot actually sent -- see ex01.
        screen_width=shot.sent_width,
        screen_height=shot.sent_height,
    )
    sent_x, sent_y = result.data["x"], result.data["y"]
    # The executor scales sent-space coordinates onto the real screen.
    executor = ActionExecutor(backend, scale_x=shot.scale_x, scale_y=shot.scale_y)
    executor.execute({"action_type": "click", "params": {"x": sent_x, "y": sent_y}})
    real_x = round(sent_x * shot.scale_x)
    real_y = round(sent_y * shot.scale_y)
    outcome = GroundOutcome(
        element=element,
        sent_x=sent_x,
        sent_y=sent_y,
        real_x=real_x,
        real_y=real_y,
        request_id=result.request_id,
        credits_charged=result.data["usage"]["credits_charged"],
    )
    emit(
        f"grounded {element!r} at sent=({sent_x}, {sent_y}) -> clicked real=({real_x}, {real_y}) "
        f"({outcome.credits_charged} cr, request_id={outcome.request_id})"
    )
    return outcome


def build_estimate() -> CostEstimate:
    """One /ground call at 1280x720 (SD by construction, so no HD surcharge)."""
    return estimate_ground(screen_width=TARGET_WIDTH, screen_height=TARGET_HEIGHT)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ground a UI element description via POST /v1/ground, then click it."
    )
    parser.add_argument("element", help="element description, e.g. 'the blue Login button'")
    parser.add_argument(
        "--confirm", action="store_true", help="allow spending on a live (non-sandbox) key"
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        api_key = env.require_api_key()
    except env.MissingAPIKeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not spend_gate(
        build_estimate(), api_key=api_key, confirm=args.confirm, title="ex02 ground + click"
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
            ground_and_click(client, capture, backend, args.element)
    except CoastyError as exc:
        print(f"error: {exc}", file=sys.stderr)  # str() includes the request_id
        if exc.request_id:
            print(f"request_id: {exc.request_id}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
