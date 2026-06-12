"""Example 04 -- /parse: pyautogui source -> structured actions (FREE) + dry-run.

Purpose
    ``POST /v1/parse`` deterministically converts pyautogui source code into
    Coasty's structured action list -- no model call, no charge. This example
    parses a snippet, pretty-prints the structured actions, and round-trips
    them through the shared executor in DRY-RUN mode (``NullBackend`` records
    every backend call instead of moving the mouse), proving the actions are
    executable as-is.

Flow
    1. POST /v1/parse with ``{code}`` (pyautogui source, < 50k chars).
    2. Pretty-print the returned ``actions`` as JSON.
    3. Dry-run them through ``ActionExecutor(NullBackend())`` -- no scaling
       (parse output is already in your real screen's coordinate space) and
       ``raw`` actions are logged, never executed.

Endpoints used
    POST /v1/parse (scope ``parse``)

Estimated cost
    FREE -- /parse costs 0 credits, so there is no spend gate here. The $0
    estimate is still COMPUTED via :mod:`coasty.cost` and printed, keeping
    every example's output shape consistent.

Run it
    python examples/ex04_parse.py
    python examples/ex04_parse.py --code "pyautogui.click(10, 20)"
    python examples/ex04_parse.py --file my_script.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from coasty import CoastyClient, CoastyError, NullBackend, env
from coasty.cost import estimate_parse, format_estimate
from coasty.executor import ActionExecutor
from coasty.types import Action

DEFAULT_CODE = """\
pyautogui.click(640, 360)
pyautogui.write("hello coasty", interval=0.02)
pyautogui.press("enter")
pyautogui.scroll(-3)
"""


@dataclass(frozen=True)
class ParseOutcome:
    """Structured actions plus the dry-run trace."""

    actions: list[Action]
    executed: list[str]  # action_types the executor dispatched, in order
    backend_calls: list[tuple[str, dict[str, Any]]]  # what WOULD have run
    request_id: str | None


def parse_and_dry_run(
    client: CoastyClient,
    code: str,
    *,
    backend: NullBackend | None = None,
    emit: Callable[[str], None] = print,
) -> ParseOutcome:
    """The pure core: parse ``code`` and dry-run the actions on a NullBackend."""
    result = client.parse(code)
    actions = result.data["actions"]
    emit(json.dumps(actions, indent=2))  # pretty-printed structured actions

    # Round-trip: the parsed actions feed straight into the executor. The
    # NullBackend only records calls -- nothing touches the real mouse, and
    # no scaling is applied because parse output is already in real-screen
    # coordinates (it came from pyautogui source, not a sent screenshot).
    dry = backend if backend is not None else NullBackend()
    executor = ActionExecutor(dry)
    executed = executor.execute_all(actions)
    emit(f"dry-run dispatched {len(executed)} action(s): {executed}")
    for name, kwargs in dry.calls:
        emit(f"  would call backend.{name}({kwargs})")
    return ParseOutcome(
        actions=actions,
        executed=executed,
        backend_calls=list(dry.calls),
        request_id=result.request_id,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Parse pyautogui source into structured actions (free) and dry-run them."
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--code", help="pyautogui source to parse (default: a demo snippet)")
    source.add_argument("--file", type=Path, help="read the pyautogui source from this file")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.file is not None:
        try:
            code = args.file.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"error: cannot read {args.file}: {exc}", file=sys.stderr)
            return 2
    else:
        code = args.code if args.code is not None else DEFAULT_CODE

    try:
        api_key = env.require_api_key()
    except env.MissingAPIKeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # /parse is free -- print the computed $0 estimate; no spend gate needed.
    print(
        format_estimate(estimate_parse(), title="ex04 parse", sandbox=env.is_sandbox_key(api_key))
    )

    try:
        with CoastyClient(api_key) as client:
            parse_and_dry_run(client, code)
    except CoastyError as exc:
        print(f"error: {exc}", file=sys.stderr)  # str() includes the request_id
        if exc.request_id:
            print(f"request_id: {exc.request_id}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
