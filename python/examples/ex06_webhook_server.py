"""Example 06 -- Webhook receiver with HMAC verification (stdlib http.server only).

Purpose
    Receive Coasty run lifecycle webhooks, verify the ``Coasty-Signature``
    header via :func:`coasty.webhooks.verify_signature` (HMAC-SHA256 over
    ``"<t>." + raw_body``, constant-time compare, +/- 5 minute timestamp
    tolerance), and dispatch per-event handlers.

Flow
    Coasty POSTs JSON  ->  verify signature (401 on invalid / stale /
    tampered / malformed)  ->  parse the signed body  ->  dispatch on
    ``payload["event"]``  ->  respond 200 FAST. Handlers here only log;
    anything slow (DB writes, resuming runs) belongs on a queue / thread so
    the 200 goes back immediately and Coasty never retries spuriously.

Endpoints
    None called -- this is the *receiving* side. Runs created with a
    ``webhook_url`` get callbacks for run.succeeded / run.failed /
    run.cancelled / run.timed_out / run.awaiting_human, each signed with the
    run's one-time ``webhook_secret`` (returned exactly once on create).

Estimated cost
    $0.00 -- receiving webhooks is free (coasty.cost: no billable operation
    in this example), so there is no spend gate.

Run
    COASTY_WEBHOOK_SECRET=whsec_... python examples/ex06_webhook_server.py \
        --host 127.0.0.1 --port 8788
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from coasty import verify_signature

logger = logging.getLogger("coasty.examples.ex06")

SIGNATURE_HEADER = "Coasty-Signature"
SECRET_ENV_VAR = "COASTY_WEBHOOK_SECRET"

EventHandler = Callable[[dict[str, Any]], str]


# ── per-event handlers (keep them FAST; offload slow work) ─────────────────


def _run_id_of(payload: Mapping[str, Any]) -> str:
    run_id = payload.get("run_id")
    return run_id if isinstance(run_id, str) else "<unknown>"


def on_run_succeeded(payload: dict[str, Any]) -> str:
    action = f"acknowledged run.succeeded for run {_run_id_of(payload)}"
    logger.info("%s", action)
    return action


def on_run_failed(payload: dict[str, Any]) -> str:
    action = f"acknowledged run.failed for run {_run_id_of(payload)} -- alert the on-call"
    logger.warning("%s", action)
    return action


def on_run_cancelled(payload: dict[str, Any]) -> str:
    action = f"acknowledged run.cancelled for run {_run_id_of(payload)}"
    logger.info("%s", action)
    return action


def on_run_timed_out(payload: dict[str, Any]) -> str:
    action = f"acknowledged run.timed_out for run {_run_id_of(payload)} -- consider a retry"
    logger.warning("%s", action)
    return action


def on_run_awaiting_human(payload: dict[str, Any]) -> str:
    """The run paused for a human (captcha, 2FA, ambiguous UI, ...).

    This is exactly where a real operator console would, once a human has
    cleared the blocker, hand control back to the agent:

        from coasty import CoastyClient

        with CoastyClient() as client:
            result = client.resume_run(run_id, note="human solved the captcha")

    ``POST /v1/runs/{id}/resume`` is only valid while the run is in
    ``awaiting_human`` -- anything else returns ``409 NOT_AWAITING_HUMAN``.
    Do NOT call it synchronously from this handler: respond 200 first, then
    resume from a worker once the human is actually done.
    """
    run_id = _run_id_of(payload)
    reason = payload.get("reason")
    action = (
        f"resume_required: run {run_id} is awaiting a human (reason={reason!r}) -- "
        "call runs.resume once resolved"
    )
    logger.warning("%s", action)
    return action


EVENT_HANDLERS: dict[str, EventHandler] = {
    "run.succeeded": on_run_succeeded,
    "run.failed": on_run_failed,
    "run.cancelled": on_run_cancelled,
    "run.timed_out": on_run_timed_out,
    "run.awaiting_human": on_run_awaiting_human,
}


# ── pure, socket-free core (this is what the unit tests drive) ─────────────


def handle_webhook(
    raw_body: bytes,
    headers: Mapping[str, str],
    secret: str,
    *,
    now: int | None = None,
) -> tuple[int, str]:
    """Verify + dispatch one webhook delivery; returns ``(status, action)``.

    - 401 when the ``Coasty-Signature`` header is missing, malformed, signed
      with the wrong secret, computed over a tampered body, or its timestamp
      is outside the +/- 5 minute tolerance (``now`` is injectable for tests).
    - 400 when the signature is valid but the body is not a JSON object.
    - 200 otherwise; unknown event types are acknowledged and ignored.
    """
    header = next(
        (value for key, value in headers.items() if key.lower() == SIGNATURE_HEADER.lower()),
        None,
    )
    if header is None:
        return 401, f"rejected: missing {SIGNATURE_HEADER} header"
    if not verify_signature(raw_body, header, secret, now=now):
        return 401, "rejected: invalid, stale, or tampered signature"

    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 400, "rejected: signed body is not valid JSON"
    if not isinstance(payload, dict):
        return 400, "rejected: signed body is not a JSON object"

    event = payload.get("event")
    handler = EVENT_HANDLERS.get(event) if isinstance(event, str) else None
    if handler is None:
        return 200, f"ignored: unhandled event {event!r}"
    return 200, handler(payload)


# ── thin stdlib HTTP wrapper around the core ───────────────────────────────


class WebhookRequestHandler(BaseHTTPRequestHandler):
    """Reads the raw body, delegates to :func:`handle_webhook`, replies fast."""

    secret: str = ""  # bound per-server by make_server()

    def do_POST(self) -> None:  # stdlib handler hook (name fixed by http.server)
        length_header = self.headers.get("Content-Length", "0")
        try:
            length = int(length_header)
        except ValueError:
            length = 0
        raw_body = self.rfile.read(length) if length > 0 else b""

        status, action = handle_webhook(raw_body, dict(self.headers.items()), self.secret)

        body = json.dumps({"received": status == 200, "action": action}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # stdlib hook signature
        logger.info("%s -- %s", self.address_string(), format % args)


def make_server(host: str, port: int, secret: str) -> ThreadingHTTPServer:
    """Build a webhook server bound to ``secret`` (port 0 = ephemeral)."""

    class BoundHandler(WebhookRequestHandler):
        pass

    BoundHandler.secret = secret
    return ThreadingHTTPServer((host, port), BoundHandler)


# ── CLI ────────────────────────────────────────────────────────────────────


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8788, help="bind port (default 8788)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    secret = os.environ.get(SECRET_ENV_VAR, "").strip()
    if not secret:
        print(
            f"error: {SECRET_ENV_VAR} is not set. Use the one-time webhook_secret "
            "returned when the run was created (never logged here).",
            file=sys.stderr,
        )
        return 2

    server = make_server(args.host, args.port, secret)
    raw_host, port = server.server_address[0], server.server_address[1]
    host = raw_host.decode() if isinstance(raw_host, bytes) else raw_host
    print(f"listening on http://{host}:{port} -- POST signed webhooks here (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("shutting down")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
