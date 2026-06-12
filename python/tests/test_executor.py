"""ActionExecutor: BOTH documented param shapes, scaling, and 'raw' safety."""

from __future__ import annotations

import logging

import pytest

from coasty.executor import ActionExecutor, NullBackend, UnsupportedActionError


@pytest.fixture
def backend() -> NullBackend:
    return NullBackend()


@pytest.fixture
def executor(backend: NullBackend) -> ActionExecutor:
    return ActionExecutor(backend)


# ── click / move (canonical + local-automation shapes) ──────────────────────


def test_click_canonical_shape(executor: ActionExecutor, backend: NullBackend) -> None:
    executor.execute({"action_type": "click", "params": {"x": 512, "y": 340}})
    assert backend.calls == [("click", {"x": 512, "y": 340, "button": "left", "clicks": 1})]


def test_click_local_shape_with_button_and_clicks(
    executor: ActionExecutor, backend: NullBackend
) -> None:
    executor.execute(
        {"action_type": "click", "params": {"x": 10, "y": 20, "button": "right", "clicks": 2}}
    )
    assert backend.calls == [("click", {"x": 10, "y": 20, "button": "right", "clicks": 2})]


def test_move(executor: ActionExecutor, backend: NullBackend) -> None:
    executor.execute({"action_type": "move", "params": {"x": 5, "y": 6}})
    assert backend.calls == [("move", {"x": 5, "y": 6})]


def test_click_missing_coordinates_raises(executor: ActionExecutor) -> None:
    with pytest.raises(ValueError, match="missing a numeric"):
        executor.execute({"action_type": "click", "params": {"x": 5}})


# ── type_text ───────────────────────────────────────────────────────────────


def test_type_text(executor: ActionExecutor, backend: NullBackend) -> None:
    executor.execute({"action_type": "type_text", "params": {"text": "hello"}})
    assert backend.calls == [("type_text", {"text": "hello"})]


def test_type_text_requires_string(executor: ActionExecutor) -> None:
    with pytest.raises(ValueError, match="string 'text'"):
        executor.execute({"action_type": "type_text", "params": {"text": 42}})


# ── key_press / key_combo: {key} OR {keys} ──────────────────────────────────


def test_key_press_singular_key_shape(executor: ActionExecutor, backend: NullBackend) -> None:
    executor.execute({"action_type": "key_press", "params": {"key": "enter"}})
    assert backend.calls == [("key_press", {"keys": ["enter"]})]


def test_key_press_keys_list_shape(executor: ActionExecutor, backend: NullBackend) -> None:
    executor.execute({"action_type": "key_press", "params": {"keys": ["tab", "enter"]}})
    assert backend.calls == [("key_press", {"keys": ["tab", "enter"]})]


def test_key_press_keys_as_bare_string(executor: ActionExecutor, backend: NullBackend) -> None:
    executor.execute({"action_type": "key_press", "params": {"keys": "escape"}})
    assert backend.calls == [("key_press", {"keys": ["escape"]})]


def test_key_combo(executor: ActionExecutor, backend: NullBackend) -> None:
    executor.execute({"action_type": "key_combo", "params": {"keys": ["ctrl", "c"]}})
    assert backend.calls == [("key_combo", {"keys": ["ctrl", "c"]})]


def test_key_press_missing_keys_raises(executor: ActionExecutor) -> None:
    with pytest.raises(ValueError, match="'key' or 'keys'"):
        executor.execute({"action_type": "key_press", "params": {}})


# ── wait: {ms} OR {seconds} ─────────────────────────────────────────────────


def test_wait_ms_shape(executor: ActionExecutor, backend: NullBackend) -> None:
    executor.execute({"action_type": "wait", "params": {"ms": 1500}})
    assert backend.calls == [("wait", {"seconds": 1.5})]


def test_wait_seconds_shape(executor: ActionExecutor, backend: NullBackend) -> None:
    executor.execute({"action_type": "wait", "params": {"seconds": 2}})
    assert backend.calls == [("wait", {"seconds": 2.0})]


# ── scroll: {direction, amount} OR signed {clicks} ──────────────────────────


def test_scroll_direction_amount_shape(executor: ActionExecutor, backend: NullBackend) -> None:
    executor.execute(
        {"action_type": "scroll", "params": {"x": 100, "y": 200, "direction": "down", "amount": 3}}
    )
    assert backend.calls == [("scroll", {"amount": 3, "direction": "down", "x": 100, "y": 200})]


def test_scroll_positive_clicks_is_up(executor: ActionExecutor, backend: NullBackend) -> None:
    executor.execute({"action_type": "scroll", "params": {"clicks": 4}})
    assert backend.calls == [("scroll", {"amount": 4, "direction": "up", "x": None, "y": None})]


def test_scroll_negative_clicks_is_down(executor: ActionExecutor, backend: NullBackend) -> None:
    executor.execute({"action_type": "scroll", "params": {"clicks": -5}})
    assert backend.calls == [("scroll", {"amount": 5, "direction": "down", "x": None, "y": None})]


def test_scroll_direction_without_amount_defaults_to_1(
    executor: ActionExecutor, backend: NullBackend
) -> None:
    executor.execute({"action_type": "scroll", "params": {"direction": "left"}})
    assert backend.calls == [("scroll", {"amount": 1, "direction": "left", "x": None, "y": None})]


def test_scroll_unknown_direction_raises(executor: ActionExecutor) -> None:
    with pytest.raises(ValueError, match="unknown scroll direction"):
        executor.execute(
            {"action_type": "scroll", "params": {"direction": "sideways", "amount": 1}}
        )


def test_scroll_missing_both_shapes_raises(executor: ActionExecutor) -> None:
    with pytest.raises(ValueError, match="scroll requires"):
        executor.execute({"action_type": "scroll", "params": {}})


# ── drag: {from_x..} OR {x1..} ──────────────────────────────────────────────


def test_drag_canonical_shape(executor: ActionExecutor, backend: NullBackend) -> None:
    executor.execute(
        {
            "action_type": "drag",
            "params": {"from_x": 1, "from_y": 2, "to_x": 3, "to_y": 4},
        }
    )
    assert backend.calls == [("drag", {"x1": 1, "y1": 2, "x2": 3, "y2": 4, "button": "left"})]


def test_drag_local_shape(executor: ActionExecutor, backend: NullBackend) -> None:
    executor.execute({"action_type": "drag", "params": {"x1": 10, "y1": 20, "x2": 30, "y2": 40}})
    assert backend.calls == [("drag", {"x1": 10, "y1": 20, "x2": 30, "y2": 40, "button": "left"})]


# ── coordinate scaling ──────────────────────────────────────────────────────


def test_coordinates_scale_by_real_over_sent(backend: NullBackend) -> None:
    # screenshot sent at 1280x720, real screen 1920x1080 -> scale 1.5 both axes
    executor = ActionExecutor(backend, scale_x=1.5, scale_y=1.5)
    executor.execute({"action_type": "click", "params": {"x": 100, "y": 50}})
    executor.execute(
        {"action_type": "drag", "params": {"from_x": 2, "from_y": 2, "to_x": 10, "to_y": 12}}
    )
    executor.execute(
        {"action_type": "scroll", "params": {"x": 4, "y": 6, "direction": "down", "amount": 2}}
    )
    assert backend.calls[0] == ("click", {"x": 150, "y": 75, "button": "left", "clicks": 1})
    assert backend.calls[1] == ("drag", {"x1": 3, "y1": 3, "x2": 15, "y2": 18, "button": "left"})
    assert backend.calls[2] == ("scroll", {"amount": 2, "direction": "down", "x": 6, "y": 9})


def test_asymmetric_scaling(backend: NullBackend) -> None:
    executor = ActionExecutor(backend, scale_x=2.0, scale_y=0.5)
    executor.execute({"action_type": "move", "params": {"x": 7, "y": 8}})
    assert backend.calls == [("move", {"x": 14, "y": 4})]


# ── raw / terminal markers / dispatch errors ────────────────────────────────


def test_raw_is_never_executed_only_logged(
    executor: ActionExecutor, backend: NullBackend, caplog: pytest.LogCaptureFixture
) -> None:
    action = {"action_type": "raw", "params": {"code": "import os; os.system('rm -rf /')"}}
    with caplog.at_level(logging.WARNING, logger="coasty.executor"):
        handled = executor.execute(action)
    assert handled == "raw"
    assert backend.calls == []  # nothing reached the backend
    assert any("refusing to execute 'raw'" in record.message for record in caplog.records)


def test_done_and_fail_are_noops(executor: ActionExecutor, backend: NullBackend) -> None:
    assert executor.execute({"action_type": "done", "params": {}}) == "done"
    assert executor.execute({"action_type": "fail", "params": {"reason": "stuck"}}) == "fail"
    assert backend.calls == []


def test_unknown_action_type_raises(executor: ActionExecutor) -> None:
    with pytest.raises(UnsupportedActionError, match="unknown action_type"):
        executor.execute({"action_type": "levitate", "params": {}})


def test_missing_action_type_raises(executor: ActionExecutor) -> None:
    with pytest.raises(UnsupportedActionError, match="no string action_type"):
        executor.execute({"params": {"x": 1}})


def test_missing_params_treated_as_empty(executor: ActionExecutor, backend: NullBackend) -> None:
    assert executor.execute({"action_type": "done"}) == "done"
    assert backend.calls == []


# ── execute_all ─────────────────────────────────────────────────────────────


def test_execute_all_stops_after_done(executor: ActionExecutor, backend: NullBackend) -> None:
    handled = executor.execute_all(
        [
            {"action_type": "click", "params": {"x": 1, "y": 2}},
            {"action_type": "done", "params": {}},
            {"action_type": "click", "params": {"x": 9, "y": 9}},  # must not run
        ]
    )
    assert handled == ["click", "done"]
    assert len(backend.calls) == 1


def test_execute_all_stops_after_fail(executor: ActionExecutor, backend: NullBackend) -> None:
    handled = executor.execute_all(
        [
            {"action_type": "fail", "params": {"reason": "blocked"}},
            {"action_type": "click", "params": {"x": 9, "y": 9}},
        ]
    )
    assert handled == ["fail"]
    assert backend.calls == []
