"""Machines: sandbox semantics, lifecycle, actions, terminal, files, browser.

Mock conventions (documented in mock/README.md):

- Provisioning is INSTANT for every key kind; test keys get ``mch_test_<hex>``
  ids, live keys get UUID-shaped ids (and must pass the 20-credit wallet gate).
- Machines are mode-isolated: a test key never sees live machines and vice
  versa (otherwise 404 MACHINE_NOT_FOUND).
- The screenshot is a real, decodable PNG (see png.py) whose base64 is long
  enough to feed straight back into /v1/predict.
- The terminal echoes the command; files live in an in-memory per-machine
  dict; browser ops return deterministic stub results.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from .clock import iso
from .deps import (
    charge,
    check_idempotency,
    json_body,
    mock_state,
    mode_of,
    request_id,
    store_idempotent,
)
from .errors import ApiError
from .png import SCREENSHOT_HEIGHT, SCREENSHOT_WIDTH, screenshot_b64
from .pricing import MACHINE_HOURLY, MACHINE_PROVISION_GATE, SNAPSHOT
from .state import TestState
from .validation import Validator, field_bool, field_dict, field_int, field_str, parse_limit

JsonDict = dict[str, Any]

router = APIRouter(prefix="/v1")

BROWSER_OPS = {
    "open",
    "navigate",
    "click",
    "type",
    "dom",
    "clickables",
    "state",
    "info",
    "scroll",
    "close",
    "screenshot",
    "wait",
    "list-tabs",
    "open-tab",
    "close-tab",
    "switch-tab",
}
FILE_READ_OPS = {"read", "exists", "list", "list-directory", "download", "list-downloads"}
FILE_WRITE_OPS = {"write", "edit", "append", "delete", "delete-directory"}
MAX_BATCH_STEPS = 50


def _get_machine(state: TestState, machine_id: str, mode: str) -> JsonDict:
    machine = state.machines.get(machine_id)
    if machine is None or machine["_mode"] != mode:
        raise ApiError("MACHINE_NOT_FOUND", f"No machine {machine_id!r} for this key.")
    return machine


def _require_state(machine: JsonDict, allowed: list[str], action: str) -> None:
    if machine["status"] not in allowed:
        raise ApiError(
            "INVALID_STATE",
            f"Cannot {action} machine {machine['id']!r} while it is {machine['status']!r}.",
            extras={"current_state": machine["status"], "allowed_from": allowed},
        )


def machine_public(machine: JsonDict) -> JsonDict:
    return {k: v for k, v in machine.items() if not k.startswith("_")}


def _duration_ms(command: str) -> int:
    return 20 + int(hashlib.sha256(command.encode()).hexdigest(), 16) % 80


def _machine_id_for(state: TestState, mode: str) -> str:
    if mode == "test":
        return "mch_test_" + state.deterministic_hex(f"machine:{state.next_counter('machine')}", 12)
    raw = state.deterministic_hex(f"machine:{state.next_counter('machine')}", 32)
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:32]}"


def _connection_summary(machine: JsonDict) -> JsonDict:
    return {
        "public_ip": machine["public_ip"],
        "ssh_port": 22,
        "ssh_username": "ubuntu" if machine["os_type"] == "linux" else "Administrator",
        "vnc_port": 5900,
        "websocket_port": 8080,
        "has_ssh_key": True,
        "has_vnc_password": True,
    }


@router.post("/machines")
async def provision_machine(request: Request) -> JsonDict:
    state = mock_state(request)
    body = await json_body(request)
    vd = Validator()
    display_name = field_str(body, "display_name", vd, required=True, min_len=1, max_len=64)
    os_type = field_str(body, "os_type", vd, default="linux", choices={"linux", "windows"})
    desktop_enabled = field_bool(body, "desktop_enabled", vd, default=False)
    provider = field_str(body, "provider", vd, default="auto", choices={"auto", "aws", "azure"})
    cpu_cores = field_int(body, "cpu_cores", vd, lo=1, hi=16)
    memory_gb = field_int(body, "memory_gb", vd, lo=1, hi=64)
    storage_gb = field_int(body, "storage_gb", vd, lo=8, hi=500)
    field_bool(body, "restore_from_snapshot", vd, default=False)
    ttl_minutes = field_int(body, "ttl_minutes", vd, lo=5, hi=10080)
    metadata = field_dict(body, "metadata", vd, max_keys=16)
    vd.raise_if_any()
    assert display_name is not None and os_type is not None

    mode = mode_of(request)
    if mode != "test" and state.wallet_balance_cents < MACHINE_PROVISION_GATE:
        raise ApiError(
            "INSUFFICIENT_CREDITS",
            f"Provisioning requires a wallet balance of at least {MACHINE_PROVISION_GATE} "
            "credits (a pre-flight gate, not a fee).",
            extras={"required": MACHINE_PROVISION_GATE, "balance": state.wallet_balance_cents},
        )

    cache_key, cached = check_idempotency(request, body, "machines")
    if cached is not None:
        return cached

    machine_id = _machine_id_for(state, mode)
    octet = 1 + int(state.deterministic_hex(f"ip:{machine_id}", 2), 16) % 254
    machine: JsonDict = {
        "id": machine_id,
        "display_name": display_name,
        "status": "running",
        "os_type": os_type,
        "provider": "aws" if provider == "auto" else provider,
        "desktop_enabled": desktop_enabled,
        "cpu_cores": cpu_cores if cpu_cores is not None else 2,
        "memory_gb": float(memory_gb) if memory_gb is not None else 4.0,
        "storage_gb": storage_gb if storage_gb is not None else 20,
        "public_ip": f"203.0.113.{octet}",
        "is_test": mode == "test",
        "ttl_minutes": ttl_minutes,
        "created_at": iso(state.clock.now()),
        "metadata": metadata or {},
        "_mode": mode,
        "_files": {},
    }
    state.machines[machine_id] = machine
    response = {
        "machine": machine_public(machine),
        "connection": _connection_summary(machine),
        "request_id": request_id(request),
    }
    store_idempotent(request, cache_key, body, response)
    return response


@router.get("/machines")
def list_machines(request: Request) -> JsonDict:
    state = mock_state(request)
    limit = parse_limit(request.query_params.get("limit"), default=50)
    mode = mode_of(request)
    machines = [m for m in state.machines.values() if m["_mode"] == mode]
    return {
        "object": "list",
        "data": [machine_public(m) for m in machines[:limit]],
        "has_more": len(machines) > limit,
        "request_id": request_id(request),
    }


@router.get("/machines/pricing")
def machine_pricing(request: Request) -> JsonDict:
    return {
        "object": "machine.pricing",
        "unit": "credits_per_hour",
        "rates": dict(MACHINE_HOURLY),
        "snapshot_credits": SNAPSHOT,
        "provision_gate_credits": MACHINE_PROVISION_GATE,
        "note": "1 credit = 1 cent = $0.01; runtime metered per minute, rounded down.",
        "request_id": request_id(request),
    }


@router.get("/machines/{machine_id}")
def get_machine(request: Request, machine_id: str) -> JsonDict:
    state = mock_state(request)
    machine = _get_machine(state, machine_id, mode_of(request))
    return {"machine": machine_public(machine), "request_id": request_id(request)}


@router.delete("/machines/{machine_id}")
def terminate_machine(request: Request, machine_id: str) -> JsonDict:
    state = mock_state(request)
    machine = _get_machine(state, machine_id, mode_of(request))
    _require_state(machine, ["running", "stopped"], "terminate")
    machine["status"] = "terminated"
    return {
        "machine_id": machine_id,
        "status": "terminated",
        "message": "Machine terminated; all billing has ended.",
        "request_id": request_id(request),
    }


@router.post("/machines/{machine_id}/start")
def start_machine(request: Request, machine_id: str) -> JsonDict:
    state = mock_state(request)
    machine = _get_machine(state, machine_id, mode_of(request))
    _require_state(machine, ["stopped"], "start")
    machine["status"] = "running"
    return {
        "machine_id": machine_id,
        "status": "running",
        "message": "Machine started.",
        "request_id": request_id(request),
    }


@router.post("/machines/{machine_id}/stop")
def stop_machine(request: Request, machine_id: str) -> JsonDict:
    state = mock_state(request)
    machine = _get_machine(state, machine_id, mode_of(request))
    _require_state(machine, ["running"], "stop")
    machine["status"] = "stopped"
    return {
        "machine_id": machine_id,
        "status": "stopped",
        "message": "Machine stopped; storage-only billing applies.",
        "request_id": request_id(request),
    }


@router.post("/machines/{machine_id}/restart")
def restart_machine(request: Request, machine_id: str) -> JsonDict:
    state = mock_state(request)
    machine = _get_machine(state, machine_id, mode_of(request))
    _require_state(machine, ["running"], "restart")
    return {
        "machine_id": machine_id,
        "status": "running",
        "message": "Machine restarted.",
        "request_id": request_id(request),
    }


@router.patch("/machines/{machine_id}")
async def patch_machine(request: Request, machine_id: str) -> JsonDict:
    state = mock_state(request)
    machine = _get_machine(state, machine_id, mode_of(request))
    _require_state(machine, ["running", "stopped"], "update")
    body = await json_body(request)
    raw = body.get("ttl_minutes")
    if isinstance(raw, bool) or not isinstance(raw, int) or (raw != 0 and not 5 <= raw <= 10080):
        raise ApiError(
            "VALIDATION_ERROR",
            "ttl_minutes must be an integer 5-10080, or 0 to clear the TTL.",
            extras={"details": [{"loc": ["body", "ttl_minutes"], "msg": "must be 0 or 5-10080"}]},
        )
    machine["ttl_minutes"] = None if raw == 0 else raw
    return {
        "machine_id": machine_id,
        "ttl_minutes": machine["ttl_minutes"],
        "status": machine["status"],
        "message": "TTL cleared." if raw == 0 else f"TTL set to {raw} minutes.",
        "request_id": request_id(request),
    }


@router.post("/machines/{machine_id}/snapshot")
async def snapshot_machine(request: Request, machine_id: str) -> JsonDict:
    state = mock_state(request)
    machine = _get_machine(state, machine_id, mode_of(request))
    _require_state(machine, ["running", "stopped"], "snapshot")
    body = await json_body(request)
    cache_key, cached = check_idempotency(request, body, f"snapshot:{machine_id}")
    if cached is not None:
        return cached
    charge(request, SNAPSHOT, "machines.snapshot")
    number = state.next_counter(f"snapshot:{machine_id}")
    snapshot_id = "snap_" + state.deterministic_hex(f"snapshot:{machine_id}:{number}", 12)
    response = {
        "machine_id": machine_id,
        "snapshot_id": snapshot_id,
        "name": f"{machine['display_name']}-snapshot-{number}",
        "created_at": iso(state.clock.now()),
        "credits_charged": SNAPSHOT,
        "request_id": request_id(request),
    }
    store_idempotent(request, cache_key, body, response)
    return response


@router.get("/machines/{machine_id}/screenshot")
def machine_screenshot(request: Request, machine_id: str) -> JsonDict:
    state = mock_state(request)
    machine = _get_machine(state, machine_id, mode_of(request))
    _require_state(machine, ["running"], "screenshot")
    return {
        "machine_id": machine_id,
        "image_b64": screenshot_b64(),
        "mime_type": "image/png",
        "width": SCREENSHOT_WIDTH,
        "height": SCREENSHOT_HEIGHT,
        "captured_at": iso(state.clock.now()),
        "request_id": request_id(request),
    }


@router.get("/machines/{machine_id}/connection")
def machine_connection(request: Request, machine_id: str) -> Response:
    state = mock_state(request)
    machine = _get_machine(state, machine_id, mode_of(request))
    key_hex = state.deterministic_hex(f"sshkey:{machine_id}", 48)
    body = {
        **_connection_summary(machine),
        "ssh_private_key_pem": (
            "-----BEGIN OPENSSH PRIVATE KEY-----\n"
            f"MOCKKEY{key_hex}\n"
            "-----END OPENSSH PRIVATE KEY-----\n"
        ),
        "vnc_password": state.deterministic_hex(f"vnc:{machine_id}", 12),
        "websocket_url": f"ws://{machine['public_ip']}:8080/ws",
        "devtools_url": f"http://{machine['public_ip']}:9222/devtools",
        "request_id": request_id(request),
    }
    return JSONResponse(content=body, headers={"Cache-Control": "no-store"})


# ------------------------------------------------------------------ actions
def _execute_action(
    state: TestState, machine: JsonDict, command: str, parameters: JsonDict
) -> tuple[bool, JsonDict | None, str | None]:
    """Returns (success, result, error). Deterministic per command."""
    if command == "fail":
        return False, None, "Forced failure (mock 'fail' command)."
    if command == "screenshot":
        return True, {"captured": True}, None
    if command in {"click", "double_click", "right_click", "move"}:
        x = parameters.get("x", 0)
        y = parameters.get("y", 0)
        return True, {"success": True, "x": x, "y": y}, None
    if command in {"type", "type_text"}:
        text = str(parameters.get("text", ""))
        return True, {"success": True, "text_length": len(text)}, None
    if command == "key_press":
        return True, {"success": True, "key": parameters.get("key", "enter")}, None
    if command == "key_combo":
        return True, {"success": True, "keys": parameters.get("keys", [])}, None
    if command == "scroll":
        return True, {"success": True, "amount": parameters.get("amount", 3)}, None
    if command == "wait":
        return True, {"success": True, "ms": parameters.get("ms", 500)}, None
    if command in {"terminal", "terminal_run"}:
        output = _terminal_output(machine, str(parameters.get("command", "")))
        return True, {"output": output, "exit_code": 0}, None
    if command.startswith("file_") or command.startswith("directory_"):
        op = command.removeprefix("file_").removeprefix("directory_").replace("_", "-")
        if command.startswith("directory_"):
            op = f"{op}-directory" if op in {"list", "delete"} else op
        success, result, error = _execute_file_op(machine, op, parameters)
        return success, result, error
    if command.startswith("browser_"):
        op = command.removeprefix("browser_")
        return True, _browser_result(machine, op, parameters), None
    return False, None, f"Unknown command {command!r}."


def _terminal_output(machine: JsonDict, command: str) -> str:
    shell = "powershell" if machine["os_type"] == "windows" else "bash"
    return f"[mock {shell} on {machine['id']}] $ {command}\n{command}"[:5000]


def _browser_result(machine: JsonDict, op: str, parameters: JsonDict) -> JsonDict:
    result: JsonDict = {"op": op, "success": True}
    if op in {"open", "navigate"}:
        url = str(parameters.get("url", "about:blank"))
        result.update({"url": url, "title": f"Mock page: {url}"})
    elif op == "list-tabs":
        result.update({"tabs": [{"tab_id": "tab_1", "url": "about:blank", "active": True}]})
    elif op == "open-tab":
        result.update({"tab_id": "tab_2"})
    elif op == "dom":
        result.update({"dom": "<html><body><h1>Mock page</h1></body></html>"})
    elif op == "clickables":
        result.update({"clickables": [{"selector": "#mock-button", "text": "Mock button"}]})
    elif op in {"state", "info"}:
        result.update({"url": "about:blank", "title": "Mock page", "ready": True})
    elif op == "screenshot":
        result.update({"image_b64": screenshot_b64(), "mime_type": "image/png"})
    else:
        result.update({"parameters": parameters})
    return result


def _execute_file_op(
    machine: JsonDict, op: str, parameters: JsonDict
) -> tuple[bool, JsonDict | None, str | None]:
    files: dict[str, str] = machine["_files"]
    path = str(parameters.get("path", ""))
    if op == "write":
        if not path:
            return False, None, "parameters.path is required."
        files[path] = str(parameters.get("content", ""))
        return True, {"path": path, "bytes_written": len(files[path])}, None
    if op == "append":
        if not path:
            return False, None, "parameters.path is required."
        files[path] = files.get(path, "") + str(parameters.get("content", ""))
        return True, {"path": path, "bytes_written": len(files[path])}, None
    if op == "edit":
        if path not in files:
            return False, None, f"File not found: {path}"
        if "old_text" in parameters:
            files[path] = files[path].replace(
                str(parameters.get("old_text", "")), str(parameters.get("new_text", ""))
            )
        else:
            files[path] = str(parameters.get("content", ""))
        return True, {"path": path, "bytes_written": len(files[path])}, None
    if op == "read":
        if path not in files:
            return False, None, f"File not found: {path}"
        return True, {"path": path, "content": files[path]}, None
    if op == "download":
        if path not in files:
            return False, None, f"File not found: {path}"
        encoded = base64.b64encode(files[path].encode()).decode("ascii")
        return True, {"path": path, "content_b64": encoded}, None
    if op == "exists":
        return True, {"path": path, "exists": path in files}, None
    if op in {"list", "list-directory"}:
        prefix = path.rstrip("/")
        entries = sorted(p for p in files if not prefix or p.startswith(prefix))
        return True, {"path": path or "/", "entries": entries}, None
    if op == "list-downloads":
        return True, {"downloads": []}, None
    if op == "delete":
        if path not in files:
            return False, None, f"File not found: {path}"
        del files[path]
        return True, {"path": path, "deleted": True}, None
    if op == "delete-directory":
        prefix = path.rstrip("/")
        doomed = [p for p in files if prefix and p.startswith(prefix)]
        for p in doomed:
            del files[p]
        return True, {"path": path, "deleted_count": len(doomed)}, None
    return False, None, f"Unknown file op {op!r}."  # pragma: no cover - guarded by route


def _validate_action_body(body: JsonDict) -> tuple[str, JsonDict, int | None]:
    vd = Validator()
    command = field_str(body, "command", vd, required=True, min_len=1)
    parameters = field_dict(body, "parameters", vd)
    timeout_ms = field_int(body, "timeout_ms", vd, lo=1000, hi=120000)
    vd.raise_if_any()
    assert command is not None
    return command, parameters or {}, timeout_ms


@router.post("/machines/{machine_id}/actions")
async def machine_action(request: Request, machine_id: str) -> JsonDict:
    state = mock_state(request)
    machine = _get_machine(state, machine_id, mode_of(request))
    _require_state(machine, ["running"], "run actions on")
    body = await json_body(request)
    command, parameters, _ = _validate_action_body(body)
    success, result, error = _execute_action(state, machine, command, parameters)
    return {
        "machine_id": machine_id,
        "command": command,
        "success": success,
        "result": result,
        "error": error,
        "duration_ms": _duration_ms(command),
        "screenshot": screenshot_b64() if command == "screenshot" else None,
        "request_id": request_id(request),
    }


@router.post("/machines/{machine_id}/actions/batch")
async def machine_actions_batch(request: Request, machine_id: str) -> JsonDict:
    state = mock_state(request)
    machine = _get_machine(state, machine_id, mode_of(request))
    _require_state(machine, ["running"], "run actions on")
    body = await json_body(request)
    vd = Validator()
    steps = body.get("steps")
    if not isinstance(steps, list) or not steps:
        vd.add(["body", "steps"], "expected a non-empty array of action steps")
    elif len(steps) > MAX_BATCH_STEPS:
        vd.add(["body", "steps"], f"at most {MAX_BATCH_STEPS} steps per batch")
    stop_on_error = field_bool(body, "stop_on_error", vd, default=True)
    vd.raise_if_any()
    assert isinstance(steps, list) and stop_on_error is not None

    results: list[JsonDict] = []
    completed = 0
    failed = 0
    aborted = False
    for step in steps:
        if not isinstance(step, dict):
            raise ApiError(
                "VALIDATION_ERROR",
                "Each batch step must be an ActionRequest object.",
                extras={"details": [{"loc": ["body", "steps"], "msg": "expected objects"}]},
            )
        command, parameters, _ = _validate_action_body(step)
        success, result, error = _execute_action(state, machine, command, parameters)
        results.append(
            {
                "command": command,
                "success": success,
                "result": result,
                "error": error,
                "duration_ms": _duration_ms(command),
            }
        )
        if success:
            completed += 1
        else:
            failed += 1
            if stop_on_error:
                aborted = True
                break
    return {
        "machine_id": machine_id,
        "results": results,
        "completed_count": completed,
        "failed_count": failed,
        "aborted": aborted,
        "request_id": request_id(request),
    }


@router.post("/machines/{machine_id}/browser/{op}")
async def machine_browser(request: Request, machine_id: str, op: str) -> JsonDict:
    state = mock_state(request)
    machine = _get_machine(state, machine_id, mode_of(request))
    _require_state(machine, ["running"], "drive the browser on")
    if op not in BROWSER_OPS:
        raise ApiError(
            "NOT_FOUND",
            f"Unknown browser op {op!r}; must be one of {sorted(BROWSER_OPS)}.",
        )
    body = await json_body(request)
    vd = Validator()
    parameters = field_dict(body, "parameters", vd)
    field_int(body, "timeout_ms", vd, lo=1000, hi=120000)
    vd.raise_if_any()
    return {
        "machine_id": machine_id,
        "op": op,
        "success": True,
        "result": _browser_result(machine, op, parameters or {}),
        "request_id": request_id(request),
    }


@router.post("/machines/{machine_id}/terminal")
async def machine_terminal(request: Request, machine_id: str) -> JsonDict:
    state = mock_state(request)
    machine = _get_machine(state, machine_id, mode_of(request))
    _require_state(machine, ["running"], "run a terminal command on")
    body = await json_body(request)
    vd = Validator()
    command = field_str(body, "command", vd, required=True, min_len=1, max_len=8192)
    field_int(body, "timeout_ms", vd, default=30000, lo=1000, hi=120000)
    session_id = field_str(body, "session_id", vd)
    cwd = field_str(body, "cwd", vd)
    vd.raise_if_any()
    assert command is not None
    return {
        "machine_id": machine_id,
        "success": True,
        "output": _terminal_output(machine, command),
        "exit_code": 0,
        "duration_ms": _duration_ms(command),
        "session_id": session_id or "term_" + state.deterministic_hex(f"term:{machine_id}", 8),
        "cwd": cwd,
        "request_id": request_id(request),
    }


@router.post("/machines/{machine_id}/files/{op}")
async def machine_files(request: Request, machine_id: str, op: str) -> JsonDict:
    state = mock_state(request)
    machine = _get_machine(state, machine_id, mode_of(request))
    _require_state(machine, ["running"], "run file ops on")
    if op not in FILE_READ_OPS and op not in FILE_WRITE_OPS:
        raise ApiError(
            "NOT_FOUND",
            f"Unknown file op {op!r}; must be one of " f"{sorted(FILE_READ_OPS | FILE_WRITE_OPS)}.",
        )
    body = await json_body(request)
    vd = Validator()
    parameters = field_dict(body, "parameters", vd)
    vd.raise_if_any()
    success, result, error = _execute_file_op(machine, op, parameters or {})
    return {
        "machine_id": machine_id,
        "op": op,
        "success": success,
        "result": result,
        "error": error,
        "request_id": request_id(request),
    }
