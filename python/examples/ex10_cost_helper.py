"""Example 10 -- Cost helper CLI: estimate any Coasty operation before you buy.

Purpose
    A purely local calculator over :mod:`coasty.cost` (the full pricing
    table, 1 credit = $0.01). Subcommands estimate single operations;
    ``plan`` reads a small JSON file describing an intended batch and totals
    it, itemized.

Flow
    argparse subcommand -> coasty.cost.estimate_* -> itemized credits + USD.

Subcommands
    predict   --cua-version --width --height --trajectory --system-prompt-chars
    session   --steps + the same per-step surcharge flags (create = 10 cr flat)
    ground    --width --height
    run       --steps --cua-version            (5 cr/step v3+v4, 8 cr v1)
    workflow  --task-steps --cua-version       (control-flow steps are free)
    machine   --os --hours --state --snapshots (hourly, metered per minute)
    plan      PATH.json                        (totals a whole batch)

Endpoints
    None -- entirely offline arithmetic (so no spend gate either).

Estimated cost
    $0.00 to run this example. Remember: sandbox keys (sk-coasty-test-*)
    never bill, so every estimate is $0 in sandbox; and charges are debited
    up front then AUTO-REFUNDED on failure (e.g. PREDICTION_FAILED, a failed
    snapshot), so a failed op costs nothing.

Run
    python examples/ex10_cost_helper.py predict --width 1280 --height 720
    python examples/ex10_cost_helper.py machine --os windows --hours 2
    python examples/ex10_cost_helper.py plan my_batch.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from coasty import cost, env
from coasty.cost import CostEstimate, CostItem
from coasty.types import CuaVersion, OsType

SANDBOX_NOTE = "note: sandbox keys (sk-coasty-test-*) never bill -- every estimate is $0.00"
REFUND_NOTE = (
    "note: charges are debited up front and auto-refunded on failure "
    "(failed predictions/groundings/snapshots cost nothing)"
)

PLAN_KINDS = frozenset(
    {
        "predict",
        "session_create",
        "session_predict",
        "ground",
        "parse",
        "run",
        "workflow",
        "machine",
        "snapshot",
    }
)


class PlanError(ValueError):
    """A plan file is malformed (unknown kind, missing field, bad type)."""


def _scaled(estimate: CostEstimate, count: int, unit: str) -> CostEstimate:
    """Multiply an estimate by ``count``, keeping the itemization readable."""
    if count < 0:
        raise ValueError("count must be >= 0")
    if count == 1:
        return estimate
    items = tuple(
        CostItem(f"{item.label} [x{count} {unit}]", item.credits * count) for item in estimate.items
    )
    return CostEstimate(items=items)


def _cua(value: str) -> CuaVersion:
    if value not in ("v1", "v3", "v4"):
        raise argparse.ArgumentTypeError(f"cua_version must be v1/v3/v4, got {value!r}")
    return cast(CuaVersion, value)


def _os(value: str) -> OsType:
    if value not in ("linux", "windows"):
        raise argparse.ArgumentTypeError(f"os must be linux/windows, got {value!r}")
    return cast(OsType, value)


# ── per-subcommand estimators (pure; known-answer tested) ──────────────────


def estimate_predict_cmd(
    *,
    cua_version: CuaVersion = "v3",
    width: int = 1920,
    height: int = 1080,
    trajectory: int = 0,
    system_prompt_chars: int = 0,
    count: int = 1,
) -> CostEstimate:
    """POST /predict: 5 cr + trajectory(+2 ea) + HD(+1 ea) + v1(+3) + prompt(+1)."""
    one = cost.estimate_predict(
        cua_version=cua_version,
        screen_width=width,
        screen_height=height,
        trajectory_sizes=[(width, height)] * trajectory,
        system_prompt="x" * system_prompt_chars if system_prompt_chars > 0 else None,
    )
    return _scaled(one, count, "calls")


def estimate_session_cmd(
    *,
    steps: int = 5,
    cua_version: CuaVersion = "v3",
    width: int = 1920,
    height: int = 1080,
    trajectory_per_step: int = 0,
    system_prompt_chars: int = 0,
) -> CostEstimate:
    """One session: create (10 cr flat, no surcharges) + N predict steps (4 cr +)."""
    if steps < 0:
        raise ValueError("steps must be >= 0")
    step = cost.estimate_session_predict(
        cua_version=cua_version,
        screen_width=width,
        screen_height=height,
        trajectory_sizes=[(width, height)] * trajectory_per_step,
        system_prompt="x" * system_prompt_chars if system_prompt_chars > 0 else None,
    )
    return cost.combine(cost.estimate_session_create(), _scaled(step, steps, "steps"))


def estimate_ground_cmd(*, width: int = 1920, height: int = 1080, count: int = 1) -> CostEstimate:
    """POST /ground: 3 cr, +1 if HD (strictly w>1280 or h>720)."""
    return _scaled(cost.estimate_ground(screen_width=width, screen_height=height), count, "calls")


def estimate_run_cmd(*, steps: int, cua_version: CuaVersion = "v3") -> CostEstimate:
    """A task run: 5 cr per step on v3/v4, 8 cr on v1; no other surcharges."""
    return cost.estimate_run(steps=steps, cua_version=cua_version)


def estimate_workflow_cmd(*, task_steps: int, cua_version: CuaVersion = "v3") -> CostEstimate:
    """A workflow run: only task steps bill; control-flow steps are free."""
    return cost.estimate_workflow_run(task_steps=task_steps, cua_version=cua_version)


def estimate_machine_cmd(
    *,
    os_type: OsType = "linux",
    hours: float = 1.0,
    state: str = "running",
    snapshots: int = 0,
) -> CostEstimate:
    """Machine runtime (hourly rate, metered per minute, rounded down) + snapshots."""
    if hours < 0:
        raise ValueError("hours must be >= 0")
    runtime = cost.estimate_machine_runtime(os_type=os_type, state=state, minutes=hours * 60)
    if snapshots <= 0:
        return runtime
    return cost.combine(runtime, _scaled(cost.estimate_snapshot(), snapshots, "snapshots"))


# ── plan mode: total a whole intended batch from JSON ──────────────────────


def _int_field(item: Mapping[str, Any], key: str, default: int) -> int:
    value = item.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise PlanError(f"plan item field {key!r} must be an integer, got {value!r}")
    return value


def _float_field(item: Mapping[str, Any], key: str, default: float) -> float:
    value = item.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise PlanError(f"plan item field {key!r} must be a number, got {value!r}")
    return float(value)


def _cua_field(item: Mapping[str, Any]) -> CuaVersion:
    value = item.get("cua_version", "v3")
    if value not in ("v1", "v3", "v4"):
        raise PlanError(f"plan item cua_version must be v1/v3/v4, got {value!r}")
    return cast(CuaVersion, value)


def estimate_plan_item(item: Mapping[str, Any]) -> CostEstimate:
    """Estimate one ``{"kind": ..., ...}`` entry of a plan file."""
    kind = item.get("kind")
    if not isinstance(kind, str) or kind not in PLAN_KINDS:
        raise PlanError(f"unknown plan item kind {kind!r}; expected one of {sorted(PLAN_KINDS)}")
    count = _int_field(item, "count", 1)
    width = _int_field(item, "screen_width", 1920)
    height = _int_field(item, "screen_height", 1080)

    if kind == "predict":
        return estimate_predict_cmd(
            cua_version=_cua_field(item),
            width=width,
            height=height,
            trajectory=_int_field(item, "trajectory", 0),
            system_prompt_chars=_int_field(item, "system_prompt_chars", 0),
            count=count,
        )
    if kind == "session_create":
        return _scaled(cost.estimate_session_create(), count, "sessions")
    if kind == "session_predict":
        one = cost.estimate_session_predict(
            cua_version=_cua_field(item),
            screen_width=width,
            screen_height=height,
            trajectory_sizes=[(width, height)] * _int_field(item, "trajectory", 0),
            system_prompt=(
                "x" * _int_field(item, "system_prompt_chars", 0)
                if _int_field(item, "system_prompt_chars", 0) > 0
                else None
            ),
        )
        return _scaled(one, count, "steps")
    if kind == "ground":
        return estimate_ground_cmd(width=width, height=height, count=count)
    if kind == "parse":
        return _scaled(cost.estimate_parse(), count, "calls")
    if kind == "run":
        return estimate_run_cmd(steps=_int_field(item, "steps", 1), cua_version=_cua_field(item))
    if kind == "workflow":
        return estimate_workflow_cmd(
            task_steps=_int_field(item, "task_steps", 1), cua_version=_cua_field(item)
        )
    if kind == "machine":
        os_value = item.get("os", "linux")
        if os_value not in ("linux", "windows"):
            raise PlanError(f"plan item os must be linux/windows, got {os_value!r}")
        state = item.get("state", "running")
        if not isinstance(state, str):
            raise PlanError(f"plan item state must be a string, got {state!r}")
        minutes = _float_field(item, "minutes", _float_field(item, "hours", 1.0) * 60)
        return cost.estimate_machine_runtime(
            os_type=cast(OsType, os_value), state=state, minutes=minutes
        )
    # kind == "snapshot"
    return _scaled(cost.estimate_snapshot(), count, "snapshots")


def estimate_plan(plan: Mapping[str, Any]) -> CostEstimate:
    """Total an entire ``{"items": [...]}`` plan, keeping every line itemized."""
    items = plan.get("items")
    if not isinstance(items, list) or not items:
        raise PlanError('a plan needs a non-empty "items" list')
    estimates = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise PlanError(f"plan items[{index}] must be an object, got {type(item).__name__}")
        estimates.append(estimate_plan_item(item))
    return cost.combine(*estimates)


def load_plan(path: Path) -> dict[str, Any]:
    """Read + parse a plan file (raises PlanError with a clear message)."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PlanError(f"cannot read plan file {path}: {exc}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PlanError(f"plan file {path} is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise PlanError(f"plan file {path} must contain a JSON object")
    return parsed


# ── CLI plumbing ───────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def add_screen_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument("--width", type=int, default=1920, help="screenshot width (default 1920)")
        p.add_argument("--height", type=int, default=1080, help="screenshot height (default 1080)")

    p_predict = sub.add_parser("predict", help="one or more POST /predict calls")
    p_predict.add_argument("--cua-version", type=_cua, default="v3")
    add_screen_flags(p_predict)
    p_predict.add_argument("--trajectory", type=int, default=0, help="prior screenshots attached")
    p_predict.add_argument("--system-prompt-chars", type=int, default=0)
    p_predict.add_argument("--count", type=int, default=1)

    p_session = sub.add_parser("session", help="a session: create + N predict steps")
    p_session.add_argument("--steps", type=int, default=5)
    p_session.add_argument("--cua-version", type=_cua, default="v3")
    add_screen_flags(p_session)
    p_session.add_argument("--trajectory-per-step", type=int, default=0)
    p_session.add_argument("--system-prompt-chars", type=int, default=0)

    p_ground = sub.add_parser("ground", help="one or more POST /ground calls")
    add_screen_flags(p_ground)
    p_ground.add_argument("--count", type=int, default=1)

    p_run = sub.add_parser("run", help="a task run of N agent steps")
    p_run.add_argument("--steps", type=int, required=True)
    p_run.add_argument("--cua-version", type=_cua, default="v3")

    p_workflow = sub.add_parser("workflow", help="a workflow run of N billed task steps")
    p_workflow.add_argument("--task-steps", type=int, required=True)
    p_workflow.add_argument("--cua-version", type=_cua, default="v3")

    p_machine = sub.add_parser("machine", help="machine runtime + optional snapshots")
    p_machine.add_argument("--os", type=_os, default="linux")
    p_machine.add_argument("--hours", type=float, default=1.0)
    p_machine.add_argument(
        "--state", choices=("running", "stopped", "suspended"), default="running"
    )
    p_machine.add_argument("--snapshots", type=int, default=0)

    p_plan = sub.add_parser("plan", help="total a JSON batch plan")
    p_plan.add_argument("path", type=Path, help='JSON file: {"items": [{"kind": ...}, ...]}')

    return parser


def estimate_from_args(args: argparse.Namespace) -> CostEstimate:
    """Map parsed CLI args onto the right estimator (pure, easily tested)."""
    command = str(args.command)
    if command == "predict":
        return estimate_predict_cmd(
            cua_version=args.cua_version,
            width=args.width,
            height=args.height,
            trajectory=args.trajectory,
            system_prompt_chars=args.system_prompt_chars,
            count=args.count,
        )
    if command == "session":
        return estimate_session_cmd(
            steps=args.steps,
            cua_version=args.cua_version,
            width=args.width,
            height=args.height,
            trajectory_per_step=args.trajectory_per_step,
            system_prompt_chars=args.system_prompt_chars,
        )
    if command == "ground":
        return estimate_ground_cmd(width=args.width, height=args.height, count=args.count)
    if command == "run":
        return estimate_run_cmd(steps=args.steps, cua_version=args.cua_version)
    if command == "workflow":
        return estimate_workflow_cmd(task_steps=args.task_steps, cua_version=args.cua_version)
    if command == "machine":
        return estimate_machine_cmd(
            os_type=args.os, hours=args.hours, state=args.state, snapshots=args.snapshots
        )
    if command == "plan":
        return estimate_plan(load_plan(args.path))
    raise ValueError(f"unknown command {command!r}")  # unreachable via argparse


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        estimate = estimate_from_args(args)
    except (PlanError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    api_key = env.get_api_key()
    sandbox = api_key is not None and env.is_sandbox_key(api_key)
    title = f"Estimated cost ({args.command})"
    print(cost.format_estimate(estimate, title=title, sandbox=sandbox))
    if not sandbox:
        print(SANDBOX_NOTE)
    print(REFUND_NOTE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
