"""A real (tiny) parser for pyautogui source -> documented action shapes.

Supported lines (one call per line; comments and unknown lines are skipped):

    pyautogui.click(x, y[, clicks=..., button=...])
    pyautogui.write("text") / pyautogui.typewrite("text")
    pyautogui.press("enter") / pyautogui.press(["a", "b"])
    pyautogui.hotkey("ctrl", "c")
    pyautogui.scroll(-3[, x=..., y=...])
    pyautogui.moveTo(x, y)
    pyautogui.dragTo(x, y)       # from the last tracked cursor position
    time.sleep(1.5) / pyautogui.sleep(1.5)

Output params use the canonical reference shapes from .llms.txt §6
(`click {x,y}`, `key_press {key}`, `key_combo {keys}`,
`scroll {x,y,direction,amount}`, `drag {from_x,from_y,to_x,to_y}`,
`wait {ms}`).
"""

from __future__ import annotations

import ast
import re
from typing import Any

JsonDict = dict[str, Any]

_CALL_RE = re.compile(r"^(?:pyautogui|pag|time)\.(\w+)\((.*)\)\s*(?:#.*)?$")


def _split_args(argstr: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    quote: str | None = None
    current = ""
    for ch in argstr:
        if quote is not None:
            current += ch
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
            current += ch
        elif ch in "([{":
            depth += 1
            current += ch
        elif ch in ")]}":
            depth -= 1
            current += ch
        elif ch == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += ch
    if current.strip():
        parts.append(current.strip())
    return parts


def _literal(token: str) -> Any:
    try:
        return ast.literal_eval(token)
    except (ValueError, SyntaxError):
        return token


def _parse_call_args(argstr: str) -> tuple[list[Any], dict[str, Any]]:
    args: list[Any] = []
    kwargs: dict[str, Any] = {}
    for token in _split_args(argstr):
        match = re.match(r"^([A-Za-z_]\w*)\s*=\s*(.+)$", token)
        if match:
            kwargs[match.group(1)] = _literal(match.group(2))
        else:
            args.append(_literal(token))
    return args, kwargs


def _action(action_type: str, params: JsonDict, description: str, raw: str) -> JsonDict:
    return {
        "action_type": action_type,
        "params": params,
        "description": description,
        "raw_code": raw,
    }


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def parse_pyautogui(code: str) -> list[JsonDict]:
    """Parse pyautogui source into the documented action list. Deterministic."""
    actions: list[JsonDict] = []
    cursor_x, cursor_y = 0, 0

    for raw_line in code.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _CALL_RE.match(line)
        if match is None:
            continue
        func = match.group(1)
        args, kwargs = _parse_call_args(match.group(2))

        if func == "click" and len(args) >= 2:
            x, y = _as_int(args[0]), _as_int(args[1])
            if x is None or y is None:
                continue
            params: JsonDict = {"x": x, "y": y}
            if "button" in kwargs:
                params["button"] = kwargs["button"]
            if "clicks" in kwargs:
                params["clicks"] = kwargs["clicks"]
            cursor_x, cursor_y = x, y
            actions.append(_action("click", params, f"Click at ({x}, {y})", line))
        elif func in {"write", "typewrite"} and args and isinstance(args[0], str):
            text = args[0]
            actions.append(_action("type_text", {"text": text}, f"Type {text!r}", line))
        elif func == "press" and args:
            keys = args[0] if isinstance(args[0], (list, tuple)) else [args[0]]
            for key in keys:
                if isinstance(key, str):
                    actions.append(_action("key_press", {"key": key}, f"Press {key!r}", line))
        elif func == "hotkey" and args:
            combo = [str(arg) for arg in args if isinstance(arg, str)]
            if combo:
                actions.append(
                    _action("key_combo", {"keys": combo}, f"Press {'+'.join(combo)}", line)
                )
        elif func == "scroll" and args:
            amount = _as_int(args[0])
            if amount is None:
                continue
            direction = "up" if amount > 0 else "down"
            x = _as_int(kwargs.get("x")) or cursor_x
            y = _as_int(kwargs.get("y")) or cursor_y
            actions.append(
                _action(
                    "scroll",
                    {"x": x, "y": y, "direction": direction, "amount": abs(amount)},
                    f"Scroll {direction} by {abs(amount)}",
                    line,
                )
            )
        elif func == "moveTo" and len(args) >= 2:
            x, y = _as_int(args[0]), _as_int(args[1])
            if x is None or y is None:
                continue
            cursor_x, cursor_y = x, y
            actions.append(_action("move", {"x": x, "y": y}, f"Move to ({x}, {y})", line))
        elif func == "dragTo" and len(args) >= 2:
            x, y = _as_int(args[0]), _as_int(args[1])
            if x is None or y is None:
                continue
            params = {"from_x": cursor_x, "from_y": cursor_y, "to_x": x, "to_y": y}
            actions.append(
                _action(
                    "drag",
                    params,
                    f"Drag from ({cursor_x}, {cursor_y}) to ({x}, {y})",
                    line,
                )
            )
            cursor_x, cursor_y = x, y
        elif func == "sleep" and args:
            seconds = args[0]
            if isinstance(seconds, (int, float)) and not isinstance(seconds, bool):
                actions.append(
                    _action("wait", {"ms": int(seconds * 1000)}, f"Wait {seconds}s", line)
                )

    return actions
