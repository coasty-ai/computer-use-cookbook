"""Workflow DSL builders + the validate() limit matrix."""

from __future__ import annotations

from typing import Any

import pytest

from coasty import dsl


def _task(step_id: str = "t1") -> dsl.Step:
    return dsl.task(step_id, "do the thing")


# ── builders produce the documented wire shapes ─────────────────────────────


def test_task_builder_full() -> None:
    step = dsl.task(
        "buy",
        "Buy {{inputs.item}}",
        machine_id="mch_test_1",
        cua_version="v4",
        instructions="be careful",
        system_prompt="prompt",
        max_steps=25,
        save_as="purchase",
        on_awaiting_human="pause",
    )
    assert step == {
        "id": "buy",
        "type": "task",
        "task": "Buy {{inputs.item}}",
        "machine_id": "mch_test_1",
        "cua_version": "v4",
        "instructions": "be careful",
        "system_prompt": "prompt",
        "max_steps": 25,
        "save_as": "purchase",
        "on_awaiting_human": "pause",
    }


def test_task_builder_omits_none_fields() -> None:
    assert _task() == {"id": "t1", "type": "task", "task": "do the thing"}


def test_assert_builder() -> None:
    step = dsl.assert_("check", dsl.eq("{{buy.status}}", "succeeded"), message="not bought")
    assert step == {
        "id": "check",
        "type": "assert",
        "condition": {"op": "eq", "left": "{{buy.status}}", "right": "succeeded"},
        "message": "not bought",
    }


def test_if_builder_with_else() -> None:
    step = dsl.if_("branch", dsl.truthy("{{vars.flag}}"), [_task("a")], else_=[_task("b")])
    assert step["type"] == "if"
    assert step["then"][0]["id"] == "a"
    assert step["else"][0]["id"] == "b"


def test_loop_builder_count_and_while_are_exclusive() -> None:
    counted = dsl.loop("l1", [_task()], count=3, max_iterations=10)
    assert counted == {
        "id": "l1",
        "type": "loop",
        "body": [_task()],
        "count": 3,
        "max_iterations": 10,
    }
    conditioned = dsl.loop("l2", [_task()], while_=dsl.falsy("{{vars.done}}"))
    assert conditioned["while"] == {"op": "falsy", "value": "{{vars.done}}"}
    with pytest.raises(ValueError, match="exactly one"):
        dsl.loop("l3", [_task()], count=3, while_=dsl.truthy(True))
    with pytest.raises(ValueError, match="exactly one"):
        dsl.loop("l4", [_task()])


def test_parallel_human_approval_retry_succeed_fail_builders() -> None:
    par = dsl.parallel("p", [[_task("a")], [_task("b")]])
    assert par == {"id": "p", "type": "parallel", "branches": [[_task("a")], [_task("b")]]}
    approval = dsl.human_approval("ok", message="approve?", timeout_seconds=600)
    assert approval == {
        "id": "ok",
        "type": "human_approval",
        "message": "approve?",
        "timeout_seconds": 600,
    }
    rty = dsl.retry("r", [_task()], max_attempts=3)
    assert rty == {"id": "r", "type": "retry", "body": [_task()], "max_attempts": 3}
    assert dsl.succeed("s", output={"x": 1}) == {"id": "s", "type": "succeed", "output": {"x": 1}}
    assert dsl.fail("f", message="nope") == {"id": "f", "type": "fail", "message": "nope"}


def test_definition_wrapper() -> None:
    built = dsl.definition([_task()], output={"answer": "{{t1.result}}"})
    assert built == {"steps": [_task()], "output": {"answer": "{{t1.result}}"}}


def test_all_13_condition_ops_covered() -> None:
    conditions = [
        dsl.eq(1, 1),
        dsl.ne(1, 2),
        dsl.lt(1, 2),
        dsl.gt(2, 1),
        dsl.lte(1, 1),
        dsl.gte(1, 1),
        dsl.contains("abc", "b"),
        dsl.truthy(True),
        dsl.falsy(False),
        dsl.exists("{{vars.x}}"),
        dsl.and_(dsl.eq(1, 1), dsl.eq(2, 2)),
        dsl.or_(dsl.eq(1, 1)),
        dsl.not_(dsl.falsy(0)),
    ]
    assert {condition["op"] for condition in conditions} == dsl.CONDITION_OPS
    assert len(dsl.CONDITION_OPS) == 13
    assert len(dsl.STEP_TYPES) == 9


# ── validate(): happy path ──────────────────────────────────────────────────


def test_validate_accepts_a_realistic_definition() -> None:
    built = dsl.definition(
        [
            dsl.task("login", "Log into the portal", save_as="login_result"),
            dsl.assert_("logged_in", dsl.eq("{{login_result.status}}", "succeeded")),
            dsl.if_(
                "maybe_retry",
                dsl.falsy("{{login_result.passed}}"),
                [dsl.retry("again", [dsl.task("relogin", "Try again")], max_attempts=2)],
                else_=[dsl.succeed("early", output={"ok": True})],
            ),
            dsl.loop("poll", [dsl.task("check", "Check inbox")], count=3),
            dsl.parallel("fanout", [[dsl.task("a", "A")], [dsl.task("b", "B")]]),
            dsl.human_approval("gate", message="Ship it?"),
            dsl.fail("bail", message="unreachable"),
        ]
    )
    dsl.validate(built)  # must not raise


# ── validate(): limit matrix ────────────────────────────────────────────────


def _problems(definition: dict[str, Any]) -> list[str]:
    with pytest.raises(dsl.DSLValidationError) as exc_info:
        dsl.validate(definition)
    return exc_info.value.problems


def test_validate_rejects_more_than_200_total_steps() -> None:
    steps = [dsl.task(f"t{i}", "x") for i in range(201)]
    problems = _problems(dsl.definition(steps))
    assert any("201 steps" in problem for problem in problems)
    dsl.validate(dsl.definition(steps[:200]))  # exactly 200 is fine


def test_validate_counts_nested_steps_toward_the_total() -> None:
    # 100 loops each containing 2 tasks = 300 total
    steps = [
        dsl.loop(f"l{i}", [dsl.task(f"a{i}", "x"), dsl.task(f"b{i}", "x")], count=1)
        for i in range(100)
    ]
    problems = _problems(dsl.definition(steps))
    assert any("300 steps" in problem for problem in problems)


def _nested_loops(levels: int) -> dsl.Step:
    step = _task("leaf")
    for index in range(levels):
        step = dsl.loop(f"level{index}", [step], count=1)
    return step


def test_validate_nesting_depth_limit_is_8() -> None:
    dsl.validate(dsl.definition([_nested_loops(7)]))  # depth 8: ok
    problems = _problems(dsl.definition([_nested_loops(8)]))  # depth 9: too deep
    assert any("nesting depth" in problem for problem in problems)


def test_validate_parallel_branch_limit_is_16() -> None:
    branches_16 = [[dsl.task(f"t{i}", "x")] for i in range(16)]
    dsl.validate(dsl.definition([dsl.parallel("p", branches_16)]))
    branches_17 = [[dsl.task(f"t{i}", "x")] for i in range(17)]
    problems = _problems(dsl.definition([dsl.parallel("p", branches_17)]))
    assert any("17 parallel branches" in problem for problem in problems)


@pytest.mark.parametrize("bad_attempts", [0, 21, -1, "3", True])
def test_validate_retry_attempts_must_be_1_to_20(bad_attempts: Any) -> None:
    step = {"id": "r", "type": "retry", "body": [_task()], "max_attempts": bad_attempts}
    problems = _problems({"steps": [step]})
    assert any("max_attempts" in problem for problem in problems)


def test_validate_retry_attempts_bounds_are_inclusive() -> None:
    dsl.validate({"steps": [dsl.retry("lo", [_task()], max_attempts=1)]})
    dsl.validate({"steps": [dsl.retry("hi", [_task()], max_attempts=20)]})


@pytest.mark.parametrize("forbidden", ["human_approval", "succeed", "fail"])
def test_validate_forbids_terminal_steps_inside_parallel(forbidden: str) -> None:
    inner: dsl.Step = {"id": "x", "type": forbidden}
    problems = _problems(dsl.definition([dsl.parallel("p", [[inner]])]))
    assert any("not allowed inside a parallel branch" in problem for problem in problems)


def test_validate_forbids_terminal_steps_nested_deep_in_parallel() -> None:
    nested = dsl.if_("inner", dsl.truthy(True), [dsl.succeed("s")])
    problems = _problems(dsl.definition([dsl.parallel("p", [[nested]])]))
    assert any("'succeed'" in problem for problem in problems)


@pytest.mark.parametrize("reserved", ["inputs", "vars"])
def test_validate_reserved_save_as(reserved: str) -> None:
    problems = _problems({"steps": [dsl.task("t", "x", save_as=reserved)]})
    assert any("reserved" in problem for problem in problems)


@pytest.mark.parametrize("bad_id", ["", "has space", "emoji✨", "x" * 65, "a/b"])
def test_validate_step_id_regex(bad_id: str) -> None:
    problems = _problems({"steps": [{"id": bad_id, "type": "succeed"}]})
    assert any("must match" in problem for problem in problems)


def test_validate_step_id_64_chars_ok() -> None:
    dsl.validate({"steps": [{"id": "x" * 64, "type": "succeed"}]})


def test_validate_unknown_step_type() -> None:
    problems = _problems({"steps": [{"id": "t", "type": "teleport"}]})
    assert any("unknown step type" in problem for problem in problems)


def test_validate_unknown_condition_op() -> None:
    problems = _problems({"steps": [{"id": "a", "type": "assert", "condition": {"op": "xor"}}]})
    assert any("unknown condition op" in problem for problem in problems)


def test_validate_condition_required_fields() -> None:
    problems = _problems(
        {
            "steps": [
                {"id": "a", "type": "assert", "condition": {"op": "eq", "left": 1}},
                {"id": "b", "type": "assert", "condition": {"op": "truthy"}},
                {"id": "c", "type": "assert", "condition": {"op": "and", "conditions": []}},
                {"id": "d", "type": "assert", "condition": {"op": "not"}},
            ]
        }
    )
    assert any("requires 'right'" in problem for problem in problems)
    assert any("requires 'value'" in problem for problem in problems)
    assert any("non-empty 'conditions'" in problem for problem in problems)
    assert any("requires 'condition'" in problem for problem in problems)


def test_validate_nested_combiner_conditions_recursively() -> None:
    bad = dsl.and_(dsl.eq(1, 1), {"op": "nope"})
    problems = _problems({"steps": [dsl.assert_("a", bad)]})
    assert any("conditions[1]" in problem for problem in problems)


def test_validate_loop_count_while_exclusivity_on_raw_dicts() -> None:
    both = {"id": "l", "type": "loop", "body": [], "count": 2, "while": dsl.truthy(1)}
    neither = {"id": "l2", "type": "loop", "body": []}
    problems = _problems({"steps": [both, neither]})
    assert sum("exactly one of 'count' or 'while'" in problem for problem in problems) == 2


def test_validate_task_requires_nonempty_task_text() -> None:
    problems = _problems({"steps": [{"id": "t", "type": "task", "task": ""}]})
    assert any("non-empty 'task'" in problem for problem in problems)


def test_validate_definition_requires_steps_key() -> None:
    problems = _problems({})
    assert problems == ["definition requires a 'steps' list"]


def test_validate_collects_multiple_problems() -> None:
    problems = _problems(
        {
            "steps": [
                {"id": "bad id", "type": "task", "task": ""},
                {"id": "ok", "type": "warp"},
            ]
        }
    )
    assert len(problems) >= 3  # bad id, empty task, unknown type
