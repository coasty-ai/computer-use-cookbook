"""Shared test constants and a tiny SSE wire-format parser."""

from __future__ import annotations

import base64
import json
from typing import Any

JsonDict = dict[str, Any]

# Obviously-fake keys (the mock accepts any sk-coasty-test-*/sk-coasty-live-*).
TEST_KEY = "sk-coasty-test-" + "0" * 48
LIVE_KEY = "sk-coasty-live-" + "0" * 48
LEGACY_KEY = "cua_sk_" + "0" * 48

#: Valid screenshot payload: raw base64, > 100 chars, no data: prefix.
SCREENSHOT = base64.b64encode(b"x" * 120).decode("ascii")


def parse_sse(text: str) -> list[JsonDict]:
    """Parse an SSE stream body into [{id, event, data}, ...]."""
    events: list[JsonDict] = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        event: JsonDict = {}
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith(":"):
                continue  # keepalive comment
            if line.startswith("id: "):
                event["id"] = int(line[len("id: ") :])
            elif line.startswith("event: "):
                event["event"] = line[len("event: ") :]
            elif line.startswith("data: "):
                data_lines.append(line[len("data: ") :])
        event["data"] = json.loads("\n".join(data_lines)) if data_lines else None
        events.append(event)
    return events


def auth(key: str) -> dict[str, str]:
    return {"X-API-Key": key}
