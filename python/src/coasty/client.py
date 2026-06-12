"""Synchronous, typed client for the Coasty Computer Use API.

- Auth via ``X-API-Key`` (constructor arg or ``COASTY_API_KEY``).
- Per-request timeout (default 60 s, overridable per call).
- Retries with exponential backoff + full jitter (base 0.5 s, cap 8 s,
  max 4 attempts) on 429/500/503/504 and transport errors, honoring
  ``Retry-After``. Other 4xx are never retried. POSTs are only retried when
  they are inherently safe (predict/ground/parse, charged-then-refunded on
  failure) or carry an ``Idempotency-Key``.
- Every response is wrapped in :class:`ApiResult`, surfacing
  ``X-Coasty-Request-Id``, ``X-Credits-Charged`` and ``X-Credits-Remaining``.
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Generic, TypeVar, cast

import httpx

from . import env
from .errors import CoastyConnectionError, error_from_response
from .sse import SSEEvent, iter_events_reconnecting
from .types import (
    BrowserOp,
    ConnectionDetails,
    CreateSessionResponse,
    CuaVersion,
    FileOp,
    GroundResponse,
    JsonObject,
    ListPage,
    MachineActionResult,
    MachineBatchResult,
    MachineLifecycleResponse,
    MachineProvider,
    MachineScreenshot,
    ModelsResponse,
    OnAwaitingHuman,
    OsType,
    ParseResponse,
    PredictResponse,
    ProvisionMachineResponse,
    Run,
    RunStatus,
    SessionAck,
    SessionInfo,
    SessionList,
    SessionPredictResponse,
    SnapshotResponse,
    TrajectoryStep,
    UsageResponse,
    Workflow,
    WorkflowRun,
    WorkflowStatus,
)

T = TypeVar("T")

USER_AGENT = "coasty-cookbook-python/0.1.0"
DEFAULT_TIMEOUT_SECONDS = 60.0
RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 503, 504})
# POSTs that are inherently safe to retry (charged-then-refunded on failure).
_SAFE_POST_PATHS: frozenset[str] = frozenset({"/predict", "/ground", "/parse"})


@dataclass(frozen=True)
class ApiResult(Generic[T]):
    """A parsed response payload plus Coasty's tracing/billing headers."""

    data: T
    status_code: int
    request_id: str | None
    credits_charged: int | None
    credits_remaining: int | None
    idempotent_replay: bool = False


def _int_header(headers: httpx.Headers, name: str) -> int | None:
    raw = headers.get(name)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _wrap(data: T, response: httpx.Response) -> ApiResult[T]:
    headers = response.headers
    return ApiResult(
        data=data,
        status_code=response.status_code,
        request_id=headers.get("X-Coasty-Request-Id"),
        credits_charged=_int_header(headers, "X-Credits-Charged"),
        credits_remaining=_int_header(headers, "X-Credits-Remaining"),
        idempotent_replay=headers.get("X-Coasty-Idempotent-Replay", "").lower() == "true",
    )


def _drop_none(mapping: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in mapping.items() if value is not None}


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Numeric ``Retry-After`` (header preferred, ``error.retry_after`` fallback)."""
    header = response.headers.get("Retry-After")
    if header is not None:
        try:
            return max(0.0, float(header))
        except ValueError:
            pass  # HTTP-date form: fall back to body / backoff
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            retry_after = error.get("retry_after")
            if isinstance(retry_after, int | float) and not isinstance(retry_after, bool):
                return max(0.0, float(retry_after))
    return None


class CoastyClient:
    """Thin synchronous client; every cookbook example imports this."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_attempts: int = 4,
        backoff_base: float = 0.5,
        backoff_cap: float = 8.0,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        key = api_key if api_key is not None else env.get_api_key()
        if not key:
            raise env.MissingAPIKeyError(
                "No API key: pass api_key=... or set COASTY_API_KEY "
                "(sk-coasty-test-... sandbox keys are free)."
            )
        self._api_key = key
        self._base_url = (base_url if base_url is not None else env.get_base_url()).rstrip("/")
        self._timeout = timeout
        self._max_attempts = max(1, max_attempts)
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap
        self._sleep = sleep
        self._rng = rng if rng is not None else random.Random()
        self._owns_http = http_client is None
        self._http = (
            http_client if http_client is not None else httpx.Client(timeout=httpx.Timeout(timeout))
        )

    # ── plumbing ───────────────────────────────────────────────────────────

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def is_sandbox(self) -> bool:
        """True when the configured key is a never-billing sandbox key."""
        return env.is_sandbox_key(self._api_key)

    def __repr__(self) -> str:
        masked = (
            f"{self._api_key[:14]}..." if len(self._api_key) > 14 else "***"
        )  # never expose the full key
        return f"CoastyClient(base_url={self._base_url!r}, api_key={masked!r})"

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> CoastyClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def _headers(
        self,
        *,
        idempotency_key: str | None = None,
        accept: str = "application/json",
        last_event_id: str | None = None,
    ) -> dict[str, str]:
        headers = {
            "X-API-Key": self._api_key,
            "Accept": accept,
            "User-Agent": USER_AGENT,
        }
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        if last_event_id is not None:
            headers["Last-Event-ID"] = last_event_id
        return headers

    def _is_retryable(self, method: str, path: str, idempotency_key: str | None) -> bool:
        if method.upper() != "POST":
            return True
        return idempotency_key is not None or path in _SAFE_POST_PATHS

    def _backoff_delay(self, attempt: int) -> float:
        """Full jitter: uniform(0, min(cap, base * 2**(attempt-1)))."""
        ceiling = min(self._backoff_cap, self._backoff_base * (2 ** (attempt - 1)))
        return self._rng.uniform(0.0, ceiling)

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        params: Mapping[str, str | int] | None = None,
        idempotency_key: str | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        url = self._base_url + path
        can_retry = self._is_retryable(method, path, idempotency_key)
        request_timeout = httpx.Timeout(timeout if timeout is not None else self._timeout)
        headers = self._headers(idempotency_key=idempotency_key)

        attempt = 1
        while True:
            try:
                response = self._http.request(
                    method,
                    url,
                    json=dict(json_body) if json_body is not None else None,
                    params=dict(params) if params else None,
                    headers=headers,
                    timeout=request_timeout,
                )
            except httpx.TransportError as exc:
                if can_retry and attempt < self._max_attempts:
                    self._sleep(self._backoff_delay(attempt))
                    attempt += 1
                    continue
                raise CoastyConnectionError(
                    f"{method} {path} failed after {attempt} attempt(s): {exc}"
                ) from exc

            if (
                response.status_code in RETRYABLE_STATUS_CODES
                and can_retry
                and attempt < self._max_attempts
            ):
                delay = _retry_after_seconds(response)
                if delay is None:
                    delay = self._backoff_delay(attempt)
                self._sleep(delay)
                attempt += 1
                continue

            if response.status_code >= 400:
                raise error_from_response(response)
            return response

    def _json(self, response: httpx.Response) -> ApiResult[JsonObject]:
        return _wrap(cast(JsonObject, response.json()), response)

    # ── core inference ─────────────────────────────────────────────────────

    def predict(
        self,
        screenshot: str,
        instruction: str,
        *,
        cua_version: CuaVersion | None = None,
        system_prompt: str | None = None,
        instructions: str | None = None,
        screen_width: int | None = None,
        screen_height: int | None = None,
        trajectory: Sequence[TrajectoryStep] | None = None,
        max_actions: int | None = None,
        tools: Sequence[str] | None = None,
        include_reasoning: bool | None = None,
        include_raw_code: bool | None = None,
        timeout: float | None = None,
    ) -> ApiResult[PredictResponse]:
        """POST /v1/predict -- stateless action prediction (5 cr + surcharges)."""
        body: dict[str, Any] = {"screenshot": screenshot, "instruction": instruction}
        body.update(
            _drop_none(
                {
                    "cua_version": cua_version,
                    "system_prompt": system_prompt,
                    "instructions": instructions,
                    "screen_width": screen_width,
                    "screen_height": screen_height,
                    "trajectory": list(trajectory) if trajectory is not None else None,
                    "max_actions": max_actions,
                    "tools": list(tools) if tools is not None else None,
                    "include_reasoning": include_reasoning,
                    "include_raw_code": include_raw_code,
                }
            )
        )
        response = self._request("POST", "/predict", json_body=body, timeout=timeout)
        return _wrap(cast(PredictResponse, response.json()), response)

    def ground(
        self,
        screenshot: str,
        element: str,
        *,
        screen_width: int | None = None,
        screen_height: int | None = None,
        timeout: float | None = None,
    ) -> ApiResult[GroundResponse]:
        """POST /v1/ground -- element description to (x, y) (3 cr, +1 HD)."""
        body: dict[str, Any] = {"screenshot": screenshot, "element": element}
        body.update(_drop_none({"screen_width": screen_width, "screen_height": screen_height}))
        response = self._request("POST", "/ground", json_body=body, timeout=timeout)
        return _wrap(cast(GroundResponse, response.json()), response)

    def parse(self, code: str, *, timeout: float | None = None) -> ApiResult[ParseResponse]:
        """POST /v1/parse -- pyautogui source to structured actions (free)."""
        response = self._request("POST", "/parse", json_body={"code": code}, timeout=timeout)
        return _wrap(cast(ParseResponse, response.json()), response)

    def models(self, *, timeout: float | None = None) -> ApiResult[ModelsResponse]:
        """GET /v1/models -- models, CUA versions, action types (free)."""
        response = self._request("GET", "/models", timeout=timeout)
        return _wrap(cast(ModelsResponse, response.json()), response)

    def usage(
        self, *, period: str | None = None, timeout: float | None = None
    ) -> ApiResult[UsageResponse]:
        """GET /v1/usage[?period=YYYY-MM] -- billing summary (free)."""
        params = _drop_none({"period": period})
        response = self._request("GET", "/usage", params=params or None, timeout=timeout)
        return _wrap(cast(UsageResponse, response.json()), response)

    # ── sessions ───────────────────────────────────────────────────────────

    def create_session(
        self,
        *,
        cua_version: CuaVersion | None = None,
        screen_width: int | None = None,
        screen_height: int | None = None,
        max_trajectory_length: int | None = None,
        system_prompt: str | None = None,
        instructions: str | None = None,
        tools: Sequence[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
        timeout: float | None = None,
    ) -> ApiResult[CreateSessionResponse]:
        """POST /v1/sessions -- create a stateful session (10 cr flat)."""
        body = _drop_none(
            {
                "cua_version": cua_version,
                "screen_width": screen_width,
                "screen_height": screen_height,
                "max_trajectory_length": max_trajectory_length,
                "system_prompt": system_prompt,
                "instructions": instructions,
                "tools": list(tools) if tools is not None else None,
                "metadata": dict(metadata) if metadata is not None else None,
            }
        )
        response = self._request(
            "POST", "/sessions", json_body=body, idempotency_key=idempotency_key, timeout=timeout
        )
        return _wrap(cast(CreateSessionResponse, response.json()), response)

    def session_predict(
        self,
        session_id: str,
        screenshot: str,
        instruction: str,
        *,
        include_reasoning: bool | None = None,
        include_raw_code: bool | None = None,
        idempotency_key: str | None = None,
        timeout: float | None = None,
    ) -> ApiResult[SessionPredictResponse]:
        """POST /v1/sessions/{id}/predict -- next step in a session (4 cr +).

        Pass an ``idempotency_key`` so a network retry can never
        double-execute a step (it also makes the call retryable).
        """
        body: dict[str, Any] = {"screenshot": screenshot, "instruction": instruction}
        body.update(
            _drop_none(
                {"include_reasoning": include_reasoning, "include_raw_code": include_raw_code}
            )
        )
        response = self._request(
            "POST",
            f"/sessions/{session_id}/predict",
            json_body=body,
            idempotency_key=idempotency_key,
            timeout=timeout,
        )
        return _wrap(cast(SessionPredictResponse, response.json()), response)

    def reset_session(
        self, session_id: str, *, timeout: float | None = None
    ) -> ApiResult[SessionAck]:
        """POST /v1/sessions/{id}/reset -- clear the trajectory (free)."""
        response = self._request(
            "POST", f"/sessions/{session_id}/reset", json_body={}, timeout=timeout
        )
        return _wrap(cast(SessionAck, response.json()), response)

    def get_session(
        self, session_id: str, *, timeout: float | None = None
    ) -> ApiResult[SessionInfo]:
        """GET /v1/sessions/{id} (free)."""
        response = self._request("GET", f"/sessions/{session_id}", timeout=timeout)
        return _wrap(cast(SessionInfo, response.json()), response)

    def list_sessions(self, *, timeout: float | None = None) -> ApiResult[SessionList]:
        """GET /v1/sessions (free)."""
        response = self._request("GET", "/sessions", timeout=timeout)
        return _wrap(cast(SessionList, response.json()), response)

    def delete_session(
        self, session_id: str, *, timeout: float | None = None
    ) -> ApiResult[SessionAck]:
        """DELETE /v1/sessions/{id} -- frees the concurrency slot (free)."""
        response = self._request("DELETE", f"/sessions/{session_id}", timeout=timeout)
        return _wrap(cast(SessionAck, response.json()), response)

    # ── task runs ──────────────────────────────────────────────────────────

    def create_run(
        self,
        machine_id: str,
        task: str,
        *,
        cua_version: CuaVersion | None = None,
        instructions: str | None = None,
        system_prompt: str | None = None,
        max_steps: int | None = None,
        deadline_seconds: int | None = None,
        on_awaiting_human: OnAwaitingHuman | None = None,
        awaiting_human_timeout_seconds: int | None = None,
        webhook_url: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
        timeout: float | None = None,
    ) -> ApiResult[Run]:
        """POST /v1/runs -- start a server-driven agent run.

        The response carries ``webhook_secret`` exactly ONCE (when a
        ``webhook_url`` is set) -- persist it immediately.
        """
        body: dict[str, Any] = {"machine_id": machine_id, "task": task}
        body.update(
            _drop_none(
                {
                    "cua_version": cua_version,
                    "instructions": instructions,
                    "system_prompt": system_prompt,
                    "max_steps": max_steps,
                    "deadline_seconds": deadline_seconds,
                    "on_awaiting_human": on_awaiting_human,
                    "awaiting_human_timeout_seconds": awaiting_human_timeout_seconds,
                    "webhook_url": webhook_url,
                    "metadata": dict(metadata) if metadata is not None else None,
                }
            )
        )
        response = self._request(
            "POST", "/runs", json_body=body, idempotency_key=idempotency_key, timeout=timeout
        )
        return _wrap(cast(Run, response.json()), response)

    def get_run(self, run_id: str, *, timeout: float | None = None) -> ApiResult[Run]:
        """GET /v1/runs/{id}."""
        response = self._request("GET", f"/runs/{run_id}", timeout=timeout)
        return _wrap(cast(Run, response.json()), response)

    def list_runs(
        self,
        *,
        status: RunStatus | None = None,
        limit: int | None = None,
        timeout: float | None = None,
    ) -> ApiResult[ListPage[Run]]:
        """GET /v1/runs?status=&limit= (limit default 20 server-side)."""
        params = _drop_none({"status": status, "limit": limit})
        response = self._request("GET", "/runs", params=params or None, timeout=timeout)
        return _wrap(cast("ListPage[Run]", response.json()), response)

    def cancel_run(self, run_id: str, *, timeout: float | None = None) -> ApiResult[Run]:
        """POST /v1/runs/{id}/cancel."""
        response = self._request("POST", f"/runs/{run_id}/cancel", json_body={}, timeout=timeout)
        return _wrap(cast(Run, response.json()), response)

    def resume_run(
        self, run_id: str, *, note: str | None = None, timeout: float | None = None
    ) -> ApiResult[Run]:
        """POST /v1/runs/{id}/resume -- only valid from ``awaiting_human``."""
        response = self._request(
            "POST",
            f"/runs/{run_id}/resume",
            json_body=_drop_none({"note": note}),
            timeout=timeout,
        )
        return _wrap(cast(Run, response.json()), response)

    def run_events(
        self,
        run_id: str,
        *,
        last_event_id: int | str | None = None,
        timeout: float | None = None,
        max_reconnects: int = 5,
        reconnect_delay: float = 0.5,
    ) -> Iterator[SSEEvent]:
        """GET /v1/runs/{id}/events -- durable SSE stream.

        Reconnects with ``Last-Event-ID`` after a drop (no loss/duplication)
        and stops cleanly after the ``done`` event.
        """
        return self._events(
            f"/runs/{run_id}/events",
            last_event_id=last_event_id,
            timeout=timeout,
            max_reconnects=max_reconnects,
            reconnect_delay=reconnect_delay,
        )

    # ── workflows ──────────────────────────────────────────────────────────

    def create_workflow(
        self,
        name: str,
        slug: str,
        definition: Mapping[str, Any],
        *,
        inputs_schema: Mapping[str, Any] | None = None,
        description: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> ApiResult[Workflow]:
        """POST /v1/workflows."""
        body: dict[str, Any] = {"name": name, "slug": slug, "definition": dict(definition)}
        body.update(
            _drop_none(
                {
                    "inputs_schema": dict(inputs_schema) if inputs_schema is not None else None,
                    "description": description,
                    "metadata": dict(metadata) if metadata is not None else None,
                }
            )
        )
        response = self._request("POST", "/workflows", json_body=body, timeout=timeout)
        return _wrap(cast(Workflow, response.json()), response)

    def get_workflow(
        self, workflow_id: str, *, timeout: float | None = None
    ) -> ApiResult[Workflow]:
        """GET /v1/workflows/{id}."""
        response = self._request("GET", f"/workflows/{workflow_id}", timeout=timeout)
        return _wrap(cast(Workflow, response.json()), response)

    def list_workflows(
        self, *, limit: int | None = None, timeout: float | None = None
    ) -> ApiResult[ListPage[Workflow]]:
        """GET /v1/workflows?limit= (default 20 server-side)."""
        params = _drop_none({"limit": limit})
        response = self._request("GET", "/workflows", params=params or None, timeout=timeout)
        return _wrap(cast("ListPage[Workflow]", response.json()), response)

    def update_workflow(
        self,
        workflow_id: str,
        *,
        name: str | None = None,
        definition: Mapping[str, Any] | None = None,
        inputs_schema: Mapping[str, Any] | None = None,
        description: str | None = None,
        status: WorkflowStatus | None = None,
        metadata: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> ApiResult[Workflow]:
        """PUT /v1/workflows/{id} -- bumps the workflow ``version``."""
        body = _drop_none(
            {
                "name": name,
                "definition": dict(definition) if definition is not None else None,
                "inputs_schema": dict(inputs_schema) if inputs_schema is not None else None,
                "description": description,
                "status": status,
                "metadata": dict(metadata) if metadata is not None else None,
            }
        )
        response = self._request(
            "PUT", f"/workflows/{workflow_id}", json_body=body, timeout=timeout
        )
        return _wrap(cast(Workflow, response.json()), response)

    def delete_workflow(
        self, workflow_id: str, *, timeout: float | None = None
    ) -> ApiResult[JsonObject]:
        """DELETE /v1/workflows/{id} -- archives the workflow."""
        response = self._request("DELETE", f"/workflows/{workflow_id}", timeout=timeout)
        return self._json(response)

    def _workflow_run_body(
        self,
        *,
        inputs: Mapping[str, Any] | None,
        machine_id: str | None,
        budget_cents: int | None,
        max_iterations: int | None,
        deadline_seconds: int | None,
        webhook_url: str | None,
        metadata: Mapping[str, Any] | None,
        definition: Mapping[str, Any] | None = None,
        inputs_schema: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return _drop_none(
            {
                "inputs": dict(inputs) if inputs is not None else None,
                "machine_id": machine_id,
                "budget_cents": budget_cents,
                "max_iterations": max_iterations,
                "deadline_seconds": deadline_seconds,
                "webhook_url": webhook_url,
                "metadata": dict(metadata) if metadata is not None else None,
                "definition": dict(definition) if definition is not None else None,
                "inputs_schema": dict(inputs_schema) if inputs_schema is not None else None,
            }
        )

    def start_workflow_run(
        self,
        workflow_id: str,
        *,
        inputs: Mapping[str, Any] | None = None,
        machine_id: str | None = None,
        budget_cents: int | None = None,
        max_iterations: int | None = None,
        deadline_seconds: int | None = None,
        webhook_url: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
        timeout: float | None = None,
    ) -> ApiResult[WorkflowRun]:
        """POST /v1/workflows/{id}/runs -- run a saved workflow."""
        body = self._workflow_run_body(
            inputs=inputs,
            machine_id=machine_id,
            budget_cents=budget_cents,
            max_iterations=max_iterations,
            deadline_seconds=deadline_seconds,
            webhook_url=webhook_url,
            metadata=metadata,
        )
        response = self._request(
            "POST",
            f"/workflows/{workflow_id}/runs",
            json_body=body,
            idempotency_key=idempotency_key,
            timeout=timeout,
        )
        return _wrap(cast(WorkflowRun, response.json()), response)

    def start_adhoc_workflow_run(
        self,
        definition: Mapping[str, Any],
        *,
        inputs: Mapping[str, Any] | None = None,
        inputs_schema: Mapping[str, Any] | None = None,
        machine_id: str | None = None,
        budget_cents: int | None = None,
        max_iterations: int | None = None,
        deadline_seconds: int | None = None,
        webhook_url: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
        timeout: float | None = None,
    ) -> ApiResult[WorkflowRun]:
        """POST /v1/workflows/runs -- ad-hoc run with an inline definition."""
        body = self._workflow_run_body(
            inputs=inputs,
            machine_id=machine_id,
            budget_cents=budget_cents,
            max_iterations=max_iterations,
            deadline_seconds=deadline_seconds,
            webhook_url=webhook_url,
            metadata=metadata,
            definition=definition,
            inputs_schema=inputs_schema,
        )
        response = self._request(
            "POST",
            "/workflows/runs",
            json_body=body,
            idempotency_key=idempotency_key,
            timeout=timeout,
        )
        return _wrap(cast(WorkflowRun, response.json()), response)

    def get_workflow_run(
        self, workflow_run_id: str, *, timeout: float | None = None
    ) -> ApiResult[WorkflowRun]:
        """GET /v1/workflows/runs/{id}."""
        response = self._request("GET", f"/workflows/runs/{workflow_run_id}", timeout=timeout)
        return _wrap(cast(WorkflowRun, response.json()), response)

    def list_workflow_runs(
        self,
        *,
        workflow_id: str | None = None,
        limit: int | None = None,
        timeout: float | None = None,
    ) -> ApiResult[ListPage[WorkflowRun]]:
        """GET /v1/workflows/runs?workflow_id=&limit=."""
        params = _drop_none({"workflow_id": workflow_id, "limit": limit})
        response = self._request("GET", "/workflows/runs", params=params or None, timeout=timeout)
        return _wrap(cast("ListPage[WorkflowRun]", response.json()), response)

    def cancel_workflow_run(
        self, workflow_run_id: str, *, timeout: float | None = None
    ) -> ApiResult[WorkflowRun]:
        """POST /v1/workflows/runs/{id}/cancel."""
        response = self._request(
            "POST", f"/workflows/runs/{workflow_run_id}/cancel", json_body={}, timeout=timeout
        )
        return _wrap(cast(WorkflowRun, response.json()), response)

    def resume_workflow_run(
        self,
        workflow_run_id: str,
        *,
        approved: bool,
        note: str | None = None,
        timeout: float | None = None,
    ) -> ApiResult[WorkflowRun]:
        """POST /v1/workflows/runs/{id}/resume -- approve/reject a paused step.

        ``approved=False`` rejects (fails) the pending ``human_approval``.
        """
        body: dict[str, Any] = {"approved": approved}
        if note is not None:
            body["note"] = note
        response = self._request(
            "POST", f"/workflows/runs/{workflow_run_id}/resume", json_body=body, timeout=timeout
        )
        return _wrap(cast(WorkflowRun, response.json()), response)

    def workflow_run_events(
        self,
        workflow_run_id: str,
        *,
        last_event_id: int | str | None = None,
        timeout: float | None = None,
        max_reconnects: int = 5,
        reconnect_delay: float = 0.5,
    ) -> Iterator[SSEEvent]:
        """GET /v1/workflows/runs/{id}/events -- SSE, same framing as runs."""
        return self._events(
            f"/workflows/runs/{workflow_run_id}/events",
            last_event_id=last_event_id,
            timeout=timeout,
            max_reconnects=max_reconnects,
            reconnect_delay=reconnect_delay,
        )

    # ── machines ───────────────────────────────────────────────────────────

    def provision_machine(
        self,
        display_name: str,
        *,
        os_type: OsType | None = None,
        desktop_enabled: bool | None = None,
        provider: MachineProvider | None = None,
        cpu_cores: int | None = None,
        memory_gb: int | None = None,
        storage_gb: int | None = None,
        restore_from_snapshot: bool | None = None,
        ttl_minutes: int | None = None,
        metadata: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
        timeout: float | None = None,
    ) -> ApiResult[ProvisionMachineResponse]:
        """POST /v1/machines -- provision a VM (test keys: instant sandbox VM)."""
        body: dict[str, Any] = {"display_name": display_name}
        body.update(
            _drop_none(
                {
                    "os_type": os_type,
                    "desktop_enabled": desktop_enabled,
                    "provider": provider,
                    "cpu_cores": cpu_cores,
                    "memory_gb": memory_gb,
                    "storage_gb": storage_gb,
                    "restore_from_snapshot": restore_from_snapshot,
                    "ttl_minutes": ttl_minutes,
                    "metadata": dict(metadata) if metadata is not None else None,
                }
            )
        )
        response = self._request(
            "POST", "/machines", json_body=body, idempotency_key=idempotency_key, timeout=timeout
        )
        return _wrap(cast(ProvisionMachineResponse, response.json()), response)

    def list_machines(
        self, *, limit: int | None = None, timeout: float | None = None
    ) -> ApiResult[JsonObject]:
        """GET /v1/machines?limit= (1-200, default 50)."""
        params = _drop_none({"limit": limit})
        response = self._request("GET", "/machines", params=params or None, timeout=timeout)
        return self._json(response)

    def machine_pricing(self, *, timeout: float | None = None) -> ApiResult[JsonObject]:
        """GET /v1/machines/pricing -- the live machine price table."""
        response = self._request("GET", "/machines/pricing", timeout=timeout)
        return self._json(response)

    def get_machine(
        self, machine_id: str, *, timeout: float | None = None
    ) -> ApiResult[JsonObject]:
        """GET /v1/machines/{id}."""
        response = self._request("GET", f"/machines/{machine_id}", timeout=timeout)
        return self._json(response)

    def terminate_machine(
        self, machine_id: str, *, timeout: float | None = None
    ) -> ApiResult[MachineLifecycleResponse]:
        """DELETE /v1/machines/{id} -- terminate (ends all billing)."""
        response = self._request("DELETE", f"/machines/{machine_id}", timeout=timeout)
        return _wrap(cast(MachineLifecycleResponse, response.json()), response)

    def start_machine(
        self, machine_id: str, *, timeout: float | None = None
    ) -> ApiResult[MachineLifecycleResponse]:
        """POST /v1/machines/{id}/start."""
        response = self._request(
            "POST", f"/machines/{machine_id}/start", json_body={}, timeout=timeout
        )
        return _wrap(cast(MachineLifecycleResponse, response.json()), response)

    def stop_machine(
        self, machine_id: str, *, timeout: float | None = None
    ) -> ApiResult[MachineLifecycleResponse]:
        """POST /v1/machines/{id}/stop -- drops to the 1 cr/hr storage rate."""
        response = self._request(
            "POST", f"/machines/{machine_id}/stop", json_body={}, timeout=timeout
        )
        return _wrap(cast(MachineLifecycleResponse, response.json()), response)

    def restart_machine(
        self, machine_id: str, *, timeout: float | None = None
    ) -> ApiResult[MachineLifecycleResponse]:
        """POST /v1/machines/{id}/restart."""
        response = self._request(
            "POST", f"/machines/{machine_id}/restart", json_body={}, timeout=timeout
        )
        return _wrap(cast(MachineLifecycleResponse, response.json()), response)

    def set_machine_ttl(
        self, machine_id: str, ttl_minutes: int, *, timeout: float | None = None
    ) -> ApiResult[JsonObject]:
        """PATCH /v1/machines/{id} -- update the auto-terminate TTL (0 clears)."""
        response = self._request(
            "PATCH",
            f"/machines/{machine_id}",
            json_body={"ttl_minutes": ttl_minutes},
            timeout=timeout,
        )
        return self._json(response)

    def snapshot_machine(
        self,
        machine_id: str,
        *,
        idempotency_key: str | None = None,
        timeout: float | None = None,
    ) -> ApiResult[SnapshotResponse]:
        """POST /v1/machines/{id}/snapshot -- 1 cr (refunded on failure)."""
        response = self._request(
            "POST",
            f"/machines/{machine_id}/snapshot",
            json_body={},
            idempotency_key=idempotency_key,
            timeout=timeout,
        )
        return _wrap(cast(SnapshotResponse, response.json()), response)

    def machine_screenshot(
        self, machine_id: str, *, timeout: float | None = None
    ) -> ApiResult[MachineScreenshot]:
        """GET /v1/machines/{id}/screenshot -- raw base64, predict-ready."""
        response = self._request("GET", f"/machines/{machine_id}/screenshot", timeout=timeout)
        return _wrap(cast(MachineScreenshot, response.json()), response)

    def machine_action(
        self,
        machine_id: str,
        command: str,
        *,
        parameters: Mapping[str, Any] | None = None,
        timeout_ms: int | None = None,
        timeout: float | None = None,
    ) -> ApiResult[MachineActionResult]:
        """POST /v1/machines/{id}/actions -- run one low-level action."""
        body: dict[str, Any] = {"command": command}
        body.update(
            _drop_none(
                {
                    "parameters": dict(parameters) if parameters is not None else None,
                    "timeout_ms": timeout_ms,
                }
            )
        )
        response = self._request(
            "POST", f"/machines/{machine_id}/actions", json_body=body, timeout=timeout
        )
        return _wrap(cast(MachineActionResult, response.json()), response)

    def machine_actions_batch(
        self,
        machine_id: str,
        steps: Sequence[Mapping[str, Any]],
        *,
        stop_on_error: bool = True,
        timeout: float | None = None,
    ) -> ApiResult[MachineBatchResult]:
        """POST /v1/machines/{id}/actions/batch -- up to 50 actions in order."""
        body: dict[str, Any] = {
            "steps": [dict(step) for step in steps],
            "stop_on_error": stop_on_error,
        }
        response = self._request(
            "POST", f"/machines/{machine_id}/actions/batch", json_body=body, timeout=timeout
        )
        return _wrap(cast(MachineBatchResult, response.json()), response)

    def machine_browser(
        self,
        machine_id: str,
        op: BrowserOp,
        *,
        parameters: Mapping[str, Any] | None = None,
        timeout_ms: int | None = None,
        timeout: float | None = None,
    ) -> ApiResult[JsonObject]:
        """POST /v1/machines/{id}/browser/{op} -- browser convenience wrapper."""
        body: dict[str, Any] = {"parameters": dict(parameters) if parameters is not None else {}}
        if timeout_ms is not None:
            body["timeout_ms"] = timeout_ms
        response = self._request(
            "POST", f"/machines/{machine_id}/browser/{op}", json_body=body, timeout=timeout
        )
        return self._json(response)

    def machine_terminal(
        self,
        machine_id: str,
        command: str,
        *,
        timeout_ms: int | None = None,
        session_id: str | None = None,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> ApiResult[JsonObject]:
        """POST /v1/machines/{id}/terminal -- shell command (terminal:exec)."""
        body: dict[str, Any] = {"command": command}
        body.update(_drop_none({"timeout_ms": timeout_ms, "session_id": session_id, "cwd": cwd}))
        response = self._request(
            "POST", f"/machines/{machine_id}/terminal", json_body=body, timeout=timeout
        )
        return self._json(response)

    def machine_files(
        self,
        machine_id: str,
        op: FileOp,
        parameters: Mapping[str, Any],
        *,
        timeout: float | None = None,
    ) -> ApiResult[JsonObject]:
        """POST /v1/machines/{id}/files/{op} -- file ops (files:read|write)."""
        body: dict[str, Any] = {"parameters": dict(parameters)}
        response = self._request(
            "POST", f"/machines/{machine_id}/files/{op}", json_body=body, timeout=timeout
        )
        return self._json(response)

    def machine_connection(
        self, machine_id: str, *, timeout: float | None = None
    ) -> ApiResult[ConnectionDetails]:
        """GET /v1/machines/{id}/connection -- HIGH-RISK secrets; never log."""
        response = self._request("GET", f"/machines/{machine_id}/connection", timeout=timeout)
        return _wrap(cast(ConnectionDetails, response.json()), response)

    # ── SSE plumbing ───────────────────────────────────────────────────────

    def _events(
        self,
        path: str,
        *,
        last_event_id: int | str | None,
        timeout: float | None,
        max_reconnects: int,
        reconnect_delay: float,
    ) -> Iterator[SSEEvent]:
        initial = str(last_event_id) if last_event_id is not None else None

        def open_stream(cursor: str | None) -> Iterator[str]:
            return self._stream_lines(path, last_event_id=cursor, timeout=timeout)

        return iter_events_reconnecting(
            open_stream,
            last_event_id=initial,
            max_reconnects=max_reconnects,
            reconnect_delay=reconnect_delay,
            sleep=self._sleep,
        )

    def _stream_lines(
        self, path: str, *, last_event_id: str | None, timeout: float | None
    ) -> Iterator[str]:
        headers = self._headers(accept="text/event-stream", last_event_id=last_event_id)
        request_timeout = httpx.Timeout(timeout if timeout is not None else self._timeout)
        with self._http.stream(
            "GET", self._base_url + path, headers=headers, timeout=request_timeout
        ) as response:
            if response.status_code >= 400:
                response.read()
                raise error_from_response(response)
            yield from response.iter_lines()
