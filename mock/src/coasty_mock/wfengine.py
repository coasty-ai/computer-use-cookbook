"""Deterministic workflow DSL interpreter.

The engine is a Python generator so ``human_approval`` can genuinely pause:
``next(gen)`` executes until the first approval request (yielded as
``{"step_id", "message"}``) or completion; ``gen.send({"approved", "note"})``
resumes it. The driver (routes_workflows) owns run-status bookkeeping around
those pauses.

Execution conventions (documented in mock/README.md):

- ``task`` steps never abort the workflow by themselves; they bind
  ``{status, passed, result, run_id, steps, error}`` under the step id and
  ``save_as``. Markers in the *resolved* task text steer the outcome:
  ``[fail]`` -> passed=false; ``[flaky:N]`` -> passes from the Nth execution
  of that step (per run) so ``retry`` success paths are testable.
- ``assert`` failures and rejected approvals raise :class:`StepFailure`,
  which ``retry`` catches; a ``fail`` step (and guard breaches) raise
  :class:`WorkflowFailure`, which nothing catches.
- Guards: ``budget_cents`` (spent_cents accrues the nominal step price for
  every key kind so budget demos work offline), ``max_iterations`` (total
  loop iterations), ``deadline_seconds`` (each task step advances the frozen
  clock by ``config.workflow_task_step_seconds``). A breach fails the run
  with ``error.code == "GUARD_EXCEEDED"``.
- ``vars`` namespace: ``vars.iteration`` (innermost loop) and
  ``vars.attempt`` (innermost retry) are maintained by the engine.
"""

from __future__ import annotations

import re
from typing import Any

from .clock import iso
from .pricing import run_step_price
from .sse import append_event
from .state import TestState, WfGen
from .webhooks import emit_webhook

JsonDict = dict[str, Any]

_TEMPLATE_RE = re.compile(r"\{\{\s*([A-Za-z0-9_][A-Za-z0-9_.\-]*)\s*\}\}")
_FLAKY_RE = re.compile(r"\[flaky:(\d+)\]")


class WorkflowFailure(Exception):
    """Terminates the workflow run as failed; not caught by retry."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class StepFailure(Exception):
    """A retryable step failure (failed assert or rejected approval)."""


class _Succeeded(Exception):
    """Raised by a ``succeed`` step to short-circuit the run."""

    def __init__(self, output: JsonDict | None) -> None:
        super().__init__("workflow succeeded")
        self.output = output


def notify_wf(state: TestState, run: JsonDict, event: str) -> None:
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
            "workflow_id": run.get("workflow_id"),
            "status": run["status"],
            "output": run.get("output"),
            "error": run.get("error"),
            "awaiting_step_id": run.get("awaiting_step_id"),
            "created_at": iso(state.clock.now()),
        },
    )


def finish_wf_run(
    state: TestState,
    run: JsonDict,
    status: str,
    *,
    output: JsonDict | None = None,
    error: JsonDict | None = None,
) -> None:
    run["status"] = status
    run["output"] = output
    run["error"] = error
    run["awaiting_step_id"] = None
    run["awaiting_human_reason"] = None
    run["finished_at"] = iso(state.clock.now())
    log = state.wf_events[run["id"]]
    append_event(log, "status", {"status": status}, state.clock)
    append_event(log, "done", {"status": status, "output": output, "error": error}, state.clock)
    notify_wf(state, run, f"workflow_run.{status}")


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


class WorkflowEngine:
    def __init__(self, state: TestState, run: JsonDict, definition: JsonDict) -> None:
        self.state = state
        self.run = run
        self.definition = definition
        inputs = run.get("inputs") or {}
        self.ctx: JsonDict = {"inputs": dict(inputs), "vars": {"iteration": 0, "attempt": 1}}
        self.exec_counts: dict[str, int] = {}

    # ------------------------------------------------------------- plumbing
    def _emit(self, event_type: str, data: JsonDict) -> None:
        append_event(self.state.wf_events[self.run["id"]], event_type, data, self.state.clock)

    # ----------------------------------------------------------- templating
    def _resolve(self, path: str) -> Any:
        current: Any = self.ctx
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current

    def _template_str(self, value: str) -> Any:
        full = _TEMPLATE_RE.fullmatch(value.strip())
        if full:
            return self._resolve(full.group(1))

        def _sub(match: re.Match[str]) -> str:
            resolved = self._resolve(match.group(1))
            return "" if resolved is None else str(resolved)

        return _TEMPLATE_RE.sub(_sub, value)

    def _template(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._template_str(value)
        if isinstance(value, dict):
            return {key: self._template(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._template(item) for item in value]
        return value

    # ----------------------------------------------------------- conditions
    def _eval(self, condition: JsonDict) -> bool:
        op = str(condition["op"])
        if op in {"and", "or"}:
            results = [self._eval(sub) for sub in condition["conditions"]]
            return all(results) if op == "and" else any(results)
        if op == "not":
            return not self._eval(condition["condition"])
        if op in {"truthy", "falsy", "exists"}:
            value = self._template(condition.get("value"))
            if op == "exists":
                return value is not None
            return bool(value) if op == "truthy" else not bool(value)
        left = self._template(condition.get("left"))
        right = self._template(condition.get("right"))
        if op == "eq":
            return bool(left == right)
        if op == "ne":
            return bool(left != right)
        if op == "contains":
            if isinstance(left, str):
                return str(right) in left
            if isinstance(left, (list, tuple, set, dict)):
                return right in left
            return False
        left_num, right_num = _as_number(left), _as_number(right)
        if left_num is None or right_num is None:
            return False
        if op == "lt":
            return left_num < right_num
        if op == "gt":
            return left_num > right_num
        if op == "lte":
            return left_num <= right_num
        return left_num >= right_num  # gte

    # ------------------------------------------------------------ execution
    def execute(self) -> WfGen:
        run = self.run
        run["status"] = "running"
        run["started_at"] = iso(self.state.clock.now())
        run["_started_epoch"] = self.state.clock.now()
        self._emit("status", {"status": "running"})
        try:
            yield from self._exec_steps(list(self.definition.get("steps", [])))
        except _Succeeded as succeeded:
            finish_wf_run(self.state, run, "succeeded", output=succeeded.output)
            return
        except StepFailure as failure:
            finish_wf_run(
                self.state,
                run,
                "failed",
                error={"code": "STEP_FAILED", "message": str(failure)},
            )
            return
        except WorkflowFailure as failure:
            finish_wf_run(
                self.state,
                run,
                "failed",
                error={"code": failure.code, "message": failure.message},
            )
            return
        output = self._template(self.definition.get("output"))
        if output is not None and not isinstance(output, dict):
            output = {"value": output}
        finish_wf_run(self.state, run, "succeeded", output=output)

    def _exec_steps(self, steps: list[JsonDict]) -> WfGen:
        for step in steps:
            yield from self._exec_step(step)

    def _exec_step(self, step: JsonDict) -> WfGen:
        step_type = str(step["type"])
        if step_type == "task":
            self._exec_task(step)
        elif step_type == "assert":
            self._exec_assert(step)
        elif step_type == "if":
            branch = step.get("then", []) if self._eval(step["condition"]) else step.get("else", [])
            yield from self._exec_steps(list(branch or []))
        elif step_type == "loop":
            yield from self._exec_loop(step)
        elif step_type == "parallel":
            for branch_steps in step["branches"]:
                yield from self._exec_steps(list(branch_steps))
        elif step_type == "retry":
            yield from self._exec_retry(step)
        elif step_type == "human_approval":
            yield from self._exec_approval(step)
        elif step_type == "succeed":
            output = self._template(step.get("output"))
            raise _Succeeded(output if isinstance(output, dict) else None)
        else:  # fail
            message = self._template(step.get("message"))
            raise WorkflowFailure(
                "WORKFLOW_FAILED", str(message) if message else f"fail step '{step['id']}'"
            )

    def _exec_assert(self, step: JsonDict) -> None:
        if self._eval(step["condition"]):
            self._emit("step", {"step_id": step["id"], "type": "assert", "passed": True})
            return
        message = self._template(step.get("message"))
        raise StepFailure(str(message) if message else f"assert '{step['id']}' failed")

    def _exec_task(self, step: JsonDict) -> None:
        state, run = self.state, self.run
        price = run_step_price(str(step.get("cua_version") or "v3"))
        budget = int(run.get("budget_cents") or 0)
        if budget and int(run["spent_cents"]) + price > budget:
            raise WorkflowFailure(
                "GUARD_EXCEEDED",
                f"budget_cents ({budget}) would be exceeded by the next task step.",
            )
        state.clock.advance(state.config.workflow_task_step_seconds)
        deadline = run.get("_deadline_seconds")
        if deadline is not None and state.clock.now() - float(run["_started_epoch"]) > float(
            deadline
        ):
            raise WorkflowFailure("GUARD_EXCEEDED", f"deadline_seconds ({deadline}) exceeded.")
        if run["_mode"] != "test":
            if state.wallet_balance_cents < price:
                raise WorkflowFailure("WALLET_EXHAUSTED", "API wallet ran dry mid-workflow.")
            state.wallet_balance_cents -= price
            state.record_usage("workflows.task_step", price)
        else:
            state.record_usage("workflows.task_step", 0)
        run["spent_cents"] = int(run["spent_cents"]) + price

        step_id = str(step["id"])
        task_text = str(self._template_str(str(step["task"])))
        count = self.exec_counts.get(step_id, 0) + 1
        self.exec_counts[step_id] = count
        lowered = task_text.lower()
        passed = "[fail]" not in lowered
        flaky = _FLAKY_RE.search(lowered)
        if flaky:
            passed = count >= int(flaky.group(1))
        binding: JsonDict = {
            "status": "succeeded" if passed else "failed",
            "passed": passed,
            "result": f"{'Completed' if passed else 'Failed'}: {task_text}",
            "run_id": "run_" + state.deterministic_hex(f"wftask:{run['id']}:{step_id}:{count}", 8),
            "steps": state.config.run_success_steps,
            "error": (
                None
                if passed
                else {"code": "TASK_FAILED", "message": f"Task failed: {task_text[:120]}"}
            ),
        }
        self.ctx[step_id] = binding
        save_as = step.get("save_as")
        if isinstance(save_as, str) and save_as:
            self.ctx[save_as] = binding
        self._emit(
            "step",
            {"step_id": step_id, "type": "task", "passed": passed, "status": binding["status"]},
        )
        self._emit("billing", {"spent_cents": run["spent_cents"], "credits_charged": price})

    def _exec_loop(self, step: JsonDict) -> WfGen:
        run = self.run
        count = step.get("count")
        cap = int(step.get("max_iterations") or 100)
        guard = run.get("_max_iterations")
        iteration = 0
        self.ctx["vars"]["iteration"] = 0
        while True:
            if count is not None:
                if iteration >= int(count):
                    break
            elif not self._eval(step["while"]):
                break
            if iteration >= cap:
                break
            iteration += 1
            run["iterations_used"] = int(run["iterations_used"]) + 1
            if guard is not None and int(run["iterations_used"]) > int(guard):
                raise WorkflowFailure("GUARD_EXCEEDED", f"max_iterations ({guard}) exceeded.")
            self.ctx["vars"]["iteration"] = iteration
            yield from self._exec_steps(list(step["body"]))

    def _exec_retry(self, step: JsonDict) -> WfGen:
        max_attempts = int(step["max_attempts"])
        for attempt in range(1, max_attempts + 1):
            self.ctx["vars"]["attempt"] = attempt
            try:
                yield from self._exec_steps(list(step["body"]))
                return
            except StepFailure as failure:
                if attempt >= max_attempts:
                    raise
                self._emit(
                    "text",
                    {
                        "text": f"retry '{step['id']}' attempt {attempt} failed "
                        f"({failure}); retrying"
                    },
                )

    def _exec_approval(self, step: JsonDict) -> WfGen:
        message = self._template(step.get("message"))
        request: JsonDict = {
            "step_id": str(step["id"]),
            "message": str(message) if message else "Human approval required",
        }
        reply = yield request
        approved = bool(reply.get("approved"))
        note = reply.get("note")
        self._emit(
            "step",
            {"step_id": step["id"], "type": "human_approval", "approved": approved, "note": note},
        )
        if not approved:
            suffix = f": {note}" if note else ""
            raise StepFailure(f"human_approval '{step['id']}' rejected{suffix}")
