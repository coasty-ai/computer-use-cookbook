"""Deterministic, in-memory, seedable server state.

All ids derive from ``sha256(f"{seed}:{kind}:{counter}")`` so a given seed
always yields the same id sequence. ``POST /__mock__/reset`` swaps in a fresh
state; ``POST /__mock__/config`` tweaks knobs (wallet balance, webhook
delivery, frozen time, ...).
"""

from __future__ import annotations

import hashlib
from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Any

from .clock import Clock, FrozenClock, period_of

JsonDict = dict[str, Any]
WfGen = Generator[JsonDict, JsonDict, None]


@dataclass
class MockConfig:
    """Tunable knobs, all settable through ``POST /__mock__/config``."""

    wallet_balance_cents: int = 10_000
    deliver_webhooks: bool = True
    run_success_steps: int = 3
    predict_done_after: int = 3
    retry_after_seconds: int = 1
    session_ttl_seconds: int = 1800
    max_concurrent_sessions: int = 25
    run_step_seconds: int = 1
    workflow_task_step_seconds: int = 30
    latency_ms: int = 0  # reserved; the mock never sleeps in tests


@dataclass
class TestState:
    """The whole mock universe. One instance per app; reset in place."""

    seed: int = 0
    clock: Clock = field(default_factory=FrozenClock)
    config: MockConfig = field(default_factory=MockConfig)

    wallet_balance_cents: int = field(init=False)
    _counters: dict[str, int] = field(init=False)
    sessions: dict[str, JsonDict] = field(init=False)
    predict_counts: dict[tuple[str, str], int] = field(init=False)
    runs: dict[str, JsonDict] = field(init=False)
    run_events: dict[str, list[JsonDict]] = field(init=False)
    idempotency: dict[tuple[str, str, str], JsonDict] = field(init=False)
    workflows: dict[str, JsonDict] = field(init=False)
    workflow_slugs: dict[str, str] = field(init=False)
    workflow_runs: dict[str, JsonDict] = field(init=False)
    wf_events: dict[str, list[JsonDict]] = field(init=False)
    wf_gens: dict[str, WfGen] = field(init=False)
    machines: dict[str, JsonDict] = field(init=False)
    webhook_deliveries: list[JsonDict] = field(init=False)
    usage: dict[str, JsonDict] = field(init=False)

    def __post_init__(self) -> None:
        self._wipe()

    def _wipe(self) -> None:
        self.wallet_balance_cents = self.config.wallet_balance_cents
        self._counters = {}
        self.sessions = {}
        self.predict_counts = {}
        self.runs = {}
        self.run_events = {}
        self.idempotency = {}
        self.workflows = {}
        self.workflow_slugs = {}
        self.workflow_runs = {}
        self.wf_events = {}
        self.wf_gens = {}
        self.machines = {}
        self.webhook_deliveries = []
        self.usage = {}

    def reset(self, seed: int | None = None) -> None:
        """Restore a pristine state (fresh config, fresh frozen clock)."""
        if seed is not None:
            self.seed = seed
        self.config = MockConfig()
        if isinstance(self.clock, FrozenClock):
            self.clock = FrozenClock()
        self._wipe()

    # ------------------------------------------------------------------ ids
    def _hash(self, kind: str, n: int) -> str:
        return hashlib.sha256(f"{self.seed}:{kind}:{n}".encode()).hexdigest()

    def next_id(self, kind: str, prefix: str, length: int = 8) -> str:
        n = self._counters.get(kind, 0) + 1
        self._counters[kind] = n
        return f"{prefix}{self._hash(kind, n)[:length]}"

    def next_counter(self, kind: str) -> int:
        n = self._counters.get(kind, 0) + 1
        self._counters[kind] = n
        return n

    def next_request_id(self) -> str:
        return self.next_id("request", "req_", 12)

    def webhook_secret_for(self, resource_id: str) -> str:
        digest = hashlib.sha256(f"{self.seed}:whsec:{resource_id}".encode()).hexdigest()
        return f"whsec_{digest[:32]}"

    def deterministic_hex(self, salt: str, length: int = 12) -> str:
        return hashlib.sha256(f"{self.seed}:{salt}".encode()).hexdigest()[:length]

    # ---------------------------------------------------------------- usage
    def _period(self) -> JsonDict:
        period = period_of(self.clock.now())
        bucket = self.usage.get(period)
        if bucket is None:
            bucket = {"total_requests": 0, "total_credits": 0, "breakdown": {}}
            self.usage[period] = bucket
        return bucket

    def count_request(self) -> None:
        bucket = self._period()
        bucket["total_requests"] = int(bucket["total_requests"]) + 1

    def record_usage(self, endpoint: str, credits: int) -> None:
        bucket = self._period()
        breakdown: JsonDict = bucket["breakdown"]
        entry = breakdown.setdefault(endpoint, {"requests": 0, "credits": 0})
        entry["requests"] = int(entry["requests"]) + 1
        entry["credits"] = int(entry["credits"]) + credits
        bucket["total_credits"] = int(bucket["total_credits"]) + credits

    def usage_for(self, period: str) -> JsonDict:
        bucket = self.usage.get(period, {"total_requests": 0, "total_credits": 0, "breakdown": {}})
        return {
            "period": period,
            "total_requests": bucket["total_requests"],
            "total_credits": bucket["total_credits"],
            "total_cost_cents": bucket["total_credits"],
            "breakdown": bucket["breakdown"],
            "balance": self.wallet_balance_cents,
            "wallet_balance_cents": self.wallet_balance_cents,
            "wallet_balance_usd": self.wallet_balance_cents / 100.0,
        }
