"""Standalone entrypoint: ``python -m coasty_mock --port 8787``."""

from __future__ import annotations

import argparse

import uvicorn

from .app import create_app
from .clock import Clock, FrozenClock, SystemClock
from .state import TestState


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="coasty_mock",
        description="Fully offline mock of the Coasty Computer Use API.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8787, help="bind port (default 8787)")
    parser.add_argument("--seed", type=int, default=0, help="deterministic id seed (default 0)")
    parser.add_argument(
        "--frozen-clock",
        action="store_true",
        help="freeze time at the shared test epoch (default: wall clock)",
    )
    args = parser.parse_args(argv)

    clock: Clock = FrozenClock() if args.frozen_clock else SystemClock()
    state = TestState(seed=args.seed, clock=clock)
    app = create_app(state)
    print(f"coasty-mock listening on http://{args.host}:{args.port}/v1")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
