"""Example 09 -- The error-handling matrix: every typed failure, deliberately.

Purpose
    Trigger each documented error class on purpose and show exactly what the
    shared client raises (typed exception, ``.code``, ``.request_id``,
    context extras) and what it RETRIED vs surfaced immediately:

    - 401 INVALID_API_KEY        -> AuthenticationError      (never retried)
    - 403 INSUFFICIENT_SCOPE     -> InsufficientScopeError   (never retried)
    - 402 INSUFFICIENT_CREDITS   -> InsufficientCreditsError (never retried;
      prints required vs balance and a top-up suggestion)
    - 422 VALIDATION_ERROR       -> ValidationError          (never retried)
    - 404 RUN_NOT_FOUND          -> NotFoundError            (never retried)
    - 409 NOT_AWAITING_HUMAN     -> ConflictError            (never retried)
    - 429 RATE_LIMITED           -> client honors Retry-After, then recovers
    - 503/500 server errors      -> retried w/ backoff, then ServerError
    - 500 on a POST WITHOUT an Idempotency-Key -> NOT retried (safety guard)

Flow
    Each scenario is a small function taking ``(base_url, api_key)``; it
    builds its own client with a recording (non-sleeping) ``sleep`` and a
    request-counting hook, performs a doomed (or recovering) call, and
    returns a :class:`ScenarioResult` row. ``run_all`` renders the matrix.
    Tests point ``base_url`` at respx mocks; against the real production API
    this example only PRINTS the matrix (which scenarios are live-safe) and
    never fires requests.

    Some failures (403 scope, 402 wallet, forced 5xx) cannot be triggered
    organically on a healthy server. Against the cookbook's local mock
    server (``make mock``) those scenarios send the documented
    ``X-Mock-Force-Error: <CODE>`` test hook; ``main`` probes for that
    support with one free request and skips the forceable scenarios when
    the server does not honor it. The 429-then-recover demo needs a
    scripted 429->200 sequence, so it always defers to the test suite.

Endpoints
    GET /v1/models, POST /v1/predict, POST /v1/runs, GET /v1/runs/{id},
    POST /v1/runs/{id}/resume, POST /v1/machines/{id}/terminal

Estimated cost
    $0.00 in practice -- every scenario is designed to FAIL (and predict
    charges are auto-refunded on failure; see coasty.cost / the pricing
    table). No spend gate needed: nothing here can complete a billable op
    except the 429-recovery demo, which only runs against injected mocks.
"""

from __future__ import annotations

import argparse
import random
import sys
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import httpx

from coasty import (
    CoastyClient,
    CoastyError,
    InsufficientCreditsError,
    InsufficientScopeError,
    env,
)

# Obviously-fake keys: scenarios never touch the real API.
DEMO_API_KEY = "sk-coasty-test-" + "0" * 48
# Malformed prefix: rejected as 401 by ANY conforming server (the documented
# key families are sk-coasty-live-/sk-coasty-test-/cua_sk_ only).
BAD_API_KEY = "sk-coasty-bogus-" + "f" * 48
FAKE_SCREENSHOT_B64 = "iVBORw0KGgoAAAANSUhEUg" + "A" * 120  # >100 chars, no data: prefix
FORCE_ERROR_HEADER = "X-Mock-Force-Error"  # cookbook mock-server test hook


@dataclass(frozen=True)
class ScenarioResult:
    """One row of the matrix: what happened when we poked the failure."""

    name: str
    outcome: str  # "raised" | "recovered"
    exception: str | None
    code: str | None
    request_id: str | None
    status_code: int | None
    attempts: int
    slept: tuple[float, ...]
    detail: str

    @property
    def client_retried(self) -> bool:
        # The client always backs off (sleeps) between retry attempts, so
        # recorded sleeps -- not request count -- distinguish a retry from a
        # scenario that legitimately makes several different requests.
        return len(self.slept) > 0


ScenarioFn = Callable[[str, str, "dict[str, str] | None"], ScenarioResult]


@dataclass(frozen=True)
class Scenario:
    """A named, self-contained failure demonstration."""

    name: str
    description: str
    safe_live: bool  # free + read-only: OK to fire at the real API
    run: ScenarioFn
    # Error code to force via the mock server's X-Mock-Force-Error hook when
    # the failure cannot be triggered organically (None = organic scenario).
    force_error: str | None = None
    # True when only a scripted response sequence (e.g. 429 then 200) can
    # demonstrate the behavior -- always deferred to the test suite.
    needs_script: bool = False


def _instrumented_client(
    base_url: str, api_key: str, extra_headers: dict[str, str] | None = None
) -> tuple[CoastyClient, httpx.Client, list[float], list[str]]:
    """A client whose sleeps are recorded (never real) and requests counted."""
    sleeps: list[float] = []
    requests_seen: list[str] = []

    def _count(request: httpx.Request) -> None:
        requests_seen.append(f"{request.method} {request.url.path}")

    http_client = httpx.Client(event_hooks={"request": [_count]}, headers=extra_headers or {})
    client = CoastyClient(
        api_key=api_key,
        base_url=base_url,
        http_client=http_client,
        sleep=sleeps.append,  # deterministic: record, never block
        rng=random.Random(7),  # deterministic backoff jitter
    )
    return client, http_client, sleeps, requests_seen


def _run_one(
    name: str,
    base_url: str,
    api_key: str,
    call: Callable[[CoastyClient], str],
    *,
    detail_for: Callable[[CoastyError], str] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> ScenarioResult:
    """Run one doomed (or recovering) call and capture the evidence."""
    client, http_client, sleeps, requests_seen = _instrumented_client(
        base_url, api_key, extra_headers
    )
    try:
        recovered_detail = call(client)
    except CoastyError as exc:
        return ScenarioResult(
            name=name,
            outcome="raised",
            exception=type(exc).__name__,
            code=exc.code,
            request_id=exc.request_id,
            status_code=exc.status_code,
            attempts=len(requests_seen),
            slept=tuple(sleeps),
            detail=detail_for(exc) if detail_for is not None else exc.message,
        )
    finally:
        client.close()
        http_client.close()
    return ScenarioResult(
        name=name,
        outcome="recovered",
        exception=None,
        code=None,
        request_id=None,
        status_code=200,
        attempts=len(requests_seen),
        slept=tuple(sleeps),
        detail=recovered_detail,
    )


# ── scenario functions ─────────────────────────────────────────────────────


def scenario_invalid_api_key(
    base_url: str, api_key: str, extra_headers: dict[str, str] | None = None
) -> ScenarioResult:
    """401: a bad key on a free endpoint. AuthenticationError, no retry."""
    del api_key  # this scenario deliberately uses a (fake) bad key

    def call(client: CoastyClient) -> str:
        client.models()
        return "unexpected success"

    return _run_one("401 INVALID_API_KEY", base_url, BAD_API_KEY, call, extra_headers=extra_headers)


def scenario_insufficient_scope(
    base_url: str, api_key: str, extra_headers: dict[str, str] | None = None
) -> ScenarioResult:
    """403: key lacks terminal:exec. Shows required_scope vs current_scopes."""

    def call(client: CoastyClient) -> str:
        client.machine_terminal("mch_demo", "whoami")
        return "unexpected success"

    def detail(exc: CoastyError) -> str:
        if isinstance(exc, InsufficientScopeError):
            return (
                f"needs scope {exc.required_scope!r}, key has {exc.current_scopes!r} -- "
                "mint a key with the right scopes"
            )
        return exc.message

    return _run_one(
        "403 INSUFFICIENT_SCOPE",
        base_url,
        api_key,
        call,
        detail_for=detail,
        extra_headers=extra_headers,
    )


def scenario_insufficient_credits(
    base_url: str, api_key: str, extra_headers: dict[str, str] | None = None
) -> ScenarioResult:
    """402: wallet cannot cover the op. Prints required vs balance + top-up."""

    def call(client: CoastyClient) -> str:
        client.predict(FAKE_SCREENSHOT_B64, "open the settings page")
        return "unexpected success"

    def detail(exc: CoastyError) -> str:
        if isinstance(exc, InsufficientCreditsError):
            required = exc.required if exc.required is not None else 0
            balance = exc.balance if exc.balance is not None else 0
            shortfall = max(0, required - balance)
            return (
                f"required={required} cr, balance={balance} cr -- "
                f"top up at least ${shortfall / 100:.2f} to proceed"
            )
        return exc.message

    return _run_one(
        "402 INSUFFICIENT_CREDITS",
        base_url,
        api_key,
        call,
        detail_for=detail,
        extra_headers=extra_headers,
    )


def scenario_validation_error(
    base_url: str, api_key: str, extra_headers: dict[str, str] | None = None
) -> ScenarioResult:
    """422: an invalid body (empty instruction / short screenshot). No retry."""

    def call(client: CoastyClient) -> str:
        client.predict("too-short", "")
        return "unexpected success"

    return _run_one("422 VALIDATION_ERROR", base_url, api_key, call, extra_headers=extra_headers)


def scenario_not_found(
    base_url: str, api_key: str, extra_headers: dict[str, str] | None = None
) -> ScenarioResult:
    """404: ids are mode-isolated; a missing run raises NotFoundError."""

    def call(client: CoastyClient) -> str:
        client.get_run("run_does_not_exist")
        return "unexpected success"

    return _run_one("404 RUN_NOT_FOUND", base_url, api_key, call, extra_headers=extra_headers)


def scenario_not_awaiting_human(
    base_url: str, api_key: str, extra_headers: dict[str, str] | None = None
) -> ScenarioResult:
    """409: resume is only valid from awaiting_human. Shows current_state.

    Organic flow: create a run (which starts ``queued``/``running``) and
    immediately try to resume it -- the server must refuse with
    NOT_AWAITING_HUMAN because nothing is paused. Two requests, no retries.
    """

    def call(client: CoastyClient) -> str:
        created = client.create_run(
            "mch_demo",
            "reconcile the invoices (this run is never resumed from a pause)",
            idempotency_key=f"ex09-409-{uuid.uuid4().hex[:8]}",
        )
        client.resume_run(str(created.data["id"]))
        return "unexpected success"

    def detail(exc: CoastyError) -> str:
        current = exc.extras.get("current_state")
        allowed = exc.extras.get("allowed_from")
        return f"run is {current!r}; resume allowed only from {allowed!r}"

    return _run_one(
        "409 NOT_AWAITING_HUMAN",
        base_url,
        api_key,
        call,
        detail_for=detail,
        extra_headers=extra_headers,
    )


def scenario_rate_limited_recovers(
    base_url: str, api_key: str, extra_headers: dict[str, str] | None = None
) -> ScenarioResult:
    """429 then 200: the client sleeps exactly Retry-After, then succeeds.

    /predict is safe to retry (charged-then-refunded on failure), so the
    client transparently honors the server's pacing.
    """

    def call(client: CoastyClient) -> str:
        result = client.predict(FAKE_SCREENSHOT_B64, "open the settings page")
        return (
            f"recovered after rate limit (request_id={result.request_id}, "
            f"credits_charged={result.credits_charged})"
        )

    return _run_one(
        "429 RATE_LIMITED (recovers)", base_url, api_key, call, extra_headers=extra_headers
    )


def scenario_server_errors_surface(
    base_url: str, api_key: str, extra_headers: dict[str, str] | None = None
) -> ScenarioResult:
    """Persistent 5xx: retried with backoff (Retry-After honored), then raised.

    Failed predictions are charged-then-auto-refunded, so the surfaced
    ServerError costs nothing.
    """

    def call(client: CoastyClient) -> str:
        client.predict(FAKE_SCREENSHOT_B64, "open the settings page")
        return "unexpected success"

    return _run_one(
        "503/500 retry-then-surface", base_url, api_key, call, extra_headers=extra_headers
    )


def scenario_unsafe_post_not_retried(
    base_url: str, api_key: str, extra_headers: dict[str, str] | None = None
) -> ScenarioResult:
    """500 on POST /runs WITHOUT an Idempotency-Key: surfaced on attempt 1.

    The client only retries POSTs that are inherently safe (predict/ground/
    parse) or carry an Idempotency-Key -- otherwise a retry could start the
    same run twice.
    """

    def call(client: CoastyClient) -> str:
        client.create_run("mch_demo", "reconcile the invoices")  # no idempotency_key
        return "unexpected success"

    return _run_one(
        "500 unsafe POST (no retry)", base_url, api_key, call, extra_headers=extra_headers
    )


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "401 INVALID_API_KEY",
        "Bad key on GET /models -> AuthenticationError; 4xx are never retried.",
        safe_live=True,
        run=scenario_invalid_api_key,
    ),
    Scenario(
        "403 INSUFFICIENT_SCOPE",
        "terminal:exec missing -> InsufficientScopeError with required_scope.",
        safe_live=False,
        run=scenario_insufficient_scope,
        force_error="INSUFFICIENT_SCOPE",
    ),
    Scenario(
        "402 INSUFFICIENT_CREDITS",
        "Wallet too small for /predict -> required vs balance + top-up hint.",
        safe_live=False,
        run=scenario_insufficient_credits,
        force_error="INSUFFICIENT_CREDITS",
    ),
    Scenario(
        "422 VALIDATION_ERROR",
        "Empty instruction + short screenshot -> ValidationError with details.",
        safe_live=True,
        run=scenario_validation_error,
    ),
    Scenario(
        "404 RUN_NOT_FOUND",
        "GET a run id that does not exist -> NotFoundError.",
        safe_live=True,
        run=scenario_not_found,
    ),
    Scenario(
        "409 NOT_AWAITING_HUMAN",
        "Resume a run that is not paused -> ConflictError with current_state.",
        safe_live=False,
        run=scenario_not_awaiting_human,
    ),
    Scenario(
        "429 RATE_LIMITED (recovers)",
        "429 with Retry-After then 200 -> the client sleeps exactly that long.",
        safe_live=False,
        run=scenario_rate_limited_recovers,
        needs_script=True,
    ),
    Scenario(
        "503/500 retry-then-surface",
        "Persistent 5xx -> backoff retries (max 4 attempts) then ServerError.",
        safe_live=False,
        run=scenario_server_errors_surface,
        force_error="UPSTREAM_UNAVAILABLE",
    ),
    Scenario(
        "500 unsafe POST (no retry)",
        "POST /runs without Idempotency-Key -> surfaced on the first attempt.",
        safe_live=False,
        run=scenario_unsafe_post_not_retried,
        force_error="INTERNAL_ERROR",
    ),
)


def _skipped(scenario: Scenario, why: str) -> ScenarioResult:
    return ScenarioResult(
        name=scenario.name,
        outcome="skipped",
        exception=None,
        code=None,
        request_id=None,
        status_code=None,
        attempts=0,
        slept=(),
        detail=why,
    )


def run_all(
    base_url: str, api_key: str = DEMO_API_KEY, *, mock_hooks: bool = False
) -> list[ScenarioResult]:
    """Run every scenario against ``base_url`` (a mock or local mock server).

    ``mock_hooks=True`` means the server honors the cookbook mock's
    ``X-Mock-Force-Error`` header, which lets us trigger the failures that a
    healthy server never produces organically (403/402/forced 5xx).
    """
    rows: list[ScenarioResult] = []
    for scenario in SCENARIOS:
        if scenario.needs_script:
            rows.append(
                _skipped(scenario, "needs a scripted 429->200 sequence; see the respx tests")
            )
        elif scenario.force_error is not None and not mock_hooks:
            rows.append(
                _skipped(
                    scenario,
                    f"cannot trigger organically; needs {FORCE_ERROR_HEADER} support "
                    "(run the cookbook mock server) or the respx tests",
                )
            )
        elif scenario.force_error is not None:
            rows.append(scenario.run(base_url, api_key, {FORCE_ERROR_HEADER: scenario.force_error}))
        else:
            rows.append(scenario.run(base_url, api_key, None))
    return rows


def format_results(results: Sequence[ScenarioResult]) -> str:
    """Render the matrix: exception, code, request_id, attempts, sleeps."""
    lines = [
        f"{'scenario':<28} {'outcome':<10} {'exception':<26} {'code':<22} "
        f"{'attempts':>8} {'request_id':<14} detail"
    ]
    for row in results:
        retry_note = f"{row.attempts}{'*' if row.client_retried else ''}"
        lines.append(
            f"{row.name:<28} {row.outcome:<10} {row.exception or '-':<26} "
            f"{row.code or '-':<22} {retry_note:>8} {row.request_id or '-':<14} {row.detail}"
        )
    lines.append("(* = the client retried; sleeps were recorded, never real)")
    return "\n".join(lines)


def format_catalog() -> str:
    """The static matrix + live-safety notes (printed in live mode)."""
    lines = ["Error-handling matrix (live mode: scenarios are NOT fired at production):"]
    for scenario in SCENARIOS:
        safety = "live-safe (free, read-only)" if scenario.safe_live else "mock-only"
        lines.append(f"  - {scenario.name:<28} [{safety}] {scenario.description}")
    lines.append("Point COASTY_BASE_URL at the local mock server (make mock) to execute them all.")
    return "\n".join(lines)


# ── CLI ────────────────────────────────────────────────────────────────────


def _supports_mock_hooks(base_url: str, api_key: str) -> bool:
    """One free probe: does this server honor the X-Mock-Force-Error hook?

    GET /models is free; a server that honors the hook must answer with the
    forced NOT_FOUND envelope instead of the model list.
    """
    try:
        response = httpx.get(
            f"{base_url}/models",
            headers={"X-API-Key": api_key, FORCE_ERROR_HEADER: "NOT_FOUND"},
            timeout=10.0,
        )
        body = response.json()
    except (httpx.HTTPError, ValueError):
        return False
    error = body.get("error") if isinstance(body, dict) else None
    return (
        response.status_code == 404 and isinstance(error, dict) and error.get("code") == "NOT_FOUND"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    parser.add_argument(
        "--base-url",
        default=None,
        help="override the target (default: COASTY_BASE_URL or the production URL)",
    )
    args = parser.parse_args(argv)

    base_url = (args.base_url or env.get_base_url()).rstrip("/")
    if base_url == env.DEFAULT_BASE_URL:
        # Never fire deliberate failures at production: just print the matrix.
        print(format_catalog())
        return 0

    api_key = env.get_api_key() or DEMO_API_KEY
    mock_hooks = _supports_mock_hooks(base_url, api_key)
    print(
        f"{FORCE_ERROR_HEADER} hook: "
        + ("supported -- executing forceable scenarios too" if mock_hooks else "not supported")
    )
    try:
        results = run_all(base_url, api_key, mock_hooks=mock_hooks)
    except CoastyError as exc:  # a scenario failed in an UNexpected way
        print(
            f"unexpected API error {exc.code} (request_id={exc.request_id}): {exc.message}",
            file=sys.stderr,
        )
        return 1
    print(format_results(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
