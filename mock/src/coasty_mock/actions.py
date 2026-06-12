"""Deterministic action synthesis for /v1/predict, sessions and /v1/ground.

Conventions (documented in mock/README.md):

- instruction containing ``[done]`` -> status "done"
- instruction containing ``[fail]`` -> status "fail"
- instruction containing "type"    -> a click + type_text pair
- instruction containing "scroll"  -> a scroll action
- anything else                    -> a single click at screen center
- the same (caller, instruction) pair returns "done" after N calls
  (default 3; configure with predict_done_after)
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from .state import TestState

JsonDict = dict[str, Any]

_QUOTED = re.compile(r"['\"]([^'\"]+)['\"]")


def raw_code_for(action: JsonDict) -> str:
    kind = action["action_type"]
    params = action["params"]
    if kind == "click":
        return f"pyautogui.click({params['x']}, {params['y']})"
    if kind == "type_text":
        return f"pyautogui.write({params['text']!r})"
    if kind == "key_press":
        return f"pyautogui.press({params['key']!r})"
    if kind == "key_combo":
        keys = ", ".join(repr(key) for key in params["keys"])
        return f"pyautogui.hotkey({keys})"
    if kind == "scroll":
        signed = params["amount"] if params["direction"] == "up" else -params["amount"]
        return f"pyautogui.scroll({signed})"
    if kind == "move":
        return f"pyautogui.moveTo({params['x']}, {params['y']})"
    if kind == "drag":
        return (
            f"pyautogui.moveTo({params['from_x']}, {params['from_y']})\n"
            f"pyautogui.dragTo({params['to_x']}, {params['to_y']})"
        )
    if kind == "wait":
        return f"time.sleep({params['ms'] / 1000})"
    if kind == "done":
        return "done()"
    if kind == "fail":
        return "fail()"
    return "# unsupported action"


def _with_description(action_type: str, params: JsonDict, description: str) -> JsonDict:
    action = {"action_type": action_type, "params": params, "description": description}
    action["raw_code"] = raw_code_for(action)
    return action


def synthesize_actions(
    state: TestState,
    *,
    counter_key: str,
    instruction: str,
    width: int,
    height: int,
    tools: list[str] | None,
    max_actions: int,
) -> tuple[str, list[JsonDict], str]:
    """Return (status, actions, reasoning), fully determined by the inputs."""
    center_x, center_y = width // 2, height // 2
    lowered = instruction.lower()

    if "[fail]" in lowered:
        actions = [_with_description("fail", {"reason": "Task marked [fail]"}, "Give up")]
        return "fail", actions, f"Mock failure forced by [fail] marker in: {instruction[:80]}"

    count_key = (counter_key, instruction)
    count = state.predict_counts.get(count_key, 0) + 1
    state.predict_counts[count_key] = count

    if "[done]" in lowered or count >= state.config.predict_done_after:
        actions = [_with_description("done", {}, "Task complete")]
        return "done", actions, f"Mock task complete for: {instruction[:80]}"

    actions = []
    if "type" in lowered:
        quoted = _QUOTED.search(instruction)
        text = quoted.group(1) if quoted else "hello world"
        actions = [
            _with_description("click", {"x": center_x, "y": center_y}, "Focus the field"),
            _with_description("type_text", {"text": text}, f"Type {text!r}"),
        ]
    elif "scroll" in lowered:
        direction = "up" if "up" in lowered.split("scroll", 1)[1][:16] else "down"
        actions = [
            _with_description(
                "scroll",
                {"x": center_x, "y": center_y, "direction": direction, "amount": 3},
                f"Scroll {direction}",
            )
        ]
    else:
        actions = [
            _with_description("click", {"x": center_x, "y": center_y}, "Click the screen center")
        ]

    if tools is not None:
        actions = [action for action in actions if action["action_type"] in tools]
        if not actions:
            fallback = "wait" if "wait" in tools else (tools[0] if tools else "wait")
            params: JsonDict = {"ms": 500} if fallback == "wait" else {}
            actions = [_with_description(fallback, params, "Fallback allowed action")]

    actions = actions[:max_actions]
    reasoning = f"Mock reasoning (call {count}) for: {instruction[:80]}"
    return "continue", actions, reasoning


def ground_point(element: str, width: int, height: int) -> tuple[int, int]:
    """Stable hash of the element string, always inside the screen bounds."""
    hx = int(hashlib.sha256(f"x:{element}".encode()).hexdigest(), 16)
    hy = int(hashlib.sha256(f"y:{element}".encode()).hexdigest(), 16)
    return hx % width, hy % height


def token_usage(instruction: str, actions: list[JsonDict]) -> tuple[int, int]:
    """Deterministic token counts for the usage block."""
    return 1000 + len(instruction), 100 + 25 * len(actions)
