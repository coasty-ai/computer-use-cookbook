"""Shared fixtures for the Python track (client tests + future examples).

Everything here is offline-safe: a fake sandbox key is pinned, the repo-root
.env is never read, and all HTTP goes through a respx router.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterator, Sequence
from typing import Any

import pytest
import respx

import coasty.env
from coasty import CoastyClient

FAKE_API_KEY = "sk-coasty-test-" + "0" * 48
BASE_URL = "https://coasty.ai/v1"


@pytest.fixture(autouse=True)
def coasty_env(monkeypatch: pytest.MonkeyPatch) -> str:
    """Pin a fake sandbox key and block any read of the real repo .env."""
    monkeypatch.setattr(coasty.env, "_dotenv_loaded", True)
    monkeypatch.setenv("COASTY_API_KEY", FAKE_API_KEY)
    monkeypatch.delenv("COASTY_BASE_URL", raising=False)
    monkeypatch.delenv("COASTY_CONFIRM_SPEND", raising=False)
    return FAKE_API_KEY


@pytest.fixture
def respx_router() -> Iterator[respx.MockRouter]:
    """A respx router intercepting all httpx traffic (offline guarantee)."""
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
def sleep_recorder() -> list[float]:
    """Recorded backoff sleeps (the client never really sleeps in tests)."""
    return []


@pytest.fixture
def client(coasty_env: str, sleep_recorder: list[float]) -> Iterator[CoastyClient]:
    """A CoastyClient with deterministic jitter and recorded (no-op) sleeps."""
    with CoastyClient(
        api_key=coasty_env,
        base_url=BASE_URL,
        sleep=sleep_recorder.append,
        rng=random.Random(42),
    ) as instance:
        yield instance


# ── payload factories (generic; examples tests reuse them) ────────────────

PayloadFactory = Callable[..., dict[str, Any]]


def _merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    base.update(overrides)
    return base


@pytest.fixture
def make_usage() -> PayloadFactory:
    def _make(**overrides: Any) -> dict[str, Any]:
        return _merge(
            {"input_tokens": 1523, "output_tokens": 245, "credits_charged": 5, "cost_cents": 5},
            overrides,
        )

    return _make


@pytest.fixture
def make_predict_response(make_usage: PayloadFactory) -> PayloadFactory:
    def _make(**overrides: Any) -> dict[str, Any]:
        return _merge(
            {
                "request_id": "req_test_1",
                "status": "continue",
                "reasoning": "Click the login button.",
                "actions": [
                    {
                        "action_type": "click",
                        "params": {"x": 512, "y": 340},
                        "description": "Click the login button",
                    }
                ],
                "raw_code": ["pyautogui.click(512, 340)"],
                "usage": make_usage(),
            },
            overrides,
        )

    return _make


@pytest.fixture
def make_run() -> PayloadFactory:
    def _make(**overrides: Any) -> dict[str, Any]:
        return _merge(
            {
                "id": "run_test_1",
                "object": "agent.run",
                "status": "queued",
                "machine_id": "mch_test_a1b2c3d4",
                "task": "Open the billing page",
                "cua_version": "v3",
                "instructions": None,
                "max_steps": 50,
                "on_awaiting_human": "pause",
                "steps_completed": 0,
                "credits_charged": 0,
                "cost_cents": 0,
                "result": None,
                "error": None,
                "awaiting_human_reason": None,
                "metadata": None,
                "webhook_url": None,
                "webhook_secret": None,
                "created_at": "2026-06-01T12:00:00Z",
                "started_at": None,
                "awaiting_human_since": None,
                "finished_at": None,
                "request_id": "req_test_run",
            },
            overrides,
        )

    return _make


@pytest.fixture
def make_workflow() -> PayloadFactory:
    def _make(**overrides: Any) -> dict[str, Any]:
        return _merge(
            {
                "id": "wf_test_1",
                "object": "workflow",
                "name": "Invoice reconciliation",
                "slug": "invoice-reconcile",
                "version": 1,
                "dsl_version": "2026-06-01",
                "definition": {"steps": []},
                "inputs_schema": None,
                "description": None,
                "status": "active",
                "metadata": None,
                "created_at": "2026-06-01T12:00:00Z",
                "updated_at": "2026-06-01T12:00:00Z",
                "request_id": "req_test_wf",
            },
            overrides,
        )

    return _make


@pytest.fixture
def make_workflow_run() -> PayloadFactory:
    def _make(**overrides: Any) -> dict[str, Any]:
        return _merge(
            {
                "id": "wfr_test_1",
                "object": "workflow.run",
                "status": "queued",
                "workflow_id": "wf_test_1",
                "workflow_version": 1,
                "machine_id": "mch_test_a1b2c3d4",
                "inputs": {},
                "output": None,
                "error": None,
                "awaiting_human_reason": None,
                "awaiting_step_id": None,
                "iterations_used": 0,
                "spent_cents": 0,
                "budget_cents": 0,
                "webhook_url": None,
                "webhook_secret": None,
                "metadata": None,
                "created_at": "2026-06-01T12:00:00Z",
                "started_at": None,
                "finished_at": None,
                "request_id": "req_test_wfr",
            },
            overrides,
        )

    return _make


@pytest.fixture
def make_machine() -> PayloadFactory:
    def _make(**overrides: Any) -> dict[str, Any]:
        return _merge(
            {
                "id": "mch_test_a1b2c3d4",
                "display_name": "invoice-bot",
                "status": "running",
                "os_type": "linux",
                "provider": "aws",
                "desktop_enabled": True,
                "cpu_cores": 2,
                "memory_gb": 4.0,
                "storage_gb": 20,
                "public_ip": "203.0.113.7",
                "is_test": True,
                "created_at": "2026-06-01T12:00:00Z",
                "metadata": {},
            },
            overrides,
        )

    return _make


@pytest.fixture
def make_provision_response(make_machine: PayloadFactory) -> PayloadFactory:
    def _make(**overrides: Any) -> dict[str, Any]:
        return _merge(
            {
                "machine": make_machine(),
                "connection": {
                    "public_ip": "203.0.113.7",
                    "ssh_port": 22,
                    "ssh_username": "ubuntu",
                    "vnc_port": 5900,
                    "websocket_port": 8080,
                    "has_ssh_key": True,
                    "has_vnc_password": True,
                },
                "request_id": "req_test_machine",
            },
            overrides,
        )

    return _make


@pytest.fixture
def make_error() -> PayloadFactory:
    """The documented error envelope; extras become context fields."""

    def _make(
        code: str = "VALIDATION_ERROR",
        message: str = "Something failed validation.",
        type: str = "validation_error",  # mirrors the wire field name
        request_id: str = "req_test_err",
        **extras: Any,
    ) -> dict[str, Any]:
        error: dict[str, Any] = {
            "code": code,
            "message": message,
            "type": type,
            "request_id": request_id,
        }
        error.update(extras)
        return {"error": error}

    return _make


@pytest.fixture
def sse_body() -> Callable[[Sequence[tuple[int, str, str]]], str]:
    """Build a raw SSE body from (seq, event, data) frames."""

    def _build(frames: Sequence[tuple[int, str, str]]) -> str:
        chunks = [f"id: {seq}\nevent: {event}\ndata: {data}\n\n" for seq, event, data in frames]
        return "".join(chunks)

    return _build
