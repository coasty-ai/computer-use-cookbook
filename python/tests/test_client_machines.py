"""Client contract tests: machines lifecycle + actions/terminal/files/browser."""

from __future__ import annotations

import json
from typing import Any

import httpx
import respx

from coasty import CoastyClient

BASE_URL = "https://coasty.ai/v1"
MID = "mch_test_a1b2c3d4"


def body_of(route: respx.Route) -> dict[str, Any]:
    return json.loads(route.calls.last.request.content)  # type: ignore[no-any-return]


def lifecycle(status: str) -> dict[str, Any]:
    return {
        "machine_id": MID,
        "status": status,
        "message": f"machine is now {status}",
        "request_id": "req_lc",
    }


def test_provision_machine_contract(
    client: CoastyClient, respx_router: respx.MockRouter, make_provision_response: Any
) -> None:
    route = respx_router.post(f"{BASE_URL}/machines").mock(
        return_value=httpx.Response(201, json=make_provision_response())
    )
    result = client.provision_machine(
        "invoice-bot",
        os_type="linux",
        desktop_enabled=True,
        cpu_cores=2,
        memory_gb=4,
        storage_gb=20,
        ttl_minutes=120,
        metadata={"team": "ops"},
        idempotency_key="prov-1",
    )
    assert route.calls.last.request.headers["Idempotency-Key"] == "prov-1"
    assert body_of(route) == {
        "display_name": "invoice-bot",
        "os_type": "linux",
        "desktop_enabled": True,
        "cpu_cores": 2,
        "memory_gb": 4,
        "storage_gb": 20,
        "ttl_minutes": 120,
        "metadata": {"team": "ops"},
    }
    assert result.data["machine"]["id"] == MID
    assert result.data["machine"]["is_test"] is True
    assert result.data["connection"]["ssh_port"] == 22


def test_list_machines_and_pricing(
    client: CoastyClient, respx_router: respx.MockRouter, make_machine: Any
) -> None:
    list_route = respx_router.get(f"{BASE_URL}/machines", params={"limit": "100"}).mock(
        return_value=httpx.Response(
            200, json={"machines": [make_machine()], "request_id": "req_lm"}
        )
    )
    respx_router.get(f"{BASE_URL}/machines/pricing").mock(
        return_value=httpx.Response(200, json={"pricing": {"linux_running_per_hour_credits": 5}})
    )
    listed = client.list_machines(limit=100)
    assert list_route.called
    assert listed.data["machines"][0]["id"] == MID
    pricing = client.machine_pricing()
    assert pricing.data["pricing"]["linux_running_per_hour_credits"] == 5


def test_get_machine(
    client: CoastyClient, respx_router: respx.MockRouter, make_machine: Any
) -> None:
    respx_router.get(f"{BASE_URL}/machines/{MID}").mock(
        return_value=httpx.Response(200, json={"machine": make_machine(), "request_id": "req_g"})
    )
    result = client.get_machine(MID)
    assert result.data["machine"]["status"] == "running"


def test_terminate_machine_uses_delete(
    client: CoastyClient, respx_router: respx.MockRouter
) -> None:
    route = respx_router.delete(f"{BASE_URL}/machines/{MID}").mock(
        return_value=httpx.Response(200, json=lifecycle("terminated"))
    )
    result = client.terminate_machine(MID)
    assert route.calls.last.request.method == "DELETE"
    assert result.data["status"] == "terminated"


def test_start_stop_restart(client: CoastyClient, respx_router: respx.MockRouter) -> None:
    for op, method in (
        ("start", client.start_machine),
        ("stop", client.stop_machine),
        ("restart", client.restart_machine),
    ):
        route = respx_router.post(f"{BASE_URL}/machines/{MID}/{op}").mock(
            return_value=httpx.Response(200, json=lifecycle(op))
        )
        result = method(MID)
        assert route.called, op
        assert result.data["machine_id"] == MID


def test_set_machine_ttl_patch(client: CoastyClient, respx_router: respx.MockRouter) -> None:
    route = respx_router.patch(f"{BASE_URL}/machines/{MID}").mock(
        return_value=httpx.Response(
            200, json={"machine_id": MID, "ttl_minutes": 0, "request_id": "req_ttl"}
        )
    )
    client.set_machine_ttl(MID, 0)  # 0 clears the TTL
    assert body_of(route) == {"ttl_minutes": 0}


def test_snapshot_machine_with_idempotency(
    client: CoastyClient, respx_router: respx.MockRouter
) -> None:
    route = respx_router.post(f"{BASE_URL}/machines/{MID}/snapshot").mock(
        return_value=httpx.Response(
            200,
            json={
                "machine_id": MID,
                "snapshot_id": "snap_1",
                "name": "pre-upgrade",
                "created_at": "2026-06-01T12:00:00Z",
                "credits_charged": 1,
                "request_id": "req_snap",
            },
            headers={"X-Credits-Charged": "1"},
        )
    )
    result = client.snapshot_machine(MID, idempotency_key="snap-1")
    assert route.calls.last.request.headers["Idempotency-Key"] == "snap-1"
    assert result.data["snapshot_id"] == "snap_1"
    assert result.credits_charged == 1


def test_machine_screenshot(client: CoastyClient, respx_router: respx.MockRouter) -> None:
    respx_router.get(f"{BASE_URL}/machines/{MID}/screenshot").mock(
        return_value=httpx.Response(
            200,
            json={
                "machine_id": MID,
                "image_b64": "iVBORw0KGgo" * 20,
                "mime_type": "image/png",
                "width": 1280,
                "height": 720,
                "captured_at": "2026-06-01T12:00:00Z",
                "request_id": "req_shot",
            },
        )
    )
    result = client.machine_screenshot(MID)
    assert result.data["width"] == 1280
    assert not result.data["image_b64"].startswith("data:")


def test_machine_action_contract(client: CoastyClient, respx_router: respx.MockRouter) -> None:
    route = respx_router.post(f"{BASE_URL}/machines/{MID}/actions").mock(
        return_value=httpx.Response(
            200,
            json={
                "machine_id": MID,
                "command": "click",
                "success": True,
                "result": {"clicked": True},
                "error": None,
                "duration_ms": 42,
                "request_id": "req_act",
            },
        )
    )
    result = client.machine_action(MID, "click", parameters={"x": 100, "y": 200}, timeout_ms=5000)
    assert body_of(route) == {
        "command": "click",
        "parameters": {"x": 100, "y": 200},
        "timeout_ms": 5000,
    }
    assert result.data["success"] is True


def test_machine_actions_batch_contract(
    client: CoastyClient, respx_router: respx.MockRouter
) -> None:
    route = respx_router.post(f"{BASE_URL}/machines/{MID}/actions/batch").mock(
        return_value=httpx.Response(
            200,
            json={
                "machine_id": MID,
                "results": [{"success": True}, {"success": True}],
                "completed_count": 2,
                "failed_count": 0,
                "aborted": False,
                "request_id": "req_batch",
            },
        )
    )
    steps = [
        {"command": "click", "parameters": {"x": 1, "y": 2}},
        {"command": "key_press", "parameters": {"key": "enter"}},
    ]
    result = client.machine_actions_batch(MID, steps, stop_on_error=False)
    assert body_of(route) == {"steps": steps, "stop_on_error": False}
    assert result.data["completed_count"] == 2


def test_machine_browser_op(client: CoastyClient, respx_router: respx.MockRouter) -> None:
    route = respx_router.post(f"{BASE_URL}/machines/{MID}/browser/navigate").mock(
        return_value=httpx.Response(
            200, json={"machine_id": MID, "success": True, "request_id": "req_nav"}
        )
    )
    client.machine_browser(
        MID, "navigate", parameters={"url": "https://example.com"}, timeout_ms=10000
    )
    assert body_of(route) == {
        "parameters": {"url": "https://example.com"},
        "timeout_ms": 10000,
    }


def test_machine_browser_defaults_to_empty_parameters(
    client: CoastyClient, respx_router: respx.MockRouter
) -> None:
    route = respx_router.post(f"{BASE_URL}/machines/{MID}/browser/state").mock(
        return_value=httpx.Response(200, json={"machine_id": MID, "request_id": "req_state"})
    )
    client.machine_browser(MID, "state")
    assert body_of(route) == {"parameters": {}}


def test_machine_terminal_contract(client: CoastyClient, respx_router: respx.MockRouter) -> None:
    route = respx_router.post(f"{BASE_URL}/machines/{MID}/terminal").mock(
        return_value=httpx.Response(
            200,
            json={
                "machine_id": MID,
                "output": "total 0",
                "exit_code": 0,
                "request_id": "req_term",
            },
        )
    )
    result = client.machine_terminal(MID, "ls -la", timeout_ms=30000, cwd="/home/ubuntu")
    assert body_of(route) == {"command": "ls -la", "timeout_ms": 30000, "cwd": "/home/ubuntu"}
    assert result.data["exit_code"] == 0


def test_machine_files_op(client: CoastyClient, respx_router: respx.MockRouter) -> None:
    route = respx_router.post(f"{BASE_URL}/machines/{MID}/files/read").mock(
        return_value=httpx.Response(
            200, json={"machine_id": MID, "content": "hello", "request_id": "req_read"}
        )
    )
    result = client.machine_files(MID, "read", {"path": "/tmp/hello.txt"})
    assert body_of(route) == {"parameters": {"path": "/tmp/hello.txt"}}
    assert result.data["content"] == "hello"


def test_machine_connection_secrets(client: CoastyClient, respx_router: respx.MockRouter) -> None:
    respx_router.get(f"{BASE_URL}/machines/{MID}/connection").mock(
        return_value=httpx.Response(
            200,
            json={
                "ssh_private_key_pem": "-----BEGIN OPENSSH PRIVATE KEY-----\nFAKE\n-----END",
                "vnc_password": "fake-vnc-pass",
                "websocket_url": "wss://example.invalid/ws",
                "devtools_url": "wss://example.invalid/devtools",
            },
            headers={"Cache-Control": "no-store"},
        )
    )
    result = client.machine_connection(MID)
    assert result.data["vnc_password"] == "fake-vnc-pass"
