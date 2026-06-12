"""Durable per-run SSE event logs and wire framing.

Events are appended to an in-memory list per run; ``seq`` (1-based) is the
durable cursor. Framing follows docs/API_NOTES.md §SSE framing reference:

    id: 42
    event: status
    data: {"status":"running"}
    <blank line>

Replay: ``Last-Event-ID: <seq>`` header or ``?after=<seq>`` query param skips
everything at or before that seq — no loss, no duplication. The test hook
``?drop_after=<n>`` closes the stream after at most ``n`` events (so clients
can exercise reconnect logic deterministically).
"""

from __future__ import annotations

import json
from typing import Any

from .clock import Clock, iso

JsonDict = dict[str, Any]


def append_event(log: list[JsonDict], event_type: str, data: JsonDict, clock: Clock) -> JsonDict:
    """Append a durable event; seq is 1-based and strictly increasing."""
    event: JsonDict = {
        "seq": len(log) + 1,
        "type": event_type,
        "data": data,
        "created_at": iso(clock.now()),
    }
    log.append(event)
    return event


def frame(event: JsonDict) -> str:
    """Render one event in SSE wire format (id/event/data + blank line)."""
    data = json.dumps(event["data"], separators=(",", ":"))
    return f"id: {event['seq']}\nevent: {event['type']}\ndata: {data}\n\n"


def replay_frames(log: list[JsonDict], after: int, drop_after: int | None) -> str:
    """Frames for every event with seq > after, capped by drop_after."""
    events = [event for event in log if int(event["seq"]) > after]
    if drop_after is not None:
        events = events[:drop_after]
    return "".join(frame(event) for event in events)


def cursor_from(last_event_id: str | None, after_param: str | None) -> int:
    """Resolve the replay cursor; ?after= wins over Last-Event-ID."""
    raw = after_param if after_param is not None else last_event_id
    if raw is None:
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def parse_drop_after(raw: str | None) -> int | None:
    """The ?drop_after=<n> test hook; invalid or negative values are ignored."""
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None
