"""Minimal offline stub of the Coasty /v1 API for testing curl/quickstart.sh.

This is a TEST FIXTURE, not the full mock server (see ../../mock). It
implements exactly the endpoints the quickstart touches, validates the
request shapes the script is supposed to send (auth header, Idempotency-Key,
screenshot length, ...), and replies with documented response shapes plus the
documented headers (X-Coasty-Request-Id, X-Credits-Charged, ...).

Usage:
    python stub_server.py <port_file>

Binds 127.0.0.1 on an ephemeral port and writes the chosen port number to
<port_file> once it is ready to accept connections. Runs until killed.

The only valid API key is taken from the COASTY_STUB_KEY environment variable
(default: an obviously-fake sandbox key). Every other key gets the documented
401 INVALID_API_KEY envelope, which the quickstart's deliberate-401 demo
relies on. Deterministic; no sleeps; stdlib only.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

DEFAULT_STUB_KEY = "sk-coasty-test-" + "1" * 48

_request_counter = 0
_counter_lock = threading.Lock()

# Mutable run state: run_id -> number of GETs served so far.
_run_gets: dict[str, int] = {}
_state_lock = threading.Lock()

_RUN_ID = "run_stub_0001"
_SESSION_ID = "sess_stub_0001"
_MACHINE_ID = "mch_test_stub01"

_DEMO_ACTION: dict[str, Any] = {
    "action_type": "click",
    "params": {"x": 160, "y": 120},
    "description": "Click the OK button",
    "raw_code": "pyautogui.click(160, 120)",
}
_DEMO_USAGE: dict[str, Any] = {
    "input_tokens": 100,
    "output_tokens": 20,
    "credits_charged": 0,
    "cost_cents": 0,
}


def _next_request_id() -> str:
    global _request_counter
    with _counter_lock:
        _request_counter += 1
        return f"req_stub_{_request_counter:06d}"


class StubHandler(BaseHTTPRequestHandler):
    """Routes the handful of /v1 endpoints the quickstart exercises."""

    server_version = "CoastyStub/1.0"
    protocol_version = "HTTP/1.1"

    valid_key: str = DEFAULT_STUB_KEY

    # -- plumbing -------------------------------------------------------------

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Silence per-request logging to keep smoke output deterministic."""

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _send_json(
        self,
        status: int,
        payload: dict[str, Any],
        extra_headers: dict[str, str] | None = None,
        billed: bool = True,
    ) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Coasty-Request-Id", payload.get("request_id") or _next_request_id())
        self.send_header("X-Coasty-Test-Mode", "true")
        self.send_header("X-Coasty-Key-Kind", "test")
        if billed and status < 400:
            self.send_header("X-Credits-Charged", "0")
            self.send_header("X-Credits-Remaining", "100000")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_error(
        self,
        status: int,
        code: str,
        message: str,
        error_type: str,
        extra_headers: dict[str, str] | None = None,
        **context: object,
    ) -> None:
        request_id = _next_request_id()
        envelope: dict[str, Any] = {
            "error": {
                "code": code,
                "message": message,
                "type": error_type,
                "request_id": request_id,
                "suggestion": "See https://coasty.ai/api-docs#errors",
                "docs_url": "https://coasty.ai/api-docs#errors",
                **context,
            }
        }
        body = json.dumps(envelope).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Coasty-Request-Id", request_id)
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _authed(self) -> bool:
        """Accept X-API-Key: <key> or Authorization: Bearer <key>."""
        x_api_key = self.headers.get("X-API-Key")
        if x_api_key is not None:
            return x_api_key == self.valid_key
        authz = self.headers.get("Authorization") or ""
        return authz == f"Bearer {self.valid_key}"

    def _reject_unauthed(self) -> bool:
        if self._authed():
            return False
        self._send_error(
            401,
            "INVALID_API_KEY",
            "The provided API key is invalid, malformed, or revoked.",
            "auth_error",
            extra_headers={"WWW-Authenticate": "Bearer"},
        )
        return True

    @staticmethod
    def _bad_screenshot(body: dict[str, Any]) -> bool:
        shot = body.get("screenshot")
        return (
            not isinstance(shot, str)
            or len(shot) <= 100
            or shot.startswith("data:")
            or "\n" in shot
        )

    # -- HTTP verbs -----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        if self._reject_unauthed():
            return
        if self.path == "/v1/models":
            self._send_json(
                200,
                {
                    "models": [
                        {"id": "default", "description": "Default model (stub)"},
                    ],
                    "cua_versions": [
                        {"id": "v3", "description": "Lean (stub)"},
                    ],
                    "action_types": [
                        "click",
                        "type_text",
                        "key_press",
                        "key_combo",
                        "scroll",
                        "drag",
                        "move",
                        "wait",
                        "done",
                        "fail",
                    ],
                },
                billed=False,
            )
            return
        match = re.fullmatch(r"/v1/runs/([A-Za-z0-9_-]+)", self.path)
        if match:
            self._get_run(match.group(1))
            return
        self._send_error(404, "NOT_FOUND", f"No route for GET {self.path}", "not_found_error")

    def do_POST(self) -> None:  # noqa: N802
        if self._reject_unauthed():
            return
        body = self._read_body()
        if self.path == "/v1/parse":
            self._post_parse(body)
        elif self.path == "/v1/predict":
            self._post_predict(body)
        elif self.path == "/v1/ground":
            self._post_ground(body)
        elif self.path == "/v1/sessions":
            self._post_sessions(body)
        elif re.fullmatch(r"/v1/sessions/[A-Za-z0-9_-]+/predict", self.path):
            self._post_session_predict(body)
        elif self.path == "/v1/runs":
            self._post_runs(body)
        elif self.path == "/v1/machines":
            self._post_machines(body)
        else:
            self._send_error(404, "NOT_FOUND", f"No route for POST {self.path}", "not_found_error")

    def do_DELETE(self) -> None:  # noqa: N802
        if self._reject_unauthed():
            return
        match = re.fullmatch(r"/v1/sessions/([A-Za-z0-9_-]+)", self.path)
        if match:
            self._send_json(200, {"status": "ok", "session_id": match.group(1)}, billed=False)
            return
        match = re.fullmatch(r"/v1/machines/([A-Za-z0-9_-]+)", self.path)
        if match:
            self._send_json(
                200,
                {
                    "machine_id": match.group(1),
                    "status": "terminated",
                    "message": "Machine terminated.",
                    "request_id": _next_request_id(),
                },
                billed=False,
            )
            return
        self._send_error(404, "NOT_FOUND", f"No route for DELETE {self.path}", "not_found_error")

    # -- endpoint bodies ------------------------------------------------------

    def _post_parse(self, body: dict[str, Any]) -> None:
        code = body.get("code")
        if not isinstance(code, str) or not code or len(code) >= 50_000:
            self._send_error(
                422,
                "VALIDATION_ERROR",
                "code must be a non-empty string under 50k chars",
                "validation_error",
                details=[{"loc": ["body", "code"]}],
            )
            return
        self._send_json(
            200,
            {
                "actions": [
                    {"action_type": "click", "params": {"x": 120, "y": 80}},
                    {"action_type": "type_text", "params": {"text": "hello@example.com"}},
                    {"action_type": "key_press", "params": {"key": "enter"}},
                ]
            },
            billed=False,
        )

    def _post_predict(self, body: dict[str, Any]) -> None:
        if self._bad_screenshot(body):
            self._send_error(
                422,
                "INVALID_SCREENSHOT",
                "screenshot must be raw base64 over 100 chars with no data: prefix",
                "validation_error",
            )
            return
        if not body.get("instruction"):
            self._send_error(
                422,
                "VALIDATION_ERROR",
                "instruction must be non-empty",
                "validation_error",
                details=[{"loc": ["body", "instruction"]}],
            )
            return
        self._send_json(
            200,
            {
                "request_id": _next_request_id(),
                "status": "continue",
                "reasoning": "The OK button is visible; clicking it.",
                "actions": [_DEMO_ACTION],
                "raw_code": [_DEMO_ACTION["raw_code"]],
                "usage": _DEMO_USAGE,
            },
        )

    def _post_ground(self, body: dict[str, Any]) -> None:
        if self._bad_screenshot(body):
            self._send_error(
                422,
                "INVALID_SCREENSHOT",
                "screenshot must be raw base64 over 100 chars with no data: prefix",
                "validation_error",
            )
            return
        if not body.get("element"):
            self._send_error(
                422,
                "VALIDATION_ERROR",
                "element must be non-empty",
                "validation_error",
                details=[{"loc": ["body", "element"]}],
            )
            return
        self._send_json(200, {"x": 160, "y": 120, "usage": _DEMO_USAGE})

    def _post_sessions(self, body: dict[str, Any]) -> None:
        width = body.get("screen_width", 1920)
        height = body.get("screen_height", 1080)
        self._send_json(
            200,
            {
                "session_id": _SESSION_ID,
                "cua_version": body.get("cua_version", "v3"),
                "screen_size": f"{width}x{height}",
                "created_at": "2026-06-11T00:00:00Z",
                "expires_at": "2026-06-11T00:30:00Z",
            },
        )

    def _post_session_predict(self, body: dict[str, Any]) -> None:
        if self._bad_screenshot(body) or not body.get("instruction"):
            self._send_error(
                422,
                "VALIDATION_ERROR",
                "screenshot and instruction are required",
                "validation_error",
            )
            return
        self._send_json(
            200,
            {
                "request_id": _next_request_id(),
                "session_id": _SESSION_ID,
                "step": 1,
                "status": "continue",
                "reasoning": "Clicking the OK button.",
                "actions": [_DEMO_ACTION],
                "raw_code": [_DEMO_ACTION["raw_code"]],
                "usage": _DEMO_USAGE,
            },
        )

    def _post_machines(self, body: dict[str, Any]) -> None:
        if not body.get("display_name"):
            self._send_error(
                422,
                "VALIDATION_ERROR",
                "display_name is required",
                "validation_error",
                details=[{"loc": ["body", "display_name"]}],
            )
            return
        self._send_json(
            200,
            {
                "machine": {
                    "id": _MACHINE_ID,
                    "display_name": body["display_name"],
                    "status": "running",
                    "os_type": body.get("os_type", "linux"),
                    "provider": "sandbox",
                    "desktop_enabled": False,
                    "cpu_cores": 2,
                    "memory_gb": 4,
                    "storage_gb": 16,
                    "public_ip": "127.0.0.1",
                    "is_test": True,
                    "created_at": "2026-06-11T00:00:00Z",
                    "metadata": body.get("metadata"),
                },
                "connection": {
                    "public_ip": "127.0.0.1",
                    "ssh_port": 22,
                    "ssh_username": "coasty",
                    "vnc_port": 5900,
                    "websocket_port": 8080,
                    "has_ssh_key": True,
                    "has_vnc_password": True,
                },
                "request_id": _next_request_id(),
            },
        )

    def _post_runs(self, body: dict[str, Any]) -> None:
        # The quickstart MUST send an Idempotency-Key on run creation; the
        # stub enforces it so a regression fails the smoke test loudly.
        idempotency_key = self.headers.get("Idempotency-Key")
        if not idempotency_key or not re.fullmatch(r"[A-Za-z0-9_\-:]{1,128}", idempotency_key):
            self._send_error(
                422,
                "VALIDATION_ERROR",
                "stub requires a well-formed Idempotency-Key header on POST /v1/runs",
                "validation_error",
            )
            return
        if not body.get("machine_id") or not body.get("task"):
            self._send_error(
                422,
                "VALIDATION_ERROR",
                "machine_id and task are required",
                "validation_error",
            )
            return
        with _state_lock:
            _run_gets[_RUN_ID] = 0
        self._send_json(200, self._run_payload(status="queued", steps=0, finished=False))

    def _get_run(self, run_id: str) -> None:
        with _state_lock:
            if run_id not in _run_gets:
                self._send_error(404, "RUN_NOT_FOUND", f"No run {run_id}", "not_found_error")
                return
            _run_gets[run_id] += 1
            gets = _run_gets[run_id]
        # Deterministic state machine: 1st poll -> running, 2nd+ -> succeeded.
        if gets == 1:
            self._send_json(
                200,
                self._run_payload(status="running", steps=1, finished=False),
                billed=False,
            )
        else:
            self._send_json(
                200,
                self._run_payload(status="succeeded", steps=2, finished=True),
                billed=False,
            )

    @staticmethod
    def _run_payload(status: str, steps: int, finished: bool) -> dict[str, Any]:
        return {
            "id": _RUN_ID,
            "object": "agent.run",
            "status": status,
            "machine_id": _MACHINE_ID,
            "task": "Open a terminal and run the 'date' command",
            "cua_version": "v3",
            "instructions": None,
            "max_steps": 2,
            "on_awaiting_human": "fail",
            "steps_completed": steps,
            "credits_charged": 0,
            "cost_cents": 0,
            "result": (
                {"passed": True, "status": "succeeded", "summary": "Ran date successfully."}
                if finished
                else None
            ),
            "error": None,
            "awaiting_human_reason": None,
            "metadata": None,
            "webhook_url": None,
            "webhook_secret": None,
            "created_at": "2026-06-11T00:00:00Z",
            "started_at": "2026-06-11T00:00:01Z" if steps else None,
            "awaiting_human_since": None,
            "finished_at": "2026-06-11T00:00:05Z" if finished else None,
            "request_id": _next_request_id(),
        }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: stub_server.py <port_file>", file=sys.stderr)
        return 2
    port_file = argv[1]
    StubHandler.valid_key = os.environ.get("COASTY_STUB_KEY", DEFAULT_STUB_KEY)

    server = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
    port = server.server_address[1]
    with open(port_file, "w", encoding="utf-8") as handle:
        handle.write(f"{port}\n")
    try:
        server.serve_forever(poll_interval=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
