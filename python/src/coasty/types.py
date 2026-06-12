"""Typed request/response shapes for the Coasty Computer Use API.

These TypedDicts mirror the field tables in ``docs/API_NOTES.md`` and the
canonical ``.llms.txt`` reference. They document the wire shapes; responses
are ``cast`` to them (no runtime validation), so optional/nullable fields are
marked ``NotRequired`` where the docs allow omission.
"""

from __future__ import annotations

from typing import Any, Generic, Literal, NotRequired, TypedDict, TypeVar

JsonObject = dict[str, Any]
"""A free-form JSON object (used where the docs do not pin an exact shape)."""

CuaVersion = Literal["v1", "v3", "v4"]
PredictStatus = Literal["continue", "done", "fail"]
RunStatus = Literal[
    "queued", "running", "awaiting_human", "succeeded", "failed", "cancelled", "timed_out"
]
OnAwaitingHuman = Literal["pause", "fail", "cancel"]
OsType = Literal["linux", "windows"]
MachineProvider = Literal["aws", "azure", "auto"]
WorkflowStatus = Literal["active", "archived"]
MachineStatus = Literal[
    "creating",
    "starting",
    "running",
    "stopping",
    "stopped",
    "restarting",
    "suspended",
    "suspended_for_billing",
    "error",
    "terminated",
]
ActionType = Literal[
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
    "raw",
]
RunEventType = Literal[
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
]
BrowserOp = Literal[
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
]
FileOp = Literal[
    # files:read ops
    "read",
    "exists",
    "list",
    "list-directory",
    "download",
    "list-downloads",
    # files:write ops
    "write",
    "edit",
    "append",
    "delete",
    "delete-directory",
]

TERMINAL_RUN_STATUSES: frozenset[str] = frozenset({"succeeded", "failed", "cancelled", "timed_out"})

ItemT = TypeVar("ItemT")


class Usage(TypedDict):
    """Token + billing usage attached to every billed inference response."""

    input_tokens: int
    output_tokens: int
    credits_charged: int
    cost_cents: int


class Action(TypedDict):
    """One structured GUI action returned by predict/parse."""

    action_type: str
    params: dict[str, Any]
    description: NotRequired[str | None]
    raw_code: NotRequired[str | None]


class TrajectoryStep(TypedDict):
    """A prior step you attach to /predict for context (+2 cr each)."""

    screenshot: str
    actions: list[Action]
    reasoning: NotRequired[str | None]


class PredictResponse(TypedDict):
    request_id: str
    status: PredictStatus
    reasoning: NotRequired[str | None]
    actions: list[Action]
    raw_code: NotRequired[list[str]]
    usage: Usage


class SessionPredictResponse(PredictResponse):
    session_id: str
    step: int


class CreateSessionResponse(TypedDict):
    session_id: str
    cua_version: CuaVersion
    screen_size: str
    created_at: str
    expires_at: str


class SessionInfo(TypedDict):
    session_id: str
    cua_version: CuaVersion
    screen_size: str
    step_count: int
    created_at: str
    expires_at: str
    total_credits_used: int


class SessionList(TypedDict):
    sessions: list[SessionInfo]


class SessionAck(TypedDict):
    """Response of session reset/delete."""

    status: Literal["ok"]
    session_id: str


class GroundResponse(TypedDict):
    x: int
    y: int
    usage: Usage


class ParseResponse(TypedDict):
    actions: list[Action]


class RunResult(TypedDict):
    passed: bool
    status: str
    summary: str
    verdict: NotRequired[str | None]


class RunErrorInfo(TypedDict):
    code: str
    message: str


class Run(TypedDict):
    """The ``agent.run`` object returned by create/get/list."""

    id: str
    object: Literal["agent.run"]
    status: RunStatus
    machine_id: str
    task: str
    cua_version: str
    instructions: str | None
    max_steps: int
    on_awaiting_human: OnAwaitingHuman
    steps_completed: int
    credits_charged: int
    cost_cents: int
    result: RunResult | None
    error: RunErrorInfo | None
    awaiting_human_reason: str | None
    metadata: NotRequired[dict[str, Any] | None]
    webhook_url: NotRequired[str | None]
    webhook_secret: NotRequired[str | None]  # returned ONCE on create, null afterwards
    created_at: str | None
    started_at: str | None
    awaiting_human_since: NotRequired[str | None]
    finished_at: str | None
    request_id: NotRequired[str | None]


class ListPage(TypedDict, Generic[ItemT]):
    """``{object: "list", data, has_more, request_id}`` list envelope."""

    object: Literal["list"]
    data: list[ItemT]
    has_more: bool
    request_id: NotRequired[str | None]


class Workflow(TypedDict):
    id: str
    object: Literal["workflow"]
    name: str
    slug: str
    version: int
    dsl_version: str
    definition: dict[str, Any]
    inputs_schema: NotRequired[dict[str, Any] | None]
    description: NotRequired[str | None]
    status: WorkflowStatus
    metadata: NotRequired[dict[str, Any] | None]
    created_at: NotRequired[str | None]
    updated_at: NotRequired[str | None]
    request_id: NotRequired[str | None]


class WorkflowRun(TypedDict):
    """The ``workflow.run`` object."""

    id: str
    object: Literal["workflow.run"]
    status: RunStatus
    workflow_id: str | None
    workflow_version: int | None
    machine_id: str | None
    inputs: dict[str, Any]
    output: dict[str, Any] | None
    error: RunErrorInfo | None
    awaiting_human_reason: str | None
    awaiting_step_id: str | None
    iterations_used: int
    spent_cents: int
    budget_cents: int
    webhook_url: NotRequired[str | None]
    webhook_secret: NotRequired[str | None]
    metadata: NotRequired[dict[str, Any] | None]
    created_at: str | None
    started_at: str | None
    finished_at: str | None
    request_id: NotRequired[str | None]


class Machine(TypedDict):
    id: str
    display_name: str
    status: MachineStatus
    os_type: OsType
    provider: str
    desktop_enabled: bool
    cpu_cores: int | None
    memory_gb: float | None
    storage_gb: int | None
    public_ip: str | None
    is_test: bool
    created_at: str
    metadata: NotRequired[dict[str, Any] | None]


class MachineConnectionInfo(TypedDict):
    """Public connection info returned alongside a provisioned machine."""

    public_ip: str | None
    ssh_port: int
    ssh_username: str
    vnc_port: int
    websocket_port: int
    has_ssh_key: bool
    has_vnc_password: bool


class ProvisionMachineResponse(TypedDict):
    machine: Machine
    connection: MachineConnectionInfo
    request_id: str


class MachineLifecycleResponse(TypedDict):
    """Response of start/stop/restart/terminate."""

    machine_id: str
    status: str
    message: str
    request_id: str


class SnapshotResponse(TypedDict):
    machine_id: str
    snapshot_id: str
    name: str
    created_at: str
    credits_charged: int
    request_id: str


class MachineScreenshot(TypedDict):
    machine_id: str
    image_b64: str  # raw base64, no data: prefix -- feed straight into /predict
    mime_type: str
    width: int
    height: int
    captured_at: str
    request_id: str


class MachineActionResult(TypedDict):
    machine_id: str
    command: str
    success: bool
    result: Any
    error: str | None
    duration_ms: int
    screenshot: NotRequired[str | None]
    request_id: str


class MachineBatchResult(TypedDict):
    machine_id: str
    results: list[dict[str, Any]]
    completed_count: int
    failed_count: int
    aborted: bool
    request_id: str


class ConnectionDetails(TypedDict):
    """HIGH-RISK secrets from GET /machines/{id}/connection (no-store)."""

    ssh_private_key_pem: NotRequired[str | None]
    vnc_password: NotRequired[str | None]
    websocket_url: NotRequired[str | None]
    devtools_url: NotRequired[str | None]


class ModelsResponse(TypedDict):
    models: list[dict[str, Any]]
    cua_versions: list[dict[str, Any]]
    action_types: list[str]


class UsageBreakdownEntry(TypedDict):
    requests: int
    credits: int


class UsageResponse(TypedDict):
    period: str
    total_requests: int
    total_credits: int
    total_cost_cents: int
    breakdown: dict[str, UsageBreakdownEntry]
    balance: int
    wallet_balance_cents: int
    wallet_balance_usd: float


class ErrorBody(TypedDict):
    """The documented error envelope's inner object."""

    code: str
    message: str
    type: str
    request_id: NotRequired[str | None]
    suggestion: NotRequired[str | None]
    docs_url: NotRequired[str | None]
    support: NotRequired[str | None]


class ErrorEnvelope(TypedDict):
    error: ErrorBody
