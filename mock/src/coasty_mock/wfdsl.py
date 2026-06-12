"""Workflow DSL structural validation (dsl_version 2026-06-01).

Enforces every documented limit from docs/API_NOTES.md §Workflows:

- step ids match ``^[A-Za-z0-9_-]{1,64}$``; types are one of the 9 step types
- conditions use exactly the 13 documented ops with the documented shapes
- at most 200 steps total (counting every nested step)
- at most 8 levels of nesting (if/loop/parallel/retry bodies)
- a parallel step has 1-16 branches; ``human_approval`` / ``succeed`` /
  ``fail`` are rejected anywhere inside a parallel branch
- ``retry.max_attempts`` is required, an integer 1-20
- ``save_as`` must not be ``inputs`` or ``vars``

Violations raise a 422 VALIDATION_ERROR whose ``details`` name the exact loc.
"""

from __future__ import annotations

import re
from typing import Any

from .validation import Validator

JsonDict = dict[str, Any]
Loc = list[str | int]

DSL_VERSION = "2026-06-01"
STEP_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
STEP_TYPES = {
    "task",
    "assert",
    "if",
    "loop",
    "parallel",
    "human_approval",
    "retry",
    "succeed",
    "fail",
}
BINARY_OPS = {"eq", "ne", "lt", "gt", "lte", "gte", "contains"}
UNARY_OPS = {"truthy", "falsy", "exists"}
COMBINING_OPS = {"and", "or"}
CONDITION_OPS = BINARY_OPS | UNARY_OPS | COMBINING_OPS | {"not"}
RESERVED_SAVE_AS = {"inputs", "vars"}
MAX_TOTAL_STEPS = 200
MAX_DEPTH = 8
MAX_PARALLEL_BRANCHES = 16
FORBIDDEN_IN_PARALLEL = {"human_approval", "succeed", "fail"}


def validate_condition(condition: Any, loc: Loc, vd: Validator) -> None:
    if not isinstance(condition, dict):
        vd.add(loc, "expected a condition object", "type_error")
        return
    op = condition.get("op")
    if op not in CONDITION_OPS:
        vd.add([*loc, "op"], f"unknown condition op {op!r}; must be one of {sorted(CONDITION_OPS)}")
        return
    if op in BINARY_OPS:
        for field in ("left", "right"):
            if field not in condition:
                vd.add([*loc, field], "field required", "missing")
    elif op in UNARY_OPS:
        if "value" not in condition:
            vd.add([*loc, "value"], "field required", "missing")
    elif op in COMBINING_OPS:
        conditions = condition.get("conditions")
        if not isinstance(conditions, list) or not conditions:
            vd.add([*loc, "conditions"], "expected a non-empty array of conditions")
        else:
            for index, sub in enumerate(conditions):
                validate_condition(sub, [*loc, "conditions", index], vd)
    else:  # not
        validate_condition(condition.get("condition"), [*loc, "condition"], vd)


class _Walker:
    def __init__(self, vd: Validator) -> None:
        self.vd = vd
        self.total_steps = 0
        self.depth_flagged = False

    def walk_steps(self, steps: Any, loc: Loc, depth: int, in_parallel: bool) -> None:
        if not isinstance(steps, list):
            self.vd.add(loc, "expected an array of steps", "type_error")
            return
        if depth > MAX_DEPTH:
            if not self.depth_flagged:
                self.vd.add(loc, f"steps nest deeper than {MAX_DEPTH} levels")
                self.depth_flagged = True
            return
        for index, step in enumerate(steps):
            self.walk_step(step, [*loc, index], depth, in_parallel)

    def walk_step(self, step: Any, loc: Loc, depth: int, in_parallel: bool) -> None:
        self.total_steps += 1
        if not isinstance(step, dict):
            self.vd.add(loc, "expected a step object", "type_error")
            return
        step_id = step.get("id")
        if not isinstance(step_id, str) or not STEP_ID_RE.fullmatch(step_id):
            self.vd.add([*loc, "id"], f"step id must match {STEP_ID_RE.pattern}")
        step_type = step.get("type")
        if step_type not in STEP_TYPES:
            self.vd.add(
                [*loc, "type"],
                f"unknown step type {step_type!r}; must be one of {sorted(STEP_TYPES)}",
            )
            return
        if in_parallel and step_type in FORBIDDEN_IN_PARALLEL:
            self.vd.add([*loc, "type"], f"{step_type} is not allowed inside a parallel branch")
        handler = getattr(self, f"_walk_{step_type}")
        handler(step, loc, depth, in_parallel)

    def _walk_task(self, step: JsonDict, loc: Loc, depth: int, in_parallel: bool) -> None:
        task = step.get("task")
        if not isinstance(task, str) or not 1 <= len(task) <= 16000:
            self.vd.add([*loc, "task"], "field required: a string of 1-16000 chars", "missing")
        save_as = step.get("save_as")
        if save_as is not None:
            if not isinstance(save_as, str) or not STEP_ID_RE.fullmatch(save_as):
                self.vd.add([*loc, "save_as"], f"must match {STEP_ID_RE.pattern}")
            elif save_as in RESERVED_SAVE_AS:
                self.vd.add([*loc, "save_as"], "must not be 'inputs' or 'vars' (reserved)")
        cua_version = step.get("cua_version")
        if cua_version is not None and cua_version not in {"v1", "v3", "v4"}:
            self.vd.add([*loc, "cua_version"], "must be one of ['v1', 'v3', 'v4']")
        max_steps = step.get("max_steps")
        if max_steps is not None and (
            isinstance(max_steps, bool)
            or not isinstance(max_steps, int)
            or not 1 <= max_steps <= 1000
        ):
            self.vd.add([*loc, "max_steps"], "must be an integer 1-1000")
        on_ah = step.get("on_awaiting_human")
        if on_ah is not None and on_ah not in {"pause", "fail", "cancel"}:
            self.vd.add([*loc, "on_awaiting_human"], "must be one of ['cancel', 'fail', 'pause']")

    def _walk_assert(self, step: JsonDict, loc: Loc, depth: int, in_parallel: bool) -> None:
        validate_condition(step.get("condition"), [*loc, "condition"], self.vd)

    def _walk_if(self, step: JsonDict, loc: Loc, depth: int, in_parallel: bool) -> None:
        validate_condition(step.get("condition"), [*loc, "condition"], self.vd)
        self.walk_steps(step.get("then"), [*loc, "then"], depth + 1, in_parallel)
        if "else" in step:
            self.walk_steps(step.get("else"), [*loc, "else"], depth + 1, in_parallel)

    def _walk_loop(self, step: JsonDict, loc: Loc, depth: int, in_parallel: bool) -> None:
        has_count = "count" in step
        has_while = "while" in step
        if has_count == has_while:
            self.vd.add(loc, "loop requires exactly one of 'count' or 'while'")
        if has_count:
            count = step.get("count")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                self.vd.add([*loc, "count"], "must be a non-negative integer")
        if has_while:
            validate_condition(step.get("while"), [*loc, "while"], self.vd)
        max_iterations = step.get("max_iterations")
        if max_iterations is not None and (
            isinstance(max_iterations, bool)
            or not isinstance(max_iterations, int)
            or not 1 <= max_iterations <= 100000
        ):
            self.vd.add([*loc, "max_iterations"], "must be an integer 1-100000")
        self.walk_steps(step.get("body"), [*loc, "body"], depth + 1, in_parallel)

    def _walk_parallel(self, step: JsonDict, loc: Loc, depth: int, in_parallel: bool) -> None:
        branches = step.get("branches")
        if not isinstance(branches, list) or not branches:
            self.vd.add([*loc, "branches"], "expected a non-empty array of branches")
            return
        if len(branches) > MAX_PARALLEL_BRANCHES:
            self.vd.add(
                [*loc, "branches"], f"at most {MAX_PARALLEL_BRANCHES} parallel branches are allowed"
            )
        for index, branch in enumerate(branches):
            self.walk_steps(branch, [*loc, "branches", index], depth + 1, True)

    def _walk_human_approval(self, step: JsonDict, loc: Loc, depth: int, in_parallel: bool) -> None:
        timeout = step.get("timeout_seconds")
        if timeout is not None and (
            isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 86400
        ):
            self.vd.add([*loc, "timeout_seconds"], "must be an integer 1-86400")

    def _walk_retry(self, step: JsonDict, loc: Loc, depth: int, in_parallel: bool) -> None:
        max_attempts = step.get("max_attempts")
        if (
            isinstance(max_attempts, bool)
            or not isinstance(max_attempts, int)
            or not 1 <= max_attempts <= 20
        ):
            self.vd.add([*loc, "max_attempts"], "field required: an integer 1-20")
        self.walk_steps(step.get("body"), [*loc, "body"], depth + 1, in_parallel)

    def _walk_succeed(self, step: JsonDict, loc: Loc, depth: int, in_parallel: bool) -> None:
        output = step.get("output")
        if output is not None and not isinstance(output, dict):
            self.vd.add([*loc, "output"], "expected an object", "type_error")

    def _walk_fail(self, step: JsonDict, loc: Loc, depth: int, in_parallel: bool) -> None:
        message = step.get("message")
        if message is not None and not isinstance(message, str):
            self.vd.add([*loc, "message"], "expected a string", "type_error")


def validate_definition(definition: Any, *, loc_prefix: str = "definition") -> None:
    """Validate a workflow definition; raises 422 VALIDATION_ERROR on failure."""
    vd = Validator()
    base: Loc = ["body", loc_prefix]
    if not isinstance(definition, dict):
        vd.add(base, "field required: the workflow DSL object", "missing")
        vd.raise_if_any()
        return
    steps = definition.get("steps")
    if not isinstance(steps, list) or not steps:
        vd.add([*base, "steps"], "expected a non-empty array of steps")
        vd.raise_if_any()
        return
    walker = _Walker(vd)
    walker.walk_steps(steps, [*base, "steps"], 1, False)
    if walker.total_steps > MAX_TOTAL_STEPS:
        vd.add(
            [*base, "steps"],
            f"definition has {walker.total_steps} steps; at most {MAX_TOTAL_STEPS} are allowed",
        )
    output = definition.get("output")
    if output is not None and not isinstance(output, dict):
        vd.add([*base, "output"], "expected an object", "type_error")
    vd.raise_if_any()
