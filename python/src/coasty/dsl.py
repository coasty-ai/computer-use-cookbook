"""Typed builders + validator for the Coasty workflow DSL (2026-06-01).

Step constructors: :func:`task`, :func:`assert_`, :func:`if_`, :func:`loop`,
:func:`parallel`, :func:`human_approval`, :func:`retry`, :func:`succeed`,
:func:`fail`. Condition constructors cover all 13 ops.

:func:`validate` enforces the documented limits client-side:

- <= 200 steps total (counting every nested step), <= 8 nesting levels,
- <= 16 parallel branches, ``retry.max_attempts`` in 1..20,
- no ``human_approval`` / ``succeed`` / ``fail`` inside a parallel branch,
- ``save_as`` not in ``{"inputs", "vars"}``, step ids match
  ``^[A-Za-z0-9_-]{1,64}$``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from .types import CuaVersion, OnAwaitingHuman

Condition = dict[str, Any]
Step = dict[str, Any]
WorkflowDefinition = dict[str, Any]

DSL_VERSION = "2026-06-01"
MAX_TOTAL_STEPS = 200
MAX_NESTING_DEPTH = 8
MAX_PARALLEL_BRANCHES = 16
RETRY_ATTEMPTS_MIN = 1
RETRY_ATTEMPTS_MAX = 20
RESERVED_SAVE_AS = frozenset({"inputs", "vars"})
STEP_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

STEP_TYPES = frozenset(
    {"task", "assert", "if", "loop", "parallel", "human_approval", "retry", "succeed", "fail"}
)
_COMPARISON_OPS = frozenset({"eq", "ne", "lt", "gt", "lte", "gte", "contains"})
_UNARY_VALUE_OPS = frozenset({"truthy", "falsy", "exists"})
_COMBINER_OPS = frozenset({"and", "or"})
CONDITION_OPS = _COMPARISON_OPS | _UNARY_VALUE_OPS | _COMBINER_OPS | {"not"}
_FORBIDDEN_IN_PARALLEL = frozenset({"human_approval", "succeed", "fail"})


class DSLValidationError(ValueError):
    """Raised by :func:`validate`; ``problems`` lists every violation found."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems: list[str] = list(problems)
        joined = "\n  - ".join(self.problems)
        super().__init__(f"invalid workflow definition:\n  - {joined}")


def _drop_none(mapping: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in mapping.items() if value is not None}


# ── Condition constructors (13 ops) ────────────────────────────────────────


def eq(left: Any, right: Any) -> Condition:
    return {"op": "eq", "left": left, "right": right}


def ne(left: Any, right: Any) -> Condition:
    return {"op": "ne", "left": left, "right": right}


def lt(left: Any, right: Any) -> Condition:
    return {"op": "lt", "left": left, "right": right}


def gt(left: Any, right: Any) -> Condition:
    return {"op": "gt", "left": left, "right": right}


def lte(left: Any, right: Any) -> Condition:
    return {"op": "lte", "left": left, "right": right}


def gte(left: Any, right: Any) -> Condition:
    return {"op": "gte", "left": left, "right": right}


def contains(left: Any, right: Any) -> Condition:
    return {"op": "contains", "left": left, "right": right}


def truthy(value: Any) -> Condition:
    return {"op": "truthy", "value": value}


def falsy(value: Any) -> Condition:
    return {"op": "falsy", "value": value}


def exists(value: Any) -> Condition:
    return {"op": "exists", "value": value}


def and_(*conditions: Condition) -> Condition:
    return {"op": "and", "conditions": list(conditions)}


def or_(*conditions: Condition) -> Condition:
    return {"op": "or", "conditions": list(conditions)}


def not_(condition: Condition) -> Condition:
    return {"op": "not", "condition": condition}


# ── Step constructors (9 types) ────────────────────────────────────────────


def task(
    step_id: str,
    task: str,
    *,
    machine_id: str | None = None,
    cua_version: CuaVersion | None = None,
    instructions: str | None = None,
    system_prompt: str | None = None,
    max_steps: int | None = None,
    save_as: str | None = None,
    on_awaiting_human: OnAwaitingHuman | None = None,
) -> Step:
    """A ``task`` step. Binds ``{status, passed, result, run_id, steps, error}``
    under ``save_as`` (and the step id)."""
    step: Step = {"id": step_id, "type": "task", "task": task}
    step.update(
        _drop_none(
            {
                "machine_id": machine_id,
                "cua_version": cua_version,
                "instructions": instructions,
                "system_prompt": system_prompt,
                "max_steps": max_steps,
                "save_as": save_as,
                "on_awaiting_human": on_awaiting_human,
            }
        )
    )
    return step


def assert_(step_id: str, condition: Condition, *, message: str | None = None) -> Step:
    """An ``assert`` step: fail the workflow unless ``condition`` holds."""
    step: Step = {"id": step_id, "type": "assert", "condition": condition}
    if message is not None:
        step["message"] = message
    return step


def if_(
    step_id: str,
    condition: Condition,
    then: Sequence[Step],
    *,
    else_: Sequence[Step] | None = None,
) -> Step:
    """An ``if`` step branching on a structured condition."""
    step: Step = {"id": step_id, "type": "if", "condition": condition, "then": list(then)}
    if else_ is not None:
        step["else"] = list(else_)
    return step


def loop(
    step_id: str,
    body: Sequence[Step],
    *,
    count: int | None = None,
    while_: Condition | None = None,
    max_iterations: int | None = None,
) -> Step:
    """A ``loop`` step. Exactly one of ``count`` / ``while_`` is required."""
    if (count is None) == (while_ is None):
        raise ValueError("loop() requires exactly one of count= or while_=")
    step: Step = {"id": step_id, "type": "loop", "body": list(body)}
    if count is not None:
        step["count"] = count
    if while_ is not None:
        step["while"] = while_
    if max_iterations is not None:
        step["max_iterations"] = max_iterations
    return step


def parallel(step_id: str, branches: Sequence[Sequence[Step]]) -> Step:
    """A ``parallel`` step running independent branches concurrently."""
    return {"id": step_id, "type": "parallel", "branches": [list(b) for b in branches]}


def human_approval(
    step_id: str,
    *,
    message: str | None = None,
    timeout_seconds: int | None = None,
) -> Step:
    """A ``human_approval`` step: pause until approved/rejected."""
    step: Step = {"id": step_id, "type": "human_approval"}
    step.update(_drop_none({"message": message, "timeout_seconds": timeout_seconds}))
    return step


def retry(step_id: str, body: Sequence[Step], *, max_attempts: int) -> Step:
    """A ``retry`` step: re-run ``body`` up to ``max_attempts`` times (1-20)."""
    return {"id": step_id, "type": "retry", "body": list(body), "max_attempts": max_attempts}


def succeed(step_id: str, *, output: Mapping[str, Any] | None = None) -> Step:
    """A ``succeed`` step finishing the workflow with an optional output."""
    step: Step = {"id": step_id, "type": "succeed"}
    if output is not None:
        step["output"] = dict(output)
    return step


def fail(step_id: str, *, message: str | None = None) -> Step:
    """A ``fail`` step finishing the workflow as failed."""
    step: Step = {"id": step_id, "type": "fail"}
    if message is not None:
        step["message"] = message
    return step


def definition(
    steps: Sequence[Step],
    *,
    output: Mapping[str, Any] | None = None,
) -> WorkflowDefinition:
    """Wrap steps (and an optional top-level ``output``) into a definition."""
    result: WorkflowDefinition = {"steps": list(steps)}
    if output is not None:
        result["output"] = dict(output)
    return result


# ── Validation ─────────────────────────────────────────────────────────────


def _validate_condition(condition: object, where: str, problems: list[str]) -> None:
    if not isinstance(condition, Mapping):
        problems.append(f"{where}: condition must be an object, got {type(condition).__name__}")
        return
    op = condition.get("op")
    if not isinstance(op, str) or op not in CONDITION_OPS:
        problems.append(f"{where}: unknown condition op {op!r}")
        return
    if op in _COMPARISON_OPS:
        for key in ("left", "right"):
            if key not in condition:
                problems.append(f"{where}: condition op {op!r} requires {key!r}")
    elif op in _UNARY_VALUE_OPS:
        if "value" not in condition:
            problems.append(f"{where}: condition op {op!r} requires 'value'")
    elif op in _COMBINER_OPS:
        nested = condition.get("conditions")
        if not isinstance(nested, list) or not nested:
            problems.append(f"{where}: condition op {op!r} requires a non-empty 'conditions' list")
        else:
            for index, child in enumerate(nested):
                _validate_condition(child, f"{where}.conditions[{index}]", problems)
    else:  # not
        if "condition" not in condition:
            problems.append(f"{where}: condition op 'not' requires 'condition'")
        else:
            _validate_condition(condition["condition"], f"{where}.condition", problems)


def _validate_steps(
    steps: object,
    *,
    where: str,
    depth: int,
    inside_parallel: bool,
    problems: list[str],
) -> int:
    """Validate a step list; returns the number of steps counted (recursively)."""
    if not isinstance(steps, list):
        problems.append(f"{where}: steps must be a list, got {type(steps).__name__}")
        return 0
    if depth > MAX_NESTING_DEPTH:
        problems.append(
            f"{where}: nesting depth {depth} exceeds the maximum of {MAX_NESTING_DEPTH}"
        )
        return len(steps)

    count = 0
    for index, step in enumerate(steps):
        label = f"{where}[{index}]"
        count += 1
        if not isinstance(step, Mapping):
            problems.append(f"{label}: step must be an object, got {type(step).__name__}")
            continue

        step_id = step.get("id")
        if not isinstance(step_id, str) or not STEP_ID_RE.fullmatch(step_id):
            problems.append(f"{label}: step id {step_id!r} must match ^[A-Za-z0-9_-]{{1,64}}$")
        else:
            label = f"{where}[{index}] (id={step_id})"

        step_type = step.get("type")
        if not isinstance(step_type, str) or step_type not in STEP_TYPES:
            problems.append(f"{label}: unknown step type {step_type!r}")
            continue

        if inside_parallel and step_type in _FORBIDDEN_IN_PARALLEL:
            problems.append(
                f"{label}: {step_type!r} steps are not allowed inside a parallel branch"
            )

        if step_type == "task":
            task_text = step.get("task")
            if not isinstance(task_text, str) or not task_text:
                problems.append(f"{label}: task steps require a non-empty 'task' string")
            save_as = step.get("save_as")
            if save_as is not None:
                if not isinstance(save_as, str):
                    problems.append(f"{label}: save_as must be a string")
                elif save_as in RESERVED_SAVE_AS:
                    problems.append(f"{label}: save_as {save_as!r} is reserved (inputs, vars)")
        elif step_type == "assert":
            if "condition" not in step:
                problems.append(f"{label}: assert steps require a 'condition'")
            else:
                _validate_condition(step["condition"], label, problems)
        elif step_type == "if":
            if "condition" not in step:
                problems.append(f"{label}: if steps require a 'condition'")
            else:
                _validate_condition(step["condition"], label, problems)
            if "then" not in step:
                problems.append(f"{label}: if steps require a 'then' branch")
            else:
                count += _validate_steps(
                    step["then"],
                    where=f"{label}.then",
                    depth=depth + 1,
                    inside_parallel=inside_parallel,
                    problems=problems,
                )
            if "else" in step:
                count += _validate_steps(
                    step["else"],
                    where=f"{label}.else",
                    depth=depth + 1,
                    inside_parallel=inside_parallel,
                    problems=problems,
                )
        elif step_type == "loop":
            has_count = "count" in step
            has_while = "while" in step
            if has_count == has_while:
                problems.append(f"{label}: loop steps require exactly one of 'count' or 'while'")
            if has_count and (not isinstance(step["count"], int) or step["count"] < 1):
                problems.append(f"{label}: loop count must be an integer >= 1")
            if has_while:
                _validate_condition(step["while"], f"{label}.while", problems)
            max_iterations = step.get("max_iterations")
            if max_iterations is not None and (
                not isinstance(max_iterations, int) or max_iterations < 1
            ):
                problems.append(f"{label}: max_iterations must be an integer >= 1")
            if "body" not in step:
                problems.append(f"{label}: loop steps require a 'body'")
            else:
                count += _validate_steps(
                    step["body"],
                    where=f"{label}.body",
                    depth=depth + 1,
                    inside_parallel=inside_parallel,
                    problems=problems,
                )
        elif step_type == "parallel":
            branches = step.get("branches")
            if not isinstance(branches, list) or not branches:
                problems.append(f"{label}: parallel steps require a non-empty 'branches' list")
            else:
                if len(branches) > MAX_PARALLEL_BRANCHES:
                    problems.append(
                        f"{label}: {len(branches)} parallel branches exceed the "
                        f"maximum of {MAX_PARALLEL_BRANCHES}"
                    )
                for branch_index, branch in enumerate(branches):
                    count += _validate_steps(
                        branch,
                        where=f"{label}.branches[{branch_index}]",
                        depth=depth + 1,
                        inside_parallel=True,
                        problems=problems,
                    )
        elif step_type == "retry":
            max_attempts = step.get("max_attempts")
            if (
                not isinstance(max_attempts, int)
                or isinstance(max_attempts, bool)
                or not RETRY_ATTEMPTS_MIN <= max_attempts <= RETRY_ATTEMPTS_MAX
            ):
                problems.append(
                    f"{label}: retry max_attempts must be an integer in "
                    f"{RETRY_ATTEMPTS_MIN}..{RETRY_ATTEMPTS_MAX}"
                )
            if "body" not in step:
                problems.append(f"{label}: retry steps require a 'body'")
            else:
                count += _validate_steps(
                    step["body"],
                    where=f"{label}.body",
                    depth=depth + 1,
                    inside_parallel=inside_parallel,
                    problems=problems,
                )
        # human_approval / succeed / fail need no extra field checks
    return count


def validate(definition: Mapping[str, Any]) -> None:
    """Validate a workflow definition against the documented limits.

    Raises :class:`DSLValidationError` (with a ``problems`` list) on any
    violation; returns ``None`` when the definition is structurally valid.
    """
    problems: list[str] = []
    if not isinstance(definition, Mapping):
        raise DSLValidationError([f"definition must be an object, got {type(definition).__name__}"])
    if "steps" not in definition:
        problems.append("definition requires a 'steps' list")
        raise DSLValidationError(problems)

    total = _validate_steps(
        definition["steps"],
        where="steps",
        depth=1,
        inside_parallel=False,
        problems=problems,
    )
    if total > MAX_TOTAL_STEPS:
        problems.append(
            f"definition has {total} steps (counting nested); the maximum is {MAX_TOTAL_STEPS}"
        )
    if problems:
        raise DSLValidationError(problems)
