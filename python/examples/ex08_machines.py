"""Example 08 -- Machines lifecycle with cost-awareness and guaranteed cleanup.

Purpose
    Provision a cloud VM, drive it (screenshot, low-level actions, a batch,
    terminal, files, browser), snapshot it, and ALWAYS stop + terminate it in
    a ``finally`` block -- printing a running cost estimate the whole way.

Flow
    spend gate -> POST /v1/machines (ttl_minutes auto-terminate guard)
    -> PATCH /v1/machines/{id} (re-arm the TTL spend guard)
    -> poll GET /v1/machines/{id} until running
    -> GET /v1/machines/{id}/screenshot -> save PNG
    -> POST .../actions (click, type_text) -> POST .../actions/batch
    -> POST .../terminal -> POST .../files/write + .../files/read
    -> POST .../browser/navigate -> POST .../snapshot ($0.01)
    -> finally: POST .../stop + DELETE /v1/machines/{id}

Endpoints
    POST/GET/PATCH/DELETE /v1/machines[/{id}], /screenshot, /actions,
    /actions/batch, /terminal, /files/{op}, /browser/{op}, /snapshot, /stop

Estimated cost (via coasty.cost, printed before provisioning)
    Runtime is hourly, metered per minute, rounded down: Linux 5 cr/hr,
    Windows 9 cr/hr while running; 1 cr/hr stopped; creating/terminated free.
    Snapshot: 1 cr ($0.01), refunded on failure. Per-call ops (actions,
    terminal, files, browser, screenshots, start/stop) are FREE.
    Sandbox keys get an instant free ``mch_test_*`` VM ($0); live keys need a
    wallet of at least 20 cr ($0.20) to provision (a gate, not a fee) and
    must pass --confirm / COASTY_CONFIRM_SPEND=1.

Run
    python examples/ex08_machines.py --os linux --ttl-minutes 30 \
        --screenshot ex08_screenshot.png --confirm
"""

from __future__ import annotations

import argparse
import base64
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from coasty import CoastyClient, CoastyError, cost, env
from coasty.types import OsType

DEFAULT_DISPLAY_NAME = "cookbook-ex08"
DEFAULT_TTL_MINUTES = 30
DEFAULT_BROWSER_URL = "https://example.com"
REMOTE_FILE_PATH = "/tmp/coasty-ex08.txt"
REMOTE_FILE_CONTENT = "hello from the coasty cookbook (ex08)"
PROVISION_WALLET_GATE_CREDITS = 20


class SpendNotConfirmedError(RuntimeError):
    """Raised when a billable lifecycle is attempted on a live key w/o consent."""


class MachineNeverBecameReadyError(RuntimeError):
    """Polling gave up before the machine reached ``running``."""

    def __init__(self, machine_id: str, status: str, request_id: str | None) -> None:
        super().__init__(
            f"machine {machine_id} never reached 'running' (last status {status!r}, "
            f"request_id={request_id})"
        )
        self.machine_id = machine_id
        self.last_status = status
        self.request_id = request_id


@dataclass(frozen=True)
class LifecycleReport:
    """Summary of one full machine lifecycle, for printing and for tests."""

    machine_id: str
    os_type: OsType
    is_test_machine: bool
    provision_request_id: str | None
    snapshot_id: str | None
    screenshot_path: str | None
    terminal_excerpt: str | None
    file_roundtrip_ok: bool
    elapsed_minutes: float
    estimated_runtime_credits: int
    stopped: bool
    terminated: bool


def ensure_provision_allowed(
    estimate: cost.CostEstimate,
    *,
    sandbox: bool,
    os_type: OsType,
    confirm: bool,
    printer: Callable[[str], None] = print,
) -> None:
    """Print the itemized estimate + live-key gates; refuse without consent."""
    rate = cost.machine_hourly_credits(os_type, "running")
    printer(cost.format_estimate(estimate, title="Estimated machine cost", sandbox=sandbox))
    printer(f"hourly rate while running ({os_type}): {rate} cr/hr, metered per minute")
    if sandbox:
        printer("sandbox key: provisioning is instant and free (mch_test_* VM, $0)")
        return
    printer(
        f"live key: provisioning requires a wallet balance of >= "
        f"{PROVISION_WALLET_GATE_CREDITS} cr ($0.20) -- a gate, not a fee"
    )
    if confirm:
        return
    raise SpendNotConfirmedError(
        "refusing to provision on a live key: pass --confirm or set COASTY_CONFIRM_SPEND=1 "
        "(or use an sk-coasty-test-... sandbox key for a free instant VM)"
    )


def _wait_until_running(
    client: CoastyClient,
    machine_id: str,
    initial_status: str,
    *,
    poll_interval: float,
    max_polls: int,
    sleep: Callable[[float], None],
    printer: Callable[[str], None],
) -> None:
    """Poll GET /v1/machines/{id} until status == running (or give up loudly)."""
    status = initial_status
    last_request_id: str | None = None
    for _ in range(max_polls):
        if status == "running":
            return
        sleep(poll_interval)
        polled = client.get_machine(machine_id)
        machine_raw = polled.data.get("machine", polled.data)
        machine: dict[str, Any] = machine_raw if isinstance(machine_raw, dict) else {}
        status = str(machine.get("status", "unknown"))
        last_request_id = polled.request_id
        printer(f"machine {machine_id}: status={status} (request_id={polled.request_id})")
    if status != "running":
        raise MachineNeverBecameReadyError(machine_id, status, last_request_id)


def run_lifecycle(
    client: CoastyClient,
    *,
    display_name: str = DEFAULT_DISPLAY_NAME,
    os_type: OsType = "linux",
    ttl_minutes: int = DEFAULT_TTL_MINUTES,
    screenshot_path: Path | None = None,
    browser_url: str = DEFAULT_BROWSER_URL,
    confirm_spend: bool = False,
    poll_interval: float = 2.0,
    max_polls: int = 30,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    printer: Callable[[str], None] = print,
) -> LifecycleReport:
    """Provision, exercise, snapshot, and ALWAYS stop + terminate the machine.

    The spend gate runs before any network call; tests can verify a live key
    without ``confirm_spend`` never even provisions.
    """
    estimate = cost.combine(
        cost.estimate_machine_runtime(os_type=os_type, state="running", minutes=ttl_minutes),
        cost.estimate_snapshot(),
    )
    ensure_provision_allowed(
        estimate,
        sandbox=client.is_sandbox,
        os_type=os_type,
        confirm=confirm_spend,
        printer=printer,
    )

    provisioned = client.provision_machine(
        display_name,
        os_type=os_type,
        ttl_minutes=ttl_minutes,  # auto-terminate: the machine cannot outlive this guard
    )
    machine = provisioned.data["machine"]
    machine_id = machine["id"]
    printer(
        f"provisioned {machine_id} ({machine['os_type']}, status={machine['status']}, "
        f"is_test={machine['is_test']}) ttl={ttl_minutes}min "
        f"(request_id={provisioned.request_id})"
    )
    started_at = clock()

    snapshot_id: str | None = None
    saved_screenshot: str | None = None
    terminal_excerpt: str | None = None
    file_roundtrip_ok = False
    stopped = False
    terminated = False
    try:
        # Re-arm the TTL explicitly: PATCH is how you extend/shrink the spend
        # guard on a machine that already exists (0 clears it -- never do that
        # in a cost-aware script).
        ttl_ack = client.set_machine_ttl(machine_id, ttl_minutes)
        printer(f"ttl re-armed at {ttl_minutes}min (request_id={ttl_ack.request_id})")

        _wait_until_running(
            client,
            machine_id,
            str(machine["status"]),
            poll_interval=poll_interval,
            max_polls=max_polls,
            sleep=sleep,
            printer=printer,
        )

        shot = client.machine_screenshot(machine_id)
        if screenshot_path is not None:
            screenshot_path.write_bytes(base64.b64decode(shot.data["image_b64"]))
            saved_screenshot = str(screenshot_path)
            printer(
                f"screenshot {shot.data['width']}x{shot.data['height']} saved to "
                f"{screenshot_path} (request_id={shot.request_id})"
            )

        click = client.machine_action(machine_id, "click", parameters={"x": 640, "y": 360})
        printer(f"action click: success={click.data['success']} (request_id={click.request_id})")
        typed = client.machine_action(
            machine_id, "type_text", parameters={"text": REMOTE_FILE_CONTENT}
        )
        printer(f"action type_text: success={typed.data['success']}")

        batch = client.machine_actions_batch(
            machine_id,
            steps=[
                {"command": "key_combo", "parameters": {"keys": ["ctrl", "a"]}},
                {"command": "wait", "parameters": {"ms": 250}},
                {"command": "key_press", "parameters": {"key": "escape"}},
            ],
            stop_on_error=True,
        )
        printer(
            f"batch: {batch.data['completed_count']} completed, "
            f"{batch.data['failed_count']} failed, aborted={batch.data['aborted']} "
            f"(request_id={batch.request_id})"
        )

        term = client.machine_terminal(machine_id, "echo coasty-cookbook && uname -a")
        terminal_output = term.data.get("output") or term.data.get("result") or ""
        terminal_excerpt = str(terminal_output)[:200]
        printer(f"terminal: {terminal_excerpt!r} (request_id={term.request_id})")

        client.machine_files(
            machine_id,
            "write",
            {"path": REMOTE_FILE_PATH, "content": REMOTE_FILE_CONTENT},
        )
        read_back = client.machine_files(machine_id, "read", {"path": REMOTE_FILE_PATH})
        # The docs do not pin the files-op response shape: some servers return
        # the content at the top level, others nest it under "result" like the
        # documented /actions envelope. Accept both.
        nested = read_back.data.get("result")
        read_content = nested.get("content") if isinstance(nested, dict) else None
        if read_content is None:
            read_content = read_back.data.get("content")
        file_roundtrip_ok = read_content == REMOTE_FILE_CONTENT
        printer(f"file write+read roundtrip ok={file_roundtrip_ok}")

        nav = client.machine_browser(machine_id, "navigate", parameters={"url": browser_url})
        printer(f"browser navigate -> {browser_url} (request_id={nav.request_id})")

        snap = client.snapshot_machine(machine_id)
        snapshot_id = snap.data["snapshot_id"]
        printer(
            f"snapshot {snapshot_id} taken -- 1 cr ($0.01), refunded on failure "
            f"(request_id={snap.request_id})"
        )
    finally:
        # Stop first (drops billing to 1 cr/hr) then terminate (billing ends).
        # Cleanup failures are reported, never silently swallowed, and never
        # mask the original in-flight exception.
        try:
            stop_ack = client.stop_machine(machine_id)
            stopped = True
            printer(f"stopped {machine_id} (request_id={stop_ack.request_id})")
        except CoastyError as exc:
            printer(f"WARNING: stop failed: {exc}")
        try:
            term_ack = client.terminate_machine(machine_id)
            terminated = True
            printer(f"terminated {machine_id} (request_id={term_ack.request_id})")
        except CoastyError as exc:
            printer(f"WARNING: terminate failed: {exc} -- the ttl_minutes guard still applies")

    elapsed_minutes = max(0.0, (clock() - started_at) / 60.0)
    runtime = cost.estimate_machine_runtime(
        os_type=os_type, state="running", minutes=elapsed_minutes
    )
    printer(cost.format_estimate(runtime, title="Actual runtime cost (running-state rate)"))
    return LifecycleReport(
        machine_id=machine_id,
        os_type=os_type,
        is_test_machine=bool(machine["is_test"]),
        provision_request_id=provisioned.request_id,
        snapshot_id=snapshot_id,
        screenshot_path=saved_screenshot,
        terminal_excerpt=terminal_excerpt,
        file_roundtrip_ok=file_roundtrip_ok,
        elapsed_minutes=elapsed_minutes,
        estimated_runtime_credits=runtime.credits,
        stopped=stopped,
        terminated=terminated,
    )


# ── CLI ────────────────────────────────────────────────────────────────────


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    parser.add_argument("--display-name", default=DEFAULT_DISPLAY_NAME)
    parser.add_argument("--os", dest="os_type", choices=("linux", "windows"), default="linux")
    parser.add_argument("--ttl-minutes", type=int, default=DEFAULT_TTL_MINUTES)
    parser.add_argument("--screenshot", type=Path, default=Path("ex08_screenshot.png"))
    parser.add_argument("--browser-url", default=DEFAULT_BROWSER_URL)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--max-polls", type=int, default=30)
    parser.add_argument("--confirm", action="store_true", help="consent to spend on a live key")
    args = parser.parse_args(argv)

    api_key = env.require_api_key()
    os_type: OsType = "windows" if args.os_type == "windows" else "linux"
    try:
        with CoastyClient(api_key=api_key) as client:
            report = run_lifecycle(
                client,
                display_name=args.display_name,
                os_type=os_type,
                ttl_minutes=args.ttl_minutes,
                screenshot_path=args.screenshot,
                browser_url=args.browser_url,
                confirm_spend=args.confirm or env.spend_confirmed(),
                poll_interval=args.poll_interval,
                max_polls=args.max_polls,
            )
    except SpendNotConfirmedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except CoastyError as exc:
        print(
            f"API error {exc.code} (request_id={exc.request_id}): {exc.message}",
            file=sys.stderr,
        )
        return 1
    print(
        f"done: machine={report.machine_id} snapshot={report.snapshot_id} "
        f"stopped={report.stopped} terminated={report.terminated} "
        f"runtime~{report.elapsed_minutes:.1f}min (~{report.estimated_runtime_credits} cr)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
