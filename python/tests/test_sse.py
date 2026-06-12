"""SSE framing + Last-Event-ID reconnection (parser units + client streams)."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from typing import Any

import httpx
import pytest
import respx

from coasty import CoastyClient
from coasty.sse import (
    SSEEvent,
    StreamInterruptedError,
    iter_events_reconnecting,
    parse_sse_lines,
)

BASE_URL = "https://coasty.ai/v1"
SSEBodyFactory = Callable[[Sequence[tuple[int, str, str]]], str]


# ── parser ──────────────────────────────────────────────────────────────────


def test_parse_basic_framing(sse_body: SSEBodyFactory) -> None:
    body = sse_body([(1, "status", '{"status":"running"}'), (2, "step", '{"step":1}')])
    events = list(parse_sse_lines(body.splitlines()))
    assert events == [
        SSEEvent(event="status", data='{"status":"running"}', id="1"),
        SSEEvent(event="step", data='{"step":1}', id="2"),
    ]
    assert events[0].seq == 1
    assert events[0].json() == {"status": "running"}


def test_parse_multiline_data_joined_with_newline() -> None:
    lines = ["id: 7", "event: text", "data: first line", "data: second line", ""]
    events = list(parse_sse_lines(lines))
    assert events == [SSEEvent(event="text", data="first line\nsecond line", id="7")]


def test_parse_ignores_comment_keepalives(sse_body: SSEBodyFactory) -> None:
    body = ": keepalive\n\n" + sse_body([(1, "status", "{}")]) + ": another comment\n\n"
    events = list(parse_sse_lines(body.splitlines()))
    assert [event.event for event in events] == ["status"]


def test_parse_strips_single_leading_space_only() -> None:
    events = list(parse_sse_lines(["data:  two spaces", ""]))
    assert events[0].data == " two spaces"


def test_parse_event_defaults_to_message() -> None:
    events = list(parse_sse_lines(["data: hello", ""]))
    assert events == [SSEEvent(event="message", data="hello", id=None)]


def test_parse_discards_partial_event_at_eof() -> None:
    lines = ["id: 1", "event: status", "data: {}", "", "id: 2", "event: step"]  # no blank
    events = list(parse_sse_lines(lines))
    assert [event.id for event in events] == ["1"]


def test_parse_non_numeric_id_has_no_seq() -> None:
    events = list(parse_sse_lines(["id: abc", "data: x", ""]))
    assert events[0].id == "abc"
    assert events[0].seq is None


# ── reconnecting iterator (unit, no HTTP) ───────────────────────────────────


def _lines_for(frames: Sequence[tuple[int, str, str]]) -> list[str]:
    lines: list[str] = []
    for seq, event, data in frames:
        lines.extend([f"id: {seq}", f"event: {event}", f"data: {data}", ""])
    return lines


def test_reconnect_resumes_with_cursor_no_loss_no_dup() -> None:
    cursors: list[str | None] = []

    def open_stream(cursor: str | None) -> Iterator[str]:
        cursors.append(cursor)
        if len(cursors) == 1:
            # drops mid-way after delivering 1 and 2
            yield from _lines_for([(1, "status", "{}"), (2, "step", "{}")])
            raise ConnectionError("dropped")
        # server replays at-and-after the cursor; 2 must be deduped
        yield from _lines_for([(2, "step", "{}"), (3, "step", "{}"), (4, "done", "{}")])

    events = list(iter_events_reconnecting(open_stream, reconnect_delay=0.0))
    assert cursors == [None, "2"]
    assert [event.id for event in events] == ["1", "2", "3", "4"]
    assert events[-1].event == "done"


def test_reconnect_after_clean_truncation_without_done() -> None:
    cursors: list[str | None] = []

    def open_stream(cursor: str | None) -> Iterator[str]:
        cursors.append(cursor)
        if len(cursors) == 1:
            return iter(_lines_for([(1, "status", "{}")]))  # ends with no done
        return iter(_lines_for([(2, "done", "{}")]))

    events = list(iter_events_reconnecting(open_stream, reconnect_delay=0.0))
    assert cursors == [None, "1"]
    assert [event.id for event in events] == ["1", "2"]


def test_initial_last_event_id_skips_replayed_events() -> None:
    def open_stream(cursor: str | None) -> Iterator[str]:
        assert cursor == "2"
        return iter(_lines_for([(2, "step", "{}"), (3, "done", "{}")]))

    events = list(iter_events_reconnecting(open_stream, last_event_id="2"))
    assert [event.id for event in events] == ["3"]


def test_gives_up_after_max_reconnects() -> None:
    def open_stream(cursor: str | None) -> Iterator[str]:
        return iter(_lines_for([(1, "status", "{}")]))  # never reaches done

    sleeps: list[float] = []
    with pytest.raises(StreamInterruptedError):
        list(
            iter_events_reconnecting(
                open_stream, max_reconnects=2, reconnect_delay=0.01, sleep=sleeps.append
            )
        )
    assert sleeps == [0.01, 0.01]


def test_stops_cleanly_after_done_even_if_stream_continues() -> None:
    def open_stream(cursor: str | None) -> Iterator[str]:
        return iter(_lines_for([(1, "done", "{}"), (2, "status", "{}")]))

    events = list(iter_events_reconnecting(open_stream))
    assert [event.event for event in events] == ["done"]


# ── client streams over (mocked) HTTP ───────────────────────────────────────


class _DroppingStream(httpx.SyncByteStream):
    """Yields some bytes then dies with a transport error (mid-stream drop)."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __iter__(self) -> Iterator[bytes]:
        yield self._payload
        raise httpx.ReadError("connection dropped mid-stream")


def test_run_events_reconnects_with_last_event_id(
    client: CoastyClient,
    respx_router: respx.MockRouter,
    sse_body: SSEBodyFactory,
) -> None:
    first = sse_body([(1, "status", '{"status":"running"}'), (2, "step", '{"step":1}')])
    second = sse_body(
        [
            (2, "step", '{"step":1}'),  # replay -- must be deduped
            (3, "status", '{"status":"succeeded"}'),
            (4, "done", "{}"),
        ]
    )
    route = respx_router.get(f"{BASE_URL}/runs/run_1/events").mock(
        side_effect=[
            httpx.Response(
                200,
                stream=_DroppingStream(first.encode()),
                headers={"Content-Type": "text/event-stream"},
            ),
            httpx.Response(200, text=second, headers={"Content-Type": "text/event-stream"}),
        ]
    )

    events = list(client.run_events("run_1", reconnect_delay=0.0))

    assert [event.id for event in events] == ["1", "2", "3", "4"]  # no loss, no dup
    assert route.call_count == 2
    first_request = route.calls[0].request
    second_request = route.calls[1].request
    assert "Last-Event-ID" not in first_request.headers
    assert second_request.headers["Last-Event-ID"] == "2"
    assert first_request.headers["Accept"] == "text/event-stream"
    assert first_request.headers["X-API-Key"].startswith("sk-coasty-test-")


def test_run_events_passes_initial_cursor(
    client: CoastyClient,
    respx_router: respx.MockRouter,
    sse_body: SSEBodyFactory,
) -> None:
    body = sse_body([(5, "step", "{}"), (6, "done", "{}")])
    route = respx_router.get(f"{BASE_URL}/runs/run_2/events").mock(
        return_value=httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})
    )
    events = list(client.run_events("run_2", last_event_id=4))
    assert route.calls.last.request.headers["Last-Event-ID"] == "4"
    assert [event.id for event in events] == ["5", "6"]


def test_workflow_run_events_stream(
    client: CoastyClient,
    respx_router: respx.MockRouter,
    sse_body: SSEBodyFactory,
) -> None:
    body = sse_body([(1, "status", '{"status":"running"}'), (2, "done", "{}")])
    respx_router.get(f"{BASE_URL}/workflows/runs/wfr_1/events").mock(
        return_value=httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})
    )
    events = list(client.workflow_run_events("wfr_1"))
    assert [event.event for event in events] == ["status", "done"]


def test_run_events_http_error_raises_typed_error(
    client: CoastyClient,
    respx_router: respx.MockRouter,
    make_error: Callable[..., dict[str, Any]],
) -> None:
    from coasty import NotFoundError

    respx_router.get(f"{BASE_URL}/runs/missing/events").mock(
        return_value=httpx.Response(
            404, json=make_error(code="RUN_NOT_FOUND", type="not_found_error")
        )
    )
    with pytest.raises(NotFoundError) as exc_info:
        list(client.run_events("missing", max_reconnects=0))
    assert exc_info.value.code == "RUN_NOT_FOUND"
    assert exc_info.value.request_id == "req_test_err"
