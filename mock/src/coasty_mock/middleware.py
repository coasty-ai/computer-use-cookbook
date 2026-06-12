"""Pure-ASGI middleware: request ids, auth, forced errors, response headers.

Responsibilities (in order):

1. Mint a deterministic ``X-Coasty-Request-Id`` for EVERY response.
2. ``X-Mock-Force-Error: <CODE>`` short-circuits any ``/v1`` request with the
   documented envelope + status for that code (powers the error matrix).
3. Authenticate ``/v1`` routes: ``X-API-Key`` or ``Authorization: Bearer``;
   accepts ``sk-coasty-test-*`` / ``sk-coasty-live-*`` / legacy ``cua_sk_*``.
4. Inject ``X-Coasty-Key-Kind``, ``X-Coasty-Test-Mode``, billing headers
   (``X-Credits-Charged`` / ``X-Credits-Remaining``) and
   ``X-Coasty-Idempotent-Replay`` where handlers recorded them.
5. Convert any uncaught exception into a 500 ``INTERNAL_ERROR`` envelope that
   still carries the request id (no silent failures).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from .errors import ApiError, error_body, forced_error
from .state import TestState

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
AsgiApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def classify_key(key: str) -> str | None:
    """Key kind for a raw credential, or None when malformed."""
    if key.startswith("Bearer "):
        return None  # the documented 'Bearer pasted into X-API-Key' mistake
    if key.startswith("sk-coasty-test-") and len(key) > len("sk-coasty-test-"):
        return "test"
    if key.startswith("sk-coasty-live-") and len(key) > len("sk-coasty-live-"):
        return "live"
    if key.startswith("cua_sk_") and len(key) > len("cua_sk_"):
        return "legacy"
    return None


class CoastyMiddleware:
    def __init__(self, app: AsgiApp, state: TestState) -> None:
        self.app = app
        self.state = state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        st = self.state
        request_id = st.next_request_id()
        scope.setdefault("state", {})
        req_state: MutableMapping[str, Any] = scope["state"]
        req_state["request_id"] = request_id

        headers: dict[str, str] = {}
        for raw_key, raw_value in scope.get("headers", []):
            headers[raw_key.decode("latin-1").lower()] = raw_value.decode("latin-1")

        response_started = False

        async def send_with_headers(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                raw: list[tuple[bytes, bytes]] = list(message.get("headers", []))
                raw.append((b"x-coasty-request-id", request_id.encode("latin-1")))
                kind = req_state.get("key_kind")
                if isinstance(kind, str):
                    raw.append((b"x-coasty-key-kind", kind.encode("latin-1")))
                    if kind == "test":
                        raw.append((b"x-coasty-test-mode", b"true"))
                charged = req_state.get("credits_charged")
                if charged is not None:
                    raw.append((b"x-credits-charged", str(charged).encode("latin-1")))
                    remaining = str(st.wallet_balance_cents)
                    raw.append((b"x-credits-remaining", remaining.encode("latin-1")))
                if req_state.get("idempotent_replay"):
                    raw.append((b"x-coasty-idempotent-replay", b"true"))
                message["headers"] = raw
            await send(message)

        path: str = scope["path"]

        if path.startswith("/__mock__"):
            await self.app(scope, receive, send_with_headers)
            return

        forced_code = headers.get("x-mock-force-error")
        if forced_code:
            error = forced_error(
                forced_code,
                path=path,
                method=str(scope.get("method", "GET")),
                wallet_balance_cents=st.wallet_balance_cents,
                retry_after_seconds=st.config.retry_after_seconds,
            )
            await self._send_error(error, request_id, scope, receive, send_with_headers)
            return

        key = headers.get("x-api-key")
        if key is None:
            authorization = headers.get("authorization", "")
            if authorization.startswith("Bearer "):
                key = authorization[len("Bearer ") :]

        kind = classify_key(key) if key else None
        if key is None or kind is None:
            error = ApiError(
                "INVALID_API_KEY",
                "Missing or malformed API key. Send a raw sk-coasty-test-/sk-coasty-live- "
                "key in X-API-Key, or Authorization: Bearer <key>.",
            )
            await self._send_error(error, request_id, scope, receive, send_with_headers)
            return

        req_state["api_key"] = key
        req_state["key_kind"] = kind
        st.count_request()

        try:
            await self.app(scope, receive, send_with_headers)
        except Exception as exc:  # noqa: BLE001 - render every failure as an envelope
            if response_started:
                raise
            error = ApiError("INTERNAL_ERROR", f"Unhandled mock-server error: {exc!r}")
            await self._send_error(error, request_id, scope, receive, send_with_headers)

    async def _send_error(
        self,
        error: ApiError,
        request_id: str,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        body = json.dumps(
            error_body(error.code, error.message, error.error_type, request_id, error.extras)
        ).encode()
        raw_headers: list[tuple[bytes, bytes]] = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("latin-1")),
        ]
        for name, value in error.headers.items():
            raw_headers.append((name.lower().encode("latin-1"), value.encode("latin-1")))
        await send({"type": "http.response.start", "status": error.status, "headers": raw_headers})
        await send({"type": "http.response.body", "body": body})
