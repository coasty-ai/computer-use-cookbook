"""Time sources for the mock server.

Determinism rule: nothing in the mock reads the wall clock directly. All
timestamps come from the injected :class:`Clock`, so tests can freeze time
(``FrozenClock``) while the standalone server may opt into ``SystemClock``.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Protocol

#: Default frozen epoch (2025-06-15T15:06:40Z). Matches ``t`` in the shared
#: HMAC test vectors from docs/API_NOTES.md.
DEFAULT_EPOCH = 1_750_000_000.0


class Clock(Protocol):
    """Anything that can tell the time and (optionally) be advanced."""

    def now(self) -> float: ...

    def advance(self, seconds: float) -> None: ...


class SystemClock:
    """Wall-clock time (used by the standalone server unless frozen)."""

    def now(self) -> float:
        return time.time()

    def advance(self, seconds: float) -> None:  # pragma: no cover - no-op
        """The wall clock cannot be advanced; transitions simply take time."""
        return None


class FrozenClock:
    """A deterministic clock that only moves when told to."""

    def __init__(self, epoch: float = DEFAULT_EPOCH) -> None:
        self._now = epoch

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds

    def set_to(self, epoch: float) -> None:
        self._now = epoch


def iso(ts: float) -> str:
    """Format an epoch as the ISO-8601 Zulu shape used across the API."""
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def period_of(ts: float) -> str:
    """``YYYY-MM`` billing period for an epoch timestamp."""
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m")
