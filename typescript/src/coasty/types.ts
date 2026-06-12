/**
 * Precise request/response types for the Coasty Computer Use API.
 * Field tables mirror the canonical reference (`.llms.txt` at the repo root).
 */
import { type WorkflowDefinition } from './dsl.js';

// ---------------------------------------------------------------------------
// Shared literals
// ---------------------------------------------------------------------------

export type CuaVersion = 'v1' | 'v3' | 'v4';
export type PredictStatus = 'continue' | 'done' | 'fail';
export type OnAwaitingHuman = 'pause' | 'fail' | 'cancel';

export type RunStatus =
  | 'queued'
  | 'running'
  | 'awaiting_human'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  | 'timed_out';

export const TERMINAL_RUN_STATUSES = ['succeeded', 'failed', 'cancelled', 'timed_out'] as const;
export type TerminalRunStatus = (typeof TERMINAL_RUN_STATUSES)[number];

export function isTerminalRunStatus(status: RunStatus): status is TerminalRunStatus {
  return (TERMINAL_RUN_STATUSES as readonly string[]).includes(status);
}

export type RunEventType =
  | 'status'
  | 'text'
  | 'reasoning'
  | 'tool_call'
  | 'tool_result'
  | 'awaiting_human'
  | 'resumed'
  | 'step'
  | 'billing'
  | 'error'
  | 'done';

/**
 * Action types the model can return. `raw` only appears in the local-automation
 * docs section; executors must never run it by default (see executor.ts).
 */
export type ActionType =
  | 'click'
  | 'type_text'
  | 'key_press'
  | 'key_combo'
  | 'scroll'
  | 'drag'
  | 'move'
  | 'wait'
  | 'done'
  | 'fail'
  | 'raw';

export interface Action {
  action_type: ActionType;
  params: Record<string, unknown>;
  description?: string | null;
  raw_code?: string | null;
}

export interface Usage {
  input_tokens: number;
  output_tokens: number;
  credits_charged: number;
  cost_cents: number;
}

// ---------------------------------------------------------------------------
// Core inference: /predict, /ground, /parse, /models, /usage
// ---------------------------------------------------------------------------

export interface TrajectoryStep {
  screenshot: string;
  actions: unknown[];
  reasoning?: string | null;
}

export interface PredictRequest {
  /** Base64 PNG/JPEG, > 100 chars, no `data:` prefix. */
  screenshot: string;
  instruction: string;
  cua_version?: CuaVersion;
  system_prompt?: string | null;
  instructions?: string | null;
  /** 320-3840, default 1920. Must match the screenshot's pixel width. */
  screen_width?: number;
  /** 240-2160, default 1080. */
  screen_height?: number;
  trajectory?: TrajectoryStep[];
  /** 1-10, default 5. */
  max_actions?: number;
  tools?: string[] | null;
  include_reasoning?: boolean;
  include_raw_code?: boolean;
}

export interface PredictResponse {
  request_id: string;
  status: PredictStatus;
  reasoning: string | null;
  actions: Action[];
  raw_code: string[] | null;
  usage: Usage;
}

export interface GroundRequest {
  screenshot: string;
  element: string;
  screen_width?: number;
  screen_height?: number;
}

export interface GroundResponse {
  x: number;
  y: number;
  usage: Usage;
}

export interface ParseRequest {
  /** Non-empty pyautogui source, < 50,000 chars. Parsing is free. */
  code: string;
}

export interface ParseResponse {
  actions: Action[];
}

export interface ModelInfo {
  id: string;
  description: string;
}

export interface CuaVersionInfo {
  id: string;
  description: string;
  avg_step_time?: string;
  features?: string[];
}

export interface ModelsResponse {
  models: ModelInfo[];
  cua_versions: CuaVersionInfo[];
  action_types: string[];
}

export interface UsageBreakdownEntry {
  requests: number;
  credits: number;
}

export interface UsageResponse {
  period: string;
  total_requests: number;
  total_credits: number;
  total_cost_cents: number;
  breakdown: Record<string, UsageBreakdownEntry>;
  balance: number;
  wallet_balance_cents: number;
  wallet_balance_usd: number;
}

// ---------------------------------------------------------------------------
// Sessions
// ---------------------------------------------------------------------------

export interface CreateSessionRequest {
  cua_version?: CuaVersion;
  screen_width?: number;
  screen_height?: number;
  /** 1-20, default 3. */
  max_trajectory_length?: number;
  system_prompt?: string | null;
  instructions?: string | null;
  tools?: string[] | null;
  metadata?: Record<string, unknown> | null;
}

export interface CreateSessionResponse {
  session_id: string;
  cua_version: CuaVersion;
  screen_size: string;
  created_at: string;
  expires_at: string;
}

export interface SessionPredictRequest {
  screenshot: string;
  instruction: string;
  include_reasoning?: boolean;
  include_raw_code?: boolean;
}

export interface SessionPredictResponse extends PredictResponse {
  session_id: string;
  step: number;
}

export interface SessionAckResponse {
  status: 'ok';
  session_id: string;
}

export interface SessionInfoResponse {
  session_id: string;
  cua_version: CuaVersion;
  screen_size: string;
  step_count: number;
  created_at: string;
  expires_at: string;
  total_credits_used: number;
}

export interface SessionListResponse {
  sessions: SessionInfoResponse[];
}

// ---------------------------------------------------------------------------
// Task runs
// ---------------------------------------------------------------------------

export interface CreateRunRequest {
  /** Target machine id, 1-128 chars (required). */
  machine_id: string;
  /** Natural-language goal, 1-16000 chars (required). */
  task: string;
  cua_version?: CuaVersion;
  /** APPENDED to the base prompt, <= 16000 chars. */
  instructions?: string | null;
  /** REPLACES the base prompt (preamble priority), <= 32000 chars. */
  system_prompt?: string | null;
  /** 1-1000, default 50. */
  max_steps?: number;
  /** 1-86400. */
  deadline_seconds?: number | null;
  on_awaiting_human?: OnAwaitingHuman;
  awaiting_human_timeout_seconds?: number | null;
  /** HTTPS only. */
  webhook_url?: string | null;
  /** <= 50 keys. */
  metadata?: Record<string, unknown> | null;
}

export interface RunResult {
  passed: boolean;
  status: string;
  summary: string;
  verdict?: string | null;
}

export interface ApiErrorInfo {
  code: string;
  message: string;
}

export interface Run {
  id: string;
  object: 'agent.run';
  status: RunStatus;
  machine_id: string;
  task: string;
  cua_version: CuaVersion;
  instructions: string | null;
  max_steps: number;
  on_awaiting_human: OnAwaitingHuman;
  steps_completed: number;
  credits_charged: number;
  cost_cents: number;
  result: RunResult | null;
  error: ApiErrorInfo | null;
  awaiting_human_reason: string | null;
  metadata: Record<string, unknown> | null;
  webhook_url: string | null;
  /** Returned ONCE on create; null on get/list. Store it immediately. */
  webhook_secret?: string | null;
  created_at: string | null;
  started_at: string | null;
  awaiting_human_since: string | null;
  finished_at: string | null;
  request_id: string | null;
}

export interface ListResponse<T> {
  object: 'list';
  data: T[];
  has_more: boolean;
  request_id: string;
}

export interface ResumeRunRequest {
  /** Optional note, <= 2000 chars. */
  note?: string;
}

export interface RunEvent {
  /** The durable cursor; send as `Last-Event-ID` on reconnect. */
  seq: number;
  type: RunEventType;
  /** JSON-decoded `data:` payload (raw string when not valid JSON). */
  data: unknown;
}

// ---------------------------------------------------------------------------
// Workflows
// ---------------------------------------------------------------------------

export type WorkflowStatus = 'active' | 'archived';

export interface CreateWorkflowRequest {
  /** 1-128 chars. */
  name: string;
  /** `^[a-z0-9][a-z0-9_-]{0,62}$`. */
  slug: string;
  definition: WorkflowDefinition;
  inputs_schema?: Record<string, unknown> | null;
  /** <= 2000 chars. */
  description?: string | null;
  metadata?: Record<string, unknown> | null;
}

export interface UpdateWorkflowRequest {
  name?: string;
  definition?: WorkflowDefinition;
  inputs_schema?: Record<string, unknown> | null;
  description?: string | null;
  status?: WorkflowStatus;
  metadata?: Record<string, unknown> | null;
}

export interface Workflow {
  id: string;
  object: 'workflow';
  name: string;
  slug: string;
  version: number;
  dsl_version: string;
  definition: WorkflowDefinition;
  inputs_schema: Record<string, unknown> | null;
  description: string | null;
  status: WorkflowStatus;
  metadata: Record<string, unknown> | null;
  created_at: string | null;
  updated_at: string | null;
  request_id: string | null;
}

export interface StartWorkflowRunRequest {
  inputs?: Record<string, unknown> | null;
  /** Default machine for task steps that omit their own. */
  machine_id?: string | null;
  /** 0-10000000; 0/null = uncapped. Breach -> GUARD_EXCEEDED. */
  budget_cents?: number | null;
  /** 1-100000. */
  max_iterations?: number | null;
  /** 1-86400. */
  deadline_seconds?: number | null;
  webhook_url?: string | null;
  metadata?: Record<string, unknown> | null;
  /** Ad-hoc runs only (POST /workflows/runs). */
  definition?: WorkflowDefinition;
  inputs_schema?: Record<string, unknown> | null;
}

export interface WorkflowRun {
  id: string;
  object: 'workflow.run';
  status: RunStatus;
  workflow_id: string | null;
  workflow_version: number | null;
  machine_id: string | null;
  inputs: Record<string, unknown>;
  output: Record<string, unknown> | null;
  error: ApiErrorInfo | null;
  awaiting_human_reason: string | null;
  awaiting_step_id: string | null;
  iterations_used: number;
  spent_cents: number;
  budget_cents: number;
  webhook_url: string | null;
  /** Returned once on create. */
  webhook_secret?: string | null;
  metadata: Record<string, unknown> | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  request_id: string | null;
}

export interface ResumeWorkflowRunRequest {
  /** false rejects (fails) the pending human_approval step. */
  approved: boolean;
  note?: string;
}

// ---------------------------------------------------------------------------
// Machines
// ---------------------------------------------------------------------------

export type OsType = 'linux' | 'windows';
export type MachineProvider = 'aws' | 'azure' | 'auto';

export type MachineStatus =
  | 'creating'
  | 'starting'
  | 'running'
  | 'stopping'
  | 'restarting'
  | 'stopped'
  | 'suspended_for_billing'
  | 'error'
  | 'terminated';

export interface ProvisionMachineRequest {
  /** 1-64 chars (required). */
  display_name: string;
  os_type?: OsType;
  desktop_enabled?: boolean;
  provider?: MachineProvider;
  /** 1-16. */
  cpu_cores?: number | null;
  /** 1-64. */
  memory_gb?: number | null;
  /** 8-500. */
  storage_gb?: number | null;
  restore_from_snapshot?: boolean | null;
  /** 5-10080 minutes; auto-terminate at created_at + ttl. */
  ttl_minutes?: number | null;
  /** <= 16 entries. */
  metadata?: Record<string, string> | null;
}

export interface Machine {
  id: string;
  display_name: string;
  status: MachineStatus;
  os_type: OsType;
  provider: string;
  desktop_enabled: boolean;
  cpu_cores: number | null;
  memory_gb: number | null;
  storage_gb: number | null;
  public_ip: string | null;
  is_test: boolean;
  created_at: string | null;
  metadata: Record<string, string> | null;
}

export interface MachineConnectionInfo {
  public_ip: string | null;
  ssh_port: number | null;
  ssh_username: string | null;
  vnc_port: number | null;
  websocket_port: number | null;
  has_ssh_key: boolean;
  has_vnc_password: boolean;
}

export interface ProvisionMachineResponse {
  machine: Machine;
  connection: MachineConnectionInfo;
  request_id: string;
}

/** Lifecycle responses for start/stop/restart/terminate/patch-TTL. */
export interface MachineLifecycleResponse {
  machine_id: string;
  status: string;
  message: string;
  request_id: string;
}

export interface SnapshotResponse {
  machine_id: string;
  snapshot_id: string;
  name: string;
  created_at: string;
  credits_charged: number;
  request_id: string;
}

export interface MachineScreenshotResponse {
  machine_id: string;
  /** Pure base64 (no `data:` prefix) — feed straight back into /predict. */
  image_b64: string;
  mime_type: string;
  width: number;
  height: number;
  captured_at: string;
  request_id: string;
}

/**
 * HIGH-RISK secrets (scope `connection:read`, served with Cache-Control:
 * no-store). Never log or persist this response.
 */
export interface MachineConnectionSecrets {
  ssh_private_key_pem: string | null;
  vnc_password: string | null;
  websocket_url: string | null;
  devtools_url: string | null;
  request_id?: string;
}

/** Shape of GET /v1/machines/pricing is served live; treat as opaque JSON. */
export type MachinePricingResponse = Record<string, unknown>;

export interface MachineActionRequest {
  command: string;
  parameters?: Record<string, unknown>;
  /** 1000-120000 ms. */
  timeout_ms?: number | null;
}

export interface MachineActionResponse {
  machine_id: string;
  command: string;
  success: boolean;
  result: unknown;
  error: string | null;
  duration_ms: number;
  screenshot: string | null;
  request_id: string;
}

export interface MachineActionsBatchRequest {
  /** <= 50 steps, executed in order. */
  steps: MachineActionRequest[];
  /** Abort on the first failure (shell `&&` style). Default true. */
  stop_on_error?: boolean;
}

export interface MachineActionsBatchResponse {
  machine_id: string;
  results: unknown[];
  completed_count: number;
  failed_count: number;
  aborted: boolean;
  request_id: string;
}

export const BROWSER_OPS = [
  'open',
  'navigate',
  'click',
  'type',
  'dom',
  'clickables',
  'state',
  'info',
  'scroll',
  'close',
  'screenshot',
  'wait',
  'list-tabs',
  'open-tab',
  'close-tab',
  'switch-tab',
] as const;
export type BrowserOp = (typeof BROWSER_OPS)[number];

export interface BrowserOpRequest {
  parameters?: Record<string, unknown>;
  timeout_ms?: number | null;
}

/** Browser op responses vary by op; treated as opaque JSON. */
export type BrowserOpResponse = Record<string, unknown>;

export interface TerminalRequest {
  /** 1-8192 chars. PowerShell on Windows, bash on Unix. */
  command: string;
  /** 1000-120000, default 30000. */
  timeout_ms?: number;
  session_id?: string | null;
  cwd?: string | null;
}

/** Terminal response shape is not pinned by the docs; treated as opaque JSON. */
export type TerminalResponse = Record<string, unknown>;

export const FILE_READ_OPS = [
  'read',
  'exists',
  'list',
  'list-directory',
  'download',
  'list-downloads',
] as const;
export const FILE_WRITE_OPS = ['write', 'edit', 'append', 'delete', 'delete-directory'] as const;
export type FileReadOp = (typeof FILE_READ_OPS)[number];
export type FileWriteOp = (typeof FILE_WRITE_OPS)[number];
export type FileOp = FileReadOp | FileWriteOp;

/** File op responses vary by op; treated as opaque JSON. */
export type FileOpResponse = Record<string, unknown>;
