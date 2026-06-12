"""Pure, offline cost estimator implementing the full Coasty pricing table.

1 credit = 1 cent = $0.01 exactly. Sandbox keys (``sk-coasty-test-*``) never
bill -- pass ``sandbox=True`` to :func:`format_estimate` to label that.

Pricing (per docs/API_NOTES.md):

- predict 5 cr, session create 10 cr (no surcharges), session step 4 cr,
  ground 3 cr (+1 if HD), parse 0 cr.
- Surcharges (predict + session step): +2 cr per trajectory screenshot,
  +1 cr per HD image (strictly w>1280 OR h>720 -- exactly 1280x720 is NOT
  HD; applies to the current AND each trajectory screenshot), +3 cr on the
  v1 engine, +1 cr when system_prompt exceeds 500 chars (exactly 500 free).
- Run step: 5 cr on v3/v4, 8 cr on v1 (no other surcharges). Workflow task
  steps bill like run steps; control-flow steps are free.
- Machines (hourly, metered per minute, rounded down): Linux running 5/hr,
  Windows running 9/hr (incl. starting/stopping/restarting), stopped or
  suspended 1/hr any OS, creating/error/terminated free. Snapshot 1 cr.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .types import CuaVersion, OsType

CREDIT_USD = 0.01

PREDICT_BASE_CREDITS = 5
SESSION_CREATE_CREDITS = 10
SESSION_PREDICT_BASE_CREDITS = 4
GROUND_BASE_CREDITS = 3
PARSE_CREDITS = 0
TRAJECTORY_SURCHARGE_CREDITS = 2
HD_SURCHARGE_CREDITS = 1
V1_SURCHARGE_CREDITS = 3
SYSTEM_PROMPT_SURCHARGE_CREDITS = 1
SYSTEM_PROMPT_FREE_CHARS = 500
RUN_STEP_CREDITS = 5
RUN_STEP_V1_CREDITS = 8
SNAPSHOT_CREDITS = 1
MACHINE_RUNNING_LINUX_CREDITS_PER_HOUR = 5
MACHINE_RUNNING_WINDOWS_CREDITS_PER_HOUR = 9
MACHINE_STOPPED_CREDITS_PER_HOUR = 1

_RUNNING_STATES = frozenset({"running", "starting", "stopping", "restarting"})
_STOPPED_STATES = frozenset({"stopped", "suspended", "suspended_for_billing"})
_FREE_STATES = frozenset({"creating", "error", "terminated"})


@dataclass(frozen=True)
class CostItem:
    """One itemized line of an estimate."""

    label: str
    credits: int


@dataclass(frozen=True)
class CostEstimate:
    """An itemized credit estimate. ``usd`` assumes a live (billing) key."""

    items: tuple[CostItem, ...]

    @property
    def credits(self) -> int:
        return sum(item.credits for item in self.items)

    @property
    def usd(self) -> float:
        return self.credits * CREDIT_USD


def is_hd(width: int, height: int) -> bool:
    """HD is strictly width > 1280 OR height > 720 (1280x720 itself is SD)."""
    return width > 1280 or height > 720


def _inference_surcharges(
    *,
    cua_version: CuaVersion,
    screen_width: int,
    screen_height: int,
    trajectory_sizes: Sequence[tuple[int, int]],
    system_prompt: str | None,
) -> list[CostItem]:
    items: list[CostItem] = []
    trajectory_count = len(trajectory_sizes)
    if trajectory_count:
        items.append(
            CostItem(
                f"trajectory screenshots x{trajectory_count} "
                f"(+{TRAJECTORY_SURCHARGE_CREDITS} cr each)",
                TRAJECTORY_SURCHARGE_CREDITS * trajectory_count,
            )
        )
    hd_count = int(is_hd(screen_width, screen_height)) + sum(
        1 for (width, height) in trajectory_sizes if is_hd(width, height)
    )
    if hd_count:
        items.append(
            CostItem(
                f"HD images x{hd_count} (+{HD_SURCHARGE_CREDITS} cr each, w>1280 or h>720)",
                HD_SURCHARGE_CREDITS * hd_count,
            )
        )
    if cua_version == "v1":
        items.append(CostItem("v1 engine surcharge", V1_SURCHARGE_CREDITS))
    if system_prompt is not None and len(system_prompt) > SYSTEM_PROMPT_FREE_CHARS:
        items.append(
            CostItem(
                f"system_prompt > {SYSTEM_PROMPT_FREE_CHARS} chars",
                SYSTEM_PROMPT_SURCHARGE_CREDITS,
            )
        )
    return items


def estimate_predict(
    *,
    cua_version: CuaVersion = "v3",
    screen_width: int = 1920,
    screen_height: int = 1080,
    trajectory_sizes: Sequence[tuple[int, int]] = (),
    system_prompt: str | None = None,
) -> CostEstimate:
    """Estimate one ``POST /v1/predict`` call (base 5 cr + surcharges)."""
    items = [CostItem("predict base", PREDICT_BASE_CREDITS)]
    items.extend(
        _inference_surcharges(
            cua_version=cua_version,
            screen_width=screen_width,
            screen_height=screen_height,
            trajectory_sizes=trajectory_sizes,
            system_prompt=system_prompt,
        )
    )
    return CostEstimate(items=tuple(items))


def estimate_session_create() -> CostEstimate:
    """Estimate ``POST /v1/sessions`` (flat 10 cr, no surcharges)."""
    return CostEstimate(items=(CostItem("session create", SESSION_CREATE_CREDITS),))


def estimate_session_predict(
    *,
    cua_version: CuaVersion = "v3",
    screen_width: int = 1920,
    screen_height: int = 1080,
    trajectory_sizes: Sequence[tuple[int, int]] = (),
    system_prompt: str | None = None,
) -> CostEstimate:
    """Estimate one session step (base 4 cr + the same surcharges as predict).

    ``trajectory_sizes`` are the sizes of the server-kept trajectory
    screenshots attached to this step.
    """
    items = [CostItem("session predict base", SESSION_PREDICT_BASE_CREDITS)]
    items.extend(
        _inference_surcharges(
            cua_version=cua_version,
            screen_width=screen_width,
            screen_height=screen_height,
            trajectory_sizes=trajectory_sizes,
            system_prompt=system_prompt,
        )
    )
    return CostEstimate(items=tuple(items))


def estimate_ground(*, screen_width: int = 1920, screen_height: int = 1080) -> CostEstimate:
    """Estimate ``POST /v1/ground`` (3 cr, +1 if the screenshot is HD)."""
    items = [CostItem("ground base", GROUND_BASE_CREDITS)]
    if is_hd(screen_width, screen_height):
        items.append(CostItem("HD image surcharge", HD_SURCHARGE_CREDITS))
    return CostEstimate(items=tuple(items))


def estimate_parse() -> CostEstimate:
    """``POST /v1/parse`` is free (deterministic, no model call)."""
    return CostEstimate(items=(CostItem("parse (free)", PARSE_CREDITS),))


def run_step_credits(cua_version: CuaVersion = "v3") -> int:
    """Credits for one completed run step: 8 on v1, 5 on v3/v4."""
    return RUN_STEP_V1_CREDITS if cua_version == "v1" else RUN_STEP_CREDITS


def estimate_run(*, steps: int, cua_version: CuaVersion = "v3") -> CostEstimate:
    """Estimate a task run of ``steps`` completed agent steps."""
    if steps < 0:
        raise ValueError("steps must be >= 0")
    per_step = run_step_credits(cua_version)
    return CostEstimate(
        items=(
            CostItem(f"run steps x{steps} ({per_step} cr each, {cua_version})", per_step * steps),
        )
    )


def estimate_workflow_run(*, task_steps: int, cua_version: CuaVersion = "v3") -> CostEstimate:
    """Estimate a workflow run: only ``task`` steps bill; control flow is free."""
    if task_steps < 0:
        raise ValueError("task_steps must be >= 0")
    per_step = run_step_credits(cua_version)
    return CostEstimate(
        items=(
            CostItem(
                f"workflow task steps x{task_steps} ({per_step} cr each, {cua_version})",
                per_step * task_steps,
            ),
            CostItem("control-flow steps (free)", 0),
        )
    )


def machine_hourly_credits(os_type: OsType, state: str) -> int:
    """Hourly runtime rate for a machine in the given state."""
    if state in _RUNNING_STATES:
        return (
            MACHINE_RUNNING_WINDOWS_CREDITS_PER_HOUR
            if os_type == "windows"
            else MACHINE_RUNNING_LINUX_CREDITS_PER_HOUR
        )
    if state in _STOPPED_STATES:
        return MACHINE_STOPPED_CREDITS_PER_HOUR
    if state in _FREE_STATES:
        return 0
    raise ValueError(f"unknown machine state: {state!r}")


def estimate_machine_runtime(
    *,
    os_type: OsType,
    state: str,
    minutes: float,
) -> CostEstimate:
    """Estimate machine runtime: hourly rate, metered per minute, rounded down."""
    if minutes < 0:
        raise ValueError("minutes must be >= 0")
    rate = machine_hourly_credits(os_type, state)
    credits = int(rate * minutes / 60)  # rounded down in your favor
    return CostEstimate(
        items=(
            CostItem(
                f"machine {os_type}/{state} x{minutes:g} min ({rate} cr/hr, rounded down)",
                credits,
            ),
        )
    )


def estimate_snapshot() -> CostEstimate:
    """``POST /machines/{id}/snapshot`` -- 1 cr one-time (refunded on failure)."""
    return CostEstimate(items=(CostItem("snapshot", SNAPSHOT_CREDITS),))


def combine(*estimates: CostEstimate) -> CostEstimate:
    """Merge several estimates into one itemized estimate."""
    items: list[CostItem] = []
    for estimate in estimates:
        items.extend(estimate.items)
    return CostEstimate(items=tuple(items))


def format_estimate(
    estimate: CostEstimate,
    *,
    title: str = "Estimated cost",
    sandbox: bool = False,
) -> str:
    """Render an itemized, printable estimate for examples to show pre-spend.

    With ``sandbox=True`` the dollar total is labeled $0.00 -- sandbox keys
    (``sk-coasty-test-*``) never bill.
    """
    lines = [f"{title}:"]
    for item in estimate.items:
        lines.append(f"  - {item.label}: {item.credits} cr")
    if sandbox:
        lines.append(f"  total: {estimate.credits} cr = $0.00 (sandbox key - never billed)")
    else:
        lines.append(f"  total: {estimate.credits} cr = ${estimate.usd:.2f}")
    return "\n".join(lines)
