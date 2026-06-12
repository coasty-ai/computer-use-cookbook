"""Defensive local action executor.

The docs publish TWO param shapes for actions (see API_NOTES "Action types --
IMPORTANT discrepancy"). This executor accepts BOTH:

- ``key_press``: ``{key}`` or ``{keys: [...]}``
- ``wait``: ``{ms}`` or ``{seconds}``
- ``scroll``: ``{direction, amount}`` or signed ``{clicks}`` (+up / -down)
- ``drag``: ``{from_x, from_y, to_x, to_y}`` or ``{x1, y1, x2, y2}``

Coordinates are multiplied by ``scale_x`` / ``scale_y`` (real / sent size)
before reaching the backend. ``raw`` actions are NEVER executed -- they are
logged and skipped. ``execute`` returns the action_type it handled.
"""

from __future__ import annotations

import importlib
import logging
import time
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal, Protocol, cast

ScrollDirection = Literal["up", "down", "left", "right"]
_SCROLL_DIRECTIONS = frozenset({"up", "down", "left", "right"})

logger = logging.getLogger("coasty.executor")


class UnsupportedActionError(ValueError):
    """An action_type the executor does not know how to dispatch."""


class ActionBackend(Protocol):
    """What a local execution target must implement (pyautogui, Playwright...)."""

    def click(self, x: int, y: int, *, button: str = "left", clicks: int = 1) -> None: ...

    def type_text(self, text: str) -> None: ...

    def key_press(self, keys: Sequence[str]) -> None: ...

    def key_combo(self, keys: Sequence[str]) -> None: ...

    def scroll(
        self,
        amount: int,
        *,
        direction: ScrollDirection = "down",
        x: int | None = None,
        y: int | None = None,
    ) -> None: ...

    def drag(self, x1: int, y1: int, x2: int, y2: int, *, button: str = "left") -> None: ...

    def move(self, x: int, y: int) -> None: ...

    def wait(self, seconds: float) -> None: ...


class NullBackend:
    """Records every backend call; for tests and dry runs."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def click(self, x: int, y: int, *, button: str = "left", clicks: int = 1) -> None:
        self.calls.append(("click", {"x": x, "y": y, "button": button, "clicks": clicks}))

    def type_text(self, text: str) -> None:
        self.calls.append(("type_text", {"text": text}))

    def key_press(self, keys: Sequence[str]) -> None:
        self.calls.append(("key_press", {"keys": list(keys)}))

    def key_combo(self, keys: Sequence[str]) -> None:
        self.calls.append(("key_combo", {"keys": list(keys)}))

    def scroll(
        self,
        amount: int,
        *,
        direction: ScrollDirection = "down",
        x: int | None = None,
        y: int | None = None,
    ) -> None:
        self.calls.append(("scroll", {"amount": amount, "direction": direction, "x": x, "y": y}))

    def drag(self, x1: int, y1: int, x2: int, y2: int, *, button: str = "left") -> None:
        self.calls.append(("drag", {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "button": button}))

    def move(self, x: int, y: int) -> None:
        self.calls.append(("move", {"x": x, "y": y}))

    def wait(self, seconds: float) -> None:
        self.calls.append(("wait", {"seconds": seconds}))


class PyAutoGuiBackend:
    """Drives the real mouse/keyboard via pyautogui (the ``[local]`` extra).

    The import is lazy so CI (which never installs pyautogui) can import this
    module freely; constructing the backend without pyautogui installed
    raises a helpful ``RuntimeError``.
    """

    def __init__(
        self,
        *,
        type_interval: float = 0.02,
        drag_duration: float = 0.3,
        failsafe: bool = True,
    ) -> None:
        try:
            self._pg: Any = cast(Any, importlib.import_module("pyautogui"))
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise RuntimeError(
                "pyautogui is not installed. Install the local-automation extra: "
                'pip install "coasty[local]"'
            ) from exc
        self._pg.FAILSAFE = failsafe  # mouse to a screen corner aborts instantly
        self._type_interval = type_interval
        self._drag_duration = drag_duration

    def click(self, x: int, y: int, *, button: str = "left", clicks: int = 1) -> None:
        self._pg.click(x, y, clicks=clicks, button=button)

    def type_text(self, text: str) -> None:
        self._pg.write(text, interval=self._type_interval)

    def key_press(self, keys: Sequence[str]) -> None:
        for key in keys:
            self._pg.press(key)

    def key_combo(self, keys: Sequence[str]) -> None:
        self._pg.hotkey(*keys)

    def scroll(
        self,
        amount: int,
        *,
        direction: ScrollDirection = "down",
        x: int | None = None,
        y: int | None = None,
    ) -> None:
        if direction in ("up", "down"):
            self._pg.scroll(amount if direction == "up" else -amount, x=x, y=y)
        else:
            self._pg.hscroll(amount if direction == "right" else -amount, x=x, y=y)

    def drag(self, x1: int, y1: int, x2: int, y2: int, *, button: str = "left") -> None:
        self._pg.moveTo(x1, y1)
        self._pg.dragTo(x2, y2, duration=self._drag_duration, button=button)

    def move(self, x: int, y: int) -> None:
        self._pg.moveTo(x, y)

    def wait(self, seconds: float) -> None:
        time.sleep(seconds)


def _require_number(params: Mapping[str, Any], *names: str) -> float:
    """First numeric param among ``names`` (aliases), else ValueError."""
    for name in names:
        value = params.get(name)
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float):
            return float(value)
    raise ValueError(f"action params missing a numeric {' / '.join(names)!s} field: {params!r}")


def _optional_number(params: Mapping[str, Any], name: str) -> float | None:
    value = params.get(name)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _keys_list(params: Mapping[str, Any]) -> list[str]:
    """Normalize ``{key: "enter"}`` / ``{keys: [...]}`` / ``{keys: "enter"}``."""
    if "keys" in params:
        keys = params["keys"]
        if isinstance(keys, str):
            return [keys]
        if isinstance(keys, Sequence):
            return [str(key) for key in keys]
        raise ValueError(f"'keys' must be a string or list, got {type(keys).__name__}")
    if "key" in params:
        return [str(params["key"])]
    raise ValueError(f"action params missing 'key' or 'keys': {params!r}")


class ActionExecutor:
    """Dispatch Coasty actions onto an :class:`ActionBackend`, defensively."""

    def __init__(
        self,
        backend: ActionBackend,
        *,
        scale_x: float = 1.0,
        scale_y: float = 1.0,
        log: logging.Logger | None = None,
    ) -> None:
        self.backend = backend
        self.scale_x = scale_x
        self.scale_y = scale_y
        self._log = log or logger

    def _sx(self, value: float) -> int:
        return round(value * self.scale_x)

    def _sy(self, value: float) -> int:
        return round(value * self.scale_y)

    def execute(self, action: Mapping[str, Any]) -> str:
        """Execute one action; returns the ``action_type`` handled.

        ``done`` / ``fail`` are no-ops (loop control is the caller's job) and
        ``raw`` is logged and skipped -- pyautogui source is never executed.
        """
        action_type = action.get("action_type")
        if not isinstance(action_type, str):
            raise UnsupportedActionError(f"action has no string action_type: {action!r}")
        params_raw = action.get("params")
        params: Mapping[str, Any] = params_raw if isinstance(params_raw, Mapping) else {}

        if action_type == "click":
            clicks_value = params.get("clicks")
            self.backend.click(
                self._sx(_require_number(params, "x")),
                self._sy(_require_number(params, "y")),
                button=str(params.get("button", "left")),
                clicks=int(clicks_value) if isinstance(clicks_value, int) else 1,
            )
        elif action_type == "type_text":
            text = params.get("text")
            if not isinstance(text, str):
                raise ValueError(f"type_text requires a string 'text' param: {params!r}")
            self.backend.type_text(text)
        elif action_type == "key_press":
            self.backend.key_press(_keys_list(params))
        elif action_type == "key_combo":
            self.backend.key_combo(_keys_list(params))
        elif action_type == "scroll":
            self._dispatch_scroll(params)
        elif action_type == "drag":
            self.backend.drag(
                self._sx(_require_number(params, "from_x", "x1")),
                self._sy(_require_number(params, "from_y", "y1")),
                self._sx(_require_number(params, "to_x", "x2")),
                self._sy(_require_number(params, "to_y", "y2")),
                button=str(params.get("button", "left")),
            )
        elif action_type == "move":
            self.backend.move(
                self._sx(_require_number(params, "x")),
                self._sy(_require_number(params, "y")),
            )
        elif action_type == "wait":
            if "ms" in params:
                seconds = _require_number(params, "ms") / 1000.0
            else:
                seconds = _require_number(params, "seconds")
            self.backend.wait(seconds)
        elif action_type in ("done", "fail"):
            pass  # terminal markers: nothing to execute
        elif action_type == "raw":
            code = params.get("code")
            preview = repr(code)[:120] if code is not None else "<none>"
            self._log.warning(
                "refusing to execute 'raw' action (never executed by default); code=%s",
                preview,
            )
        else:
            raise UnsupportedActionError(f"unknown action_type: {action_type!r}")
        return action_type

    def _dispatch_scroll(self, params: Mapping[str, Any]) -> None:
        x = _optional_number(params, "x")
        y = _optional_number(params, "y")
        if "clicks" in params:
            clicks = int(_require_number(params, "clicks"))
            direction: ScrollDirection = "up" if clicks >= 0 else "down"
            amount = abs(clicks)
        elif "direction" in params or "amount" in params:
            raw_direction = str(params.get("direction", "down"))
            if raw_direction not in _SCROLL_DIRECTIONS:
                raise ValueError(f"unknown scroll direction: {raw_direction!r}")
            # mypy: narrowed by the membership check above
            direction = raw_direction  # type: ignore[assignment]
            amount = int(_require_number(params, "amount")) if "amount" in params else 1
        else:
            raise ValueError(f"scroll requires 'direction'+'amount' or signed 'clicks': {params!r}")
        self.backend.scroll(
            amount,
            direction=direction,
            x=self._sx(x) if x is not None else None,
            y=self._sy(y) if y is not None else None,
        )

    def execute_all(self, actions: Iterable[Mapping[str, Any]]) -> list[str]:
        """Execute actions in order; stops after a ``done`` or ``fail``."""
        handled: list[str] = []
        for action in actions:
            handled.append(self.execute(action))
            if handled[-1] in ("done", "fail"):
                break
        return handled
