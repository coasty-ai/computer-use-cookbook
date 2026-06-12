"""ex05: runs -- create w/ idempotency, poll to terminal, SSE reconnect, spend gate."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx

from coasty import CoastyClient
from ex05_runs import build_estimate, create_task_run, main, poll_run, stream_run_events

BASE_URL = "https://coasty.ai/v1"
RUN_ID = "run_test_1"
FAKE_LIVE_KEY = "sk-coasty-live-" + "0" * 48  # obviously fake


# ── create ───────────────────────────────────────────────────────────────────


def test_create_task_run_v4_sends_idempotency_key(
    client: CoastyClient, respx_router: respx.MockRouter, make_run: Any
) -> None:
    route = respx_router.post(f"{BASE_URL}/runs").mock(
        return_value=httpx.Response(201, json=make_run(cua_version="v4", max_steps=20))
    )
    run = create_task_run(
        client,
        machine_id="mch_test_a1b2c3d4",
        task="reconcile invoices",
        cua_version="v4",
        max_steps=20,
        idempotency_key="ex05-demo-1",
        emit=lambda _: None,
    )
    request = route.calls.last.request
    assert request.headers["Idempotency-Key"] == "ex05-demo-1"
    body = json.loads(request.content)
    assert body == {
        "machine_id": "mch_test_a1b2c3d4",
        "task": "reconcile invoices",
        "cua_version": "v4",
        "max_steps": 20,
        "on_awaiting_human": "pause",
    }
    assert run["id"] == RUN_ID


# ── polling ──────────────────────────────────────────────────────────────────


def test_poll_run_resumes_awaiting_human_and_reaches_terminal(
    client: CoastyClient, respx_router: respx.MockRouter, make_run: Any
) -> None:
    get_route = respx_router.get(f"{BASE_URL}/runs/{RUN_ID}").mock(
        side_effect=[
            httpx.Response(200, json=make_run(status="running", steps_completed=1)),
            httpx.Response(
                200,
                json=make_run(status="awaiting_human", awaiting_human_reason="captcha"),
            ),
            httpx.Response(200, json=make_run(status="running", steps_completed=3)),
            httpx.Response(
                200,
                json=make_run(
                    status="succeeded",
                    steps_completed=4,
                    credits_charged=20,
                    cost_cents=20,
                    result={"passed": True, "status": "succeeded", "summary": "all done"},
                    finished_at="2026-06-01T12:05:00Z",
                ),
            ),
        ]
    )
    resume_route = respx_router.post(f"{BASE_URL}/runs/{RUN_ID}/resume").mock(
        return_value=httpx.Response(200, json=make_run(status="running"))
    )
    sleeps: list[float] = []
    reasons: list[str | None] = []

    def handler(reason: str | None) -> str | None:
        reasons.append(reason)
        return "solved the captcha"

    final = poll_run(
        client,
        RUN_ID,
        on_awaiting_human=handler,
        poll_interval=0.01,
        sleep=sleeps.append,
        emit=lambda _: None,
    )

    assert final["status"] == "succeeded"
    assert final["cost_cents"] == 20
    assert final["result"] is not None and final["result"]["passed"] is True
    assert get_route.call_count == 4
    assert reasons == ["captcha"]
    assert resume_route.call_count == 1
    assert json.loads(resume_route.calls.last.request.content) == {"note": "solved the captcha"}
    assert sleeps == [0.01, 0.01, 0.01]  # injected -- no real waiting in tests


def test_poll_run_times_out_after_max_polls(
    client: CoastyClient, respx_router: respx.MockRouter, make_run: Any
) -> None:
    respx_router.get(f"{BASE_URL}/runs/{RUN_ID}").mock(
        return_value=httpx.Response(200, json=make_run(status="running"))
    )
    with pytest.raises(TimeoutError, match="not terminal"):
        poll_run(
            client,
            RUN_ID,
            on_awaiting_human=lambda _: None,
            sleep=lambda _: None,
            max_polls=3,
            emit=lambda _: None,
        )


# ── SSE streaming with Last-Event-ID reconnection ────────────────────────────


class _DroppingStream(httpx.SyncByteStream):
    """Yields some bytes then dies with a transport error (mid-stream drop)."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __iter__(self) -> Iterator[bytes]:
        yield self._payload
        raise httpx.ReadError("connection dropped mid-stream")


def test_stream_reconnects_with_last_event_id_and_resumes_run(
    client: CoastyClient,
    respx_router: respx.MockRouter,
    make_run: Any,
    sse_body: Any,
) -> None:
    first = sse_body(
        [
            (1, "status", '{"status":"running"}'),
            (2, "awaiting_human", '{"reason":"2fa code needed"}'),
        ]
    )
    second = sse_body(
        [
            (2, "awaiting_human", '{"reason":"2fa code needed"}'),  # replay: deduped
            (3, "resumed", "{}"),
            (4, "billing", '{"credits_charged":5,"total_cost_cents":10}'),
            (5, "status", '{"status":"succeeded"}'),
            (6, "done", "{}"),
        ]
    )
    events_route = respx_router.get(f"{BASE_URL}/runs/{RUN_ID}/events").mock(
        side_effect=[
            httpx.Response(
                200,
                stream=_DroppingStream(first.encode()),
                headers={"Content-Type": "text/event-stream"},
            ),
            httpx.Response(200, text=second, headers={"Content-Type": "text/event-stream"}),
        ]
    )
    resume_route = respx_router.post(f"{BASE_URL}/runs/{RUN_ID}/resume").mock(
        return_value=httpx.Response(200, json=make_run(status="running"))
    )
    final_route = respx_router.get(f"{BASE_URL}/runs/{RUN_ID}").mock(
        return_value=httpx.Response(
            200,
            json=make_run(status="succeeded", steps_completed=2, credits_charged=10, cost_cents=10),
        )
    )
    notes: list[str | None] = []

    def handler(reason: str | None) -> str | None:
        notes.append(reason)
        return "code entered"

    outcome = stream_run_events(client, RUN_ID, on_awaiting_human=handler, emit=lambda _: None)

    # reconnect carried the seq cursor: no Last-Event-ID on the first attempt,
    # then Last-Event-ID: 2 (the last event seen before the drop)
    assert events_route.call_count == 2
    assert "Last-Event-ID" not in events_route.calls[0].request.headers
    assert events_route.calls[1].request.headers["Last-Event-ID"] == "2"

    # awaiting_human triggered exactly one resume (the replayed copy was deduped)
    assert notes == ["2fa code needed"]
    assert resume_route.call_count == 1
    assert json.loads(resume_route.calls.last.request.content) == {"note": "code entered"}
    assert outcome.resumed is True

    # every event exactly once, in order, ending at done
    assert outcome.events_seen == [
        "status",
        "awaiting_human",
        "resumed",
        "billing",
        "status",
        "done",
    ]
    assert outcome.billing_events == [{"credits_charged": 5, "total_cost_cents": 10}]
    assert final_route.called
    assert outcome.final_run["status"] == "succeeded"
    assert outcome.final_run["cost_cents"] == 10  # authoritative cost


# ── spend gate (env-patched; never touches the network) ─────────────────────


def test_main_spend_gate_blocks_live_key_without_confirm(
    monkeypatch: pytest.MonkeyPatch,
    respx_router: respx.MockRouter,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("COASTY_API_KEY", FAKE_LIVE_KEY)
    monkeypatch.delenv("COASTY_CONFIRM_SPEND", raising=False)

    exit_code = main(["--machine-id", "mch_1", "--task", "do the thing", "--max-steps", "4"])

    assert exit_code == 2
    captured = capsys.readouterr()
    assert "run steps x4" in captured.out  # the itemized estimate was printed
    assert "--confirm" in captured.out
    assert len(respx_router.calls) == 0  # blocked BEFORE any HTTP request


def test_main_spend_gate_allows_with_env_opt_in_then_runs(
    monkeypatch: pytest.MonkeyPatch,
    respx_router: respx.MockRouter,
    make_run: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("COASTY_API_KEY", FAKE_LIVE_KEY)
    monkeypatch.setenv("COASTY_CONFIRM_SPEND", "1")
    respx_router.post(f"{BASE_URL}/runs").mock(
        return_value=httpx.Response(201, json=make_run(status="queued"))
    )
    respx_router.get(f"{BASE_URL}/runs/{RUN_ID}").mock(
        return_value=httpx.Response(
            200, json=make_run(status="succeeded", cost_cents=5, credits_charged=5)
        )
    )

    exit_code = main(
        ["--machine-id", "mch_1", "--task", "do it", "--auto-resume", "n/a", "--poll-interval", "0"]
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "final status: succeeded" in out
    assert "5 cents" in out  # cost from run.cost_cents


def test_build_estimate_v1_vs_v3() -> None:
    assert build_estimate(10, "v3").credits == 50  # 5 cr/step
    assert build_estimate(10, "v4").credits == 50  # v4 bills like v3
    assert build_estimate(10, "v1").credits == 80  # legacy engine: 8 cr/step
