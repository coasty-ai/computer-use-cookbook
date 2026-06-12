"""Server-Sent Events: parser + reconnecting iterator with Last-Event-ID.

Framing (see API_NOTES "SSE framing reference"):

- UTF-8 lines, events separated by a blank line.
- Fields: ``id: <seq>``, ``event: <type>``, ``data: <json>``; multiple
  ``data:`` lines are joined with ``\\n``.
- Lines starting with ``:`` are keepalive comments and are ignored.
- The client tracks the last seen ``id`` and sends it as ``Last-Event-ID``
  on reconnect; events are durable server-side so the seq cursor guarantees
  no loss. Duplicates (replays at/below the cursor) are dropped client-side.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Any

import httpx

from .errors import CoastyError

DONE_EVENT = "done"


class StreamInterruptedError(CoastyError):
    """The event stream kept dropping before a terminal ``done`` event."""

    default_code = "STREAM_INTERRUPTED"
    default_type = "transport_error"


@dataclass(frozen=True)
class SSEEvent:
    """One parsed SSE event (``seq`` is the numeric form of ``id``)."""

    event: str
    data: str
    id: str | None = None

    @property
    def seq(self) -> int | None:
        if self.id is not None and self.id.isdigit():
            return int(self.id)
        return None

    def json(self) -> Any:
        """Decode the ``data`` payload as JSON."""
        return json.loads(self.data)


def parse_sse_lines(lines: Iterable[str]) -> Iterator[SSEEvent]:
    """Parse decoded SSE lines (no trailing newlines) into events.

    Multi-line ``data:`` is joined with newlines, ``:`` comments are ignored,
    and a partial event at EOF (no terminating blank line) is discarded, per
    the SSE spec -- the reconnect replay will redeliver it.
    """
    event_id: str | None = None
    event_type: str | None = None
    data_lines: list[str] = []
    pending = False

    for raw in lines:
        line = raw.rstrip("\r\n")
        if line == "":
            if pending:
                yield SSEEvent(
                    event=event_type or "message",
                    data="\n".join(data_lines),
                    id=event_id,
                )
            event_type = None
            data_lines = []
            pending = False
            continue
        if line.startswith(":"):
            continue  # comment / keepalive
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "id":
            event_id = value
            pending = True
        elif field == "event":
            event_type = value
            pending = True
        elif field == "data":
            data_lines.append(value)
            pending = True
        # unknown fields (incl. "retry") are ignored


def iter_events_reconnecting(
    open_stream: Callable[[str | None], Iterator[str]],
    *,
    last_event_id: str | None = None,
    done_event: str = DONE_EVENT,
    max_reconnects: int = 5,
    reconnect_delay: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[SSEEvent]:
    """Yield events, transparently reconnecting via ``Last-Event-ID``.

    ``open_stream(cursor)`` must open a fresh stream of decoded lines,
    sending ``cursor`` as the ``Last-Event-ID`` header when not ``None``.
    The iterator stops cleanly after ``done_event``; a stream that ends or
    drops earlier is reopened (up to ``max_reconnects`` times) and replayed
    events at or below the seq cursor are skipped, so callers see every
    event exactly once.
    """
    cursor = last_event_id
    last_seq = int(cursor) if cursor is not None and cursor.isdigit() else None
    reconnects = 0

    while True:
        try:
            for event in parse_sse_lines(open_stream(cursor)):
                if event.id is not None:
                    cursor = event.id
                seq = event.seq
                if seq is not None:
                    if last_seq is not None and seq <= last_seq:
                        continue  # duplicate from a replay
                    last_seq = seq
                yield event
                if event.event == done_event:
                    return
        except (httpx.TransportError, ConnectionError, TimeoutError):
            pass  # dropped mid-stream: fall through to reconnect

        reconnects += 1
        if reconnects > max_reconnects:
            raise StreamInterruptedError(
                f"event stream ended before a '{done_event}' event "
                f"after {max_reconnects} reconnect(s)",
            )
        if reconnect_delay > 0:
            sleep(reconnect_delay)
