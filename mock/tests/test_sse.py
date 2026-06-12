"""SSE framing, Last-Event-ID / ?after= replay, and the drop_after test hook."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from helpers import parse_sse

JsonDict = dict[str, Any]

EVENT_TYPES = {
    "status",
    "text",
    "reasoning",
    "tool_call",
    "tool_result",
    "awaiting_human",
    "resumed",
    "step",
    "billing",
    "error",
    "done",
}


def _create_run(client: TestClient, task: str = "stream me") -> str:
    response = client.post("/v1/runs", json={"machine_id": "m_1", "task": task})
    assert response.status_code == 200
    return str(response.json()["id"])


def test_framing_and_full_lifecycle(client: TestClient) -> None:
    run_id = _create_run(client)
    response = client.get(f"/v1/runs/{run_id}/events")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    # Raw framing: every block is id/event/data lines separated by blank lines.
    blocks = [b for b in response.text.split("\n\n") if b.strip()]
    for block in blocks:
        lines = block.split("\n")
        assert lines[0].startswith("id: ")
        assert lines[1].startswith("event: ")
        assert lines[2].startswith("data: ")

    events = parse_sse(response.text)
    assert [event["id"] for event in events] == list(range(1, len(events) + 1))
    assert {event["event"] for event in events} <= EVENT_TYPES
    assert events[0]["event"] == "status" and events[0]["data"] == {"status": "queued"}
    assert events[-1]["event"] == "done"
    assert events[-1]["data"]["status"] == "succeeded"
    statuses = [e["data"]["status"] for e in events if e["event"] == "status"]
    assert statuses == ["queued", "running", "succeeded"]
    billing = [e["data"]["credits_charged"] for e in events if e["event"] == "billing"]
    assert billing == [5, 10, 15]


def test_stream_is_deterministic_on_replay(client: TestClient) -> None:
    run_id = _create_run(client)
    first = client.get(f"/v1/runs/{run_id}/events").text
    second = client.get(f"/v1/runs/{run_id}/events").text
    assert first == second


def test_last_event_id_and_after_replay_without_loss_or_dup(client: TestClient) -> None:
    run_id = _create_run(client)
    full = parse_sse(client.get(f"/v1/runs/{run_id}/events").text)
    cut = 5
    via_header = parse_sse(
        client.get(f"/v1/runs/{run_id}/events", headers={"Last-Event-ID": str(cut)}).text
    )
    via_param = parse_sse(client.get(f"/v1/runs/{run_id}/events?after={cut}").text)
    assert via_header == via_param
    assert [event["id"] for event in via_header] == list(range(cut + 1, len(full) + 1))
    assert full[cut:] == via_header  # no loss, no duplication


def test_drop_after_closes_early_then_reconnect_resumes(client: TestClient) -> None:
    run_id = _create_run(client)
    partial = parse_sse(client.get(f"/v1/runs/{run_id}/events?drop_after=3").text)
    assert len(partial) == 3
    assert partial[-1]["event"] != "done"
    rest = parse_sse(
        client.get(
            f"/v1/runs/{run_id}/events", headers={"Last-Event-ID": str(partial[-1]["id"])}
        ).text
    )
    stitched = partial + rest
    full = parse_sse(client.get(f"/v1/runs/{run_id}/events").text)
    assert stitched == full


def test_paused_stream_ends_without_done_then_resumes(client: TestClient) -> None:
    run_id = _create_run(client, task="needs a human [pause]")
    first = parse_sse(client.get(f"/v1/runs/{run_id}/events").text)
    assert first[-1]["event"] == "awaiting_human"
    assert all(event["event"] != "done" for event in first)

    client.post(f"/v1/runs/{run_id}/resume", json={"note": "go on"})
    rest = parse_sse(
        client.get(
            f"/v1/runs/{run_id}/events", headers={"Last-Event-ID": str(first[-1]["id"])}
        ).text
    )
    assert rest[0]["event"] == "resumed"
    assert rest[0]["data"]["note"] == "go on"
    assert rest[-1]["event"] == "done"
    all_ids = [event["id"] for event in first + rest]
    assert all_ids == list(range(1, len(all_ids) + 1))


def test_workflow_run_events_replay(client: TestClient) -> None:
    definition = {
        "steps": [
            {"id": "t1", "type": "task", "task": "step one"},
            {"id": "t2", "type": "task", "task": "step two"},
        ]
    }
    run_id = client.post("/v1/workflows/runs", json={"definition": definition}).json()["id"]
    full = parse_sse(client.get(f"/v1/workflows/runs/{run_id}/events").text)
    assert full[-1]["event"] == "done"
    partial = parse_sse(client.get(f"/v1/workflows/runs/{run_id}/events?drop_after=2").text)
    rest = parse_sse(client.get(f"/v1/workflows/runs/{run_id}/events?after=2").text)
    assert partial + rest == full
