/**
 * CoastyClient — a thin, typed wrapper over global fetch for every /v1
 * endpoint, with:
 *
 *   - per-request AbortSignal timeouts (default 60s);
 *   - retries with exponential backoff + FULL jitter (base 500ms, cap 8s,
 *     max 4 attempts) on 429/500/503/504 and transport errors, honoring
 *     `Retry-After`; other 4xx are NEVER retried;
 *   - a POST retry guard: only inherently safe POSTs (predict/ground/parse —
 *     charged-then-refunded on failure) or POSTs carrying an
 *     `Idempotency-Key` are retried;
 *   - typed error envelope parsing (errors.ts) — every error carries the
 *     request id;
 *   - response metadata surfaced from `X-Coasty-Request-Id`,
 *     `X-Credits-Charged`, and `X-Credits-Remaining` on every call.
 *
 * Every method resolves to `{ data, meta }`.
 */
import { getApiKey, getBaseUrl } from './env.js';
import { CoastyError, errorFromResponse } from './errors.js';
import { reconnectingSse, type SleepFn, defaultSleep } from './sse.js';
import {
  type BrowserOp,
  type BrowserOpRequest,
  type BrowserOpResponse,
  type CreateRunRequest,
  type CreateSessionRequest,
  type CreateSessionResponse,
  type CreateWorkflowRequest,
  type FileOp,
  type FileOpResponse,
  type GroundRequest,
  type GroundResponse,
  type ListResponse,
  type Machine,
  type MachineActionRequest,
  type MachineActionResponse,
  type MachineActionsBatchRequest,
  type MachineActionsBatchResponse,
  type MachineConnectionSecrets,
  type MachineLifecycleResponse,
  type MachinePricingResponse,
  type MachineScreenshotResponse,
  type ModelsResponse,
  type ParseRequest,
  type ParseResponse,
  type PredictRequest,
  type PredictResponse,
  type ProvisionMachineRequest,
  type ProvisionMachineResponse,
  type ResumeRunRequest,
  type ResumeWorkflowRunRequest,
  type Run,
  type RunEvent,
  type RunEventType,
  type RunStatus,
  type SessionAckResponse,
  type SessionInfoResponse,
  type SessionListResponse,
  type SessionPredictRequest,
  type SessionPredictResponse,
  type SnapshotResponse,
  type StartWorkflowRunRequest,
  type TerminalRequest,
  type TerminalResponse,
  type UpdateWorkflowRequest,
  type UsageResponse,
  type Workflow,
  type WorkflowRun,
} from './types.js';

// ---------------------------------------------------------------------------
// Public option/result types
// ---------------------------------------------------------------------------

/** Billing/tracing metadata read from response headers on every call. */
export interface ResponseMeta {
  /** `X-Coasty-Request-Id` — quote it to support. */
  requestId: string | null;
  /** `X-Credits-Charged` (cents; 0 on test keys). */
  creditsCharged: number | null;
  /** `X-Credits-Remaining` — wallet balance after the charge (cents). */
  creditsRemaining: number | null;
  /** `X-Coasty-Test-Mode: true` on sandbox keys. */
  testMode: boolean;
  /** `X-Coasty-Idempotent-Replay: true` when served from the idempotency cache. */
  idempotentReplay: boolean;
  status: number;
}

export interface ApiResult<T> {
  data: T;
  meta: ResponseMeta;
}

export interface CallOptions {
  timeoutMs?: number;
  signal?: AbortSignal;
}

/** For endpoints documented to honor `Idempotency-Key` (and retried predicts). */
export interface CreateCallOptions extends CallOptions {
  /** <= 128 chars, `[A-Za-z0-9_\-:]`. Makes a retried create safe. */
  idempotencyKey?: string;
}

export interface EventStreamOptions {
  signal?: AbortSignal;
  /** Resume cursor (seq) carried as `Last-Event-ID` on the first connect. */
  lastEventId?: string | number;
  /** Max reconnects after a dropped stream (default 5). */
  maxReconnects?: number;
  /** Delay between reconnects in ms (default: the client retry base). */
  reconnectDelayMs?: number;
}

export interface CoastyClientOptions {
  /** Defaults to COASTY_API_KEY (repo-root .env is loaded if present). */
  apiKey?: string;
  /** Defaults to COASTY_BASE_URL or https://coasty.ai/v1. */
  baseUrl?: string;
  /** Per-attempt timeout (default 60_000 ms). */
  timeoutMs?: number;
  /** Total attempts including the first (default 4). */
  maxAttempts?: number;
  /** Backoff base (default 500 ms). */
  retryBaseMs?: number;
  /** Backoff cap (default 8_000 ms). */
  retryCapMs?: number;
  /** Injectable for tests. Defaults to globalThis.fetch. */
  fetch?: typeof globalThis.fetch;
  /** Injectable for tests. Defaults to a real setTimeout sleep. */
  sleep?: SleepFn;
  /** Injectable jitter source in [0, 1). Defaults to Math.random. */
  random?: () => number;
}

// ---------------------------------------------------------------------------
// Internals
// ---------------------------------------------------------------------------

type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

interface RequestConfig {
  method: HttpMethod;
  path: string;
  query?: Record<string, string | number | boolean | undefined>;
  body?: unknown;
  idempotencyKey?: string;
  timeoutMs?: number;
  signal?: AbortSignal;
  /** POSTs that are inherently safe to retry (charged-then-refunded). */
  retrySafe?: boolean;
}

const RETRYABLE_STATUSES: ReadonlySet<number> = new Set([429, 500, 503, 504]);
const IDEMPOTENT_METHODS: ReadonlySet<HttpMethod> = new Set(['GET', 'PUT', 'DELETE']);

function readMeta(response: Response): ResponseMeta {
  const intHeader = (name: string): number | null => {
    const raw = response.headers.get(name);
    if (raw === null) return null;
    const value = Number(raw);
    return Number.isFinite(value) ? value : null;
  };
  return {
    requestId: response.headers.get('x-coasty-request-id'),
    creditsCharged: intHeader('x-credits-charged'),
    creditsRemaining: intHeader('x-credits-remaining'),
    testMode: response.headers.get('x-coasty-test-mode') === 'true',
    idempotentReplay: response.headers.get('x-coasty-idempotent-replay') === 'true',
    status: response.status,
  };
}

/** Parse Retry-After (delta-seconds or HTTP-date) or the body's retry_after. */
function retryAfterMs(headers: Headers, error: CoastyError): number | null {
  const header = headers.get('retry-after');
  if (header !== null) {
    const seconds = Number(header);
    if (Number.isFinite(seconds)) return Math.max(0, seconds * 1000);
    const date = Date.parse(header);
    if (!Number.isNaN(date)) return Math.max(0, date - Date.now());
  }
  if (typeof error.retryAfter === 'number') return Math.max(0, error.retryAfter * 1000);
  return null;
}

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

export class CoastyClient {
  readonly baseUrl: string;
  private readonly apiKey: string;
  private readonly timeoutMs: number;
  private readonly maxAttempts: number;
  private readonly retryBaseMs: number;
  private readonly retryCapMs: number;
  private readonly fetchImpl: typeof globalThis.fetch;
  private readonly sleep: SleepFn;
  private readonly random: () => number;

  constructor(options: CoastyClientOptions = {}) {
    this.apiKey = options.apiKey ?? getApiKey();
    this.baseUrl = (options.baseUrl ?? getBaseUrl()).replace(/\/+$/, '');
    this.timeoutMs = options.timeoutMs ?? 60_000;
    this.maxAttempts = options.maxAttempts ?? 4;
    this.retryBaseMs = options.retryBaseMs ?? 500;
    this.retryCapMs = options.retryCapMs ?? 8_000;
    this.fetchImpl = options.fetch ?? globalThis.fetch;
    this.sleep = options.sleep ?? defaultSleep;
    this.random = options.random ?? Math.random;
  }

  // -- core inference -------------------------------------------------------

  /** POST /v1/predict (scope `predict`; safe to retry — refunded on failure). */
  predict(body: PredictRequest, options: CallOptions = {}): Promise<ApiResult<PredictResponse>> {
    return this.requestJson({
      method: 'POST',
      path: '/predict',
      body,
      retrySafe: true,
      ...options,
    });
  }

  /** POST /v1/ground (scope `ground`; safe to retry). */
  ground(body: GroundRequest, options: CallOptions = {}): Promise<ApiResult<GroundResponse>> {
    return this.requestJson({ method: 'POST', path: '/ground', body, retrySafe: true, ...options });
  }

  /** POST /v1/parse (scope `parse`; free + deterministic, safe to retry). */
  parse(body: ParseRequest, options: CallOptions = {}): Promise<ApiResult<ParseResponse>> {
    return this.requestJson({ method: 'POST', path: '/parse', body, retrySafe: true, ...options });
  }

  /** GET /v1/models (free). */
  models(options: CallOptions = {}): Promise<ApiResult<ModelsResponse>> {
    return this.requestJson({ method: 'GET', path: '/models', ...options });
  }

  /** GET /v1/usage?period=YYYY-MM (free). */
  usage(period?: string, options: CallOptions = {}): Promise<ApiResult<UsageResponse>> {
    return this.requestJson({ method: 'GET', path: '/usage', query: { period }, ...options });
  }

  // -- sessions (scope `session`) -------------------------------------------

  readonly sessions = {
    /** POST /v1/sessions — 10 credits one-time, no surcharges. */
    create: (
      body: CreateSessionRequest = {},
      options: CreateCallOptions = {},
    ): Promise<ApiResult<CreateSessionResponse>> =>
      this.requestJson({ method: 'POST', path: '/sessions', body, ...options }),

    /**
     * POST /v1/sessions/{id}/predict. NOT inherently retry-safe (the server
     * advances trajectory state) — pass an `idempotencyKey` to opt into
     * retries, as the docs' local-loop example does.
     */
    predict: (
      sessionId: string,
      body: SessionPredictRequest,
      options: CreateCallOptions = {},
    ): Promise<ApiResult<SessionPredictResponse>> =>
      this.requestJson({
        method: 'POST',
        path: `/sessions/${encodeURIComponent(sessionId)}/predict`,
        body,
        ...options,
      }),

    /** POST /v1/sessions/{id}/reset (free). */
    reset: (sessionId: string, options: CallOptions = {}): Promise<ApiResult<SessionAckResponse>> =>
      this.requestJson({
        method: 'POST',
        path: `/sessions/${encodeURIComponent(sessionId)}/reset`,
        ...options,
      }),

    /** GET /v1/sessions/{id} (free). */
    get: (sessionId: string, options: CallOptions = {}): Promise<ApiResult<SessionInfoResponse>> =>
      this.requestJson({
        method: 'GET',
        path: `/sessions/${encodeURIComponent(sessionId)}`,
        ...options,
      }),

    /** GET /v1/sessions (free). */
    list: (options: CallOptions = {}): Promise<ApiResult<SessionListResponse>> =>
      this.requestJson({ method: 'GET', path: '/sessions', ...options }),

    /** DELETE /v1/sessions/{id} — frees the concurrency slot (free). */
    delete: (
      sessionId: string,
      options: CallOptions = {},
    ): Promise<ApiResult<SessionAckResponse>> =>
      this.requestJson({
        method: 'DELETE',
        path: `/sessions/${encodeURIComponent(sessionId)}`,
        ...options,
      }),
  };

  // -- task runs (scopes `runs:read` / `runs:write`) -------------------------

  readonly runs = {
    /**
     * POST /v1/runs. `webhook_secret` is returned ONCE here — store it.
     * Honors `Idempotency-Key` (which also makes retries safe).
     */
    create: (body: CreateRunRequest, options: CreateCallOptions = {}): Promise<ApiResult<Run>> =>
      this.requestJson({ method: 'POST', path: '/runs', body, ...options }),

    /** GET /v1/runs/{id}. */
    get: (runId: string, options: CallOptions = {}): Promise<ApiResult<Run>> =>
      this.requestJson({ method: 'GET', path: `/runs/${encodeURIComponent(runId)}`, ...options }),

    /** GET /v1/runs?status=&limit= (limit default 20). */
    list: (
      params: { status?: RunStatus; limit?: number } = {},
      options: CallOptions = {},
    ): Promise<ApiResult<ListResponse<Run>>> =>
      this.requestJson({
        method: 'GET',
        path: '/runs',
        query: { status: params.status, limit: params.limit },
        ...options,
      }),

    /** POST /v1/runs/{id}/cancel. */
    cancel: (runId: string, options: CallOptions = {}): Promise<ApiResult<Run>> =>
      this.requestJson({
        method: 'POST',
        path: `/runs/${encodeURIComponent(runId)}/cancel`,
        ...options,
      }),

    /**
     * POST /v1/runs/{id}/resume — only valid from `awaiting_human`
     * (otherwise 409 NOT_AWAITING_HUMAN -> ConflictError).
     */
    resume: (
      runId: string,
      body: ResumeRunRequest = {},
      options: CallOptions = {},
    ): Promise<ApiResult<Run>> =>
      this.requestJson({
        method: 'POST',
        path: `/runs/${encodeURIComponent(runId)}/resume`,
        body,
        ...options,
      }),

    /**
     * GET /v1/runs/{id}/events — durable SSE stream. Reconnects with
     * `Last-Event-ID` automatically; terminates after the `done` event.
     */
    events: (runId: string, options: EventStreamOptions = {}): AsyncGenerator<RunEvent> =>
      this.streamEvents(`/runs/${encodeURIComponent(runId)}/events`, options),
  };

  // -- workflows (scopes `workflows:read` / `workflows:write`) ---------------

  readonly workflows = {
    /** POST /v1/workflows. */
    create: (
      body: CreateWorkflowRequest,
      options: CallOptions = {},
    ): Promise<ApiResult<Workflow>> =>
      this.requestJson({ method: 'POST', path: '/workflows', body, ...options }),

    /** GET /v1/workflows?limit= (default 20). */
    list: (
      params: { limit?: number } = {},
      options: CallOptions = {},
    ): Promise<ApiResult<ListResponse<Workflow>>> =>
      this.requestJson({
        method: 'GET',
        path: '/workflows',
        query: { limit: params.limit },
        ...options,
      }),

    /** GET /v1/workflows/{id}. */
    get: (workflowId: string, options: CallOptions = {}): Promise<ApiResult<Workflow>> =>
      this.requestJson({
        method: 'GET',
        path: `/workflows/${encodeURIComponent(workflowId)}`,
        ...options,
      }),

    /** PUT /v1/workflows/{id} — bumps `version`. */
    update: (
      workflowId: string,
      body: UpdateWorkflowRequest,
      options: CallOptions = {},
    ): Promise<ApiResult<Workflow>> =>
      this.requestJson({
        method: 'PUT',
        path: `/workflows/${encodeURIComponent(workflowId)}`,
        body,
        ...options,
      }),

    /** DELETE /v1/workflows/{id} — archives the workflow. */
    delete: (workflowId: string, options: CallOptions = {}): Promise<ApiResult<Workflow>> =>
      this.requestJson({
        method: 'DELETE',
        path: `/workflows/${encodeURIComponent(workflowId)}`,
        ...options,
      }),

    /** POST /v1/workflows/{id}/runs — run a SAVED workflow. Honors `Idempotency-Key`. */
    run: (
      workflowId: string,
      body: StartWorkflowRunRequest = {},
      options: CreateCallOptions = {},
    ): Promise<ApiResult<WorkflowRun>> =>
      this.requestJson({
        method: 'POST',
        path: `/workflows/${encodeURIComponent(workflowId)}/runs`,
        body,
        ...options,
      }),

    /** POST /v1/workflows/runs — AD-HOC run with an inline `definition`. */
    runAdhoc: (
      body: StartWorkflowRunRequest,
      options: CreateCallOptions = {},
    ): Promise<ApiResult<WorkflowRun>> =>
      this.requestJson({ method: 'POST', path: '/workflows/runs', body, ...options }),

    /** GET /v1/workflows/runs/{id}. */
    getRun: (runId: string, options: CallOptions = {}): Promise<ApiResult<WorkflowRun>> =>
      this.requestJson({
        method: 'GET',
        path: `/workflows/runs/${encodeURIComponent(runId)}`,
        ...options,
      }),

    /** GET /v1/workflows/runs?workflow_id=&limit=. */
    listRuns: (
      params: { workflowId?: string; limit?: number } = {},
      options: CallOptions = {},
    ): Promise<ApiResult<ListResponse<WorkflowRun>>> =>
      this.requestJson({
        method: 'GET',
        path: '/workflows/runs',
        query: { workflow_id: params.workflowId, limit: params.limit },
        ...options,
      }),

    /** POST /v1/workflows/runs/{id}/cancel. */
    cancelRun: (runId: string, options: CallOptions = {}): Promise<ApiResult<WorkflowRun>> =>
      this.requestJson({
        method: 'POST',
        path: `/workflows/runs/${encodeURIComponent(runId)}/cancel`,
        ...options,
      }),

    /**
     * POST /v1/workflows/runs/{id}/resume — `{approved, note?}`;
     * `approved: false` rejects (fails) the pending human_approval step.
     */
    resumeRun: (
      runId: string,
      body: ResumeWorkflowRunRequest,
      options: CallOptions = {},
    ): Promise<ApiResult<WorkflowRun>> =>
      this.requestJson({
        method: 'POST',
        path: `/workflows/runs/${encodeURIComponent(runId)}/resume`,
        body,
        ...options,
      }),

    /** GET /v1/workflows/runs/{id}/events — SSE, same framing/replay as run events. */
    runEvents: (runId: string, options: EventStreamOptions = {}): AsyncGenerator<RunEvent> =>
      this.streamEvents(`/workflows/runs/${encodeURIComponent(runId)}/events`, options),
  };

  // -- machines (scopes `machines:read` / `machines:write` / per-command) ----

  readonly machines = {
    /** POST /v1/machines — provision a VM. Honors `Idempotency-Key` (deduped 24h). */
    provision: (
      body: ProvisionMachineRequest,
      options: CreateCallOptions = {},
    ): Promise<ApiResult<ProvisionMachineResponse>> =>
      this.requestJson({ method: 'POST', path: '/machines', body, ...options }),

    /** GET /v1/machines?limit= (1-200, default 50). */
    list: (
      params: { limit?: number } = {},
      options: CallOptions = {},
    ): Promise<ApiResult<ListResponse<Machine>>> =>
      this.requestJson({
        method: 'GET',
        path: '/machines',
        query: { limit: params.limit },
        ...options,
      }),

    /** GET /v1/machines/pricing — live machine-readable price table. */
    pricing: (options: CallOptions = {}): Promise<ApiResult<MachinePricingResponse>> =>
      this.requestJson({ method: 'GET', path: '/machines/pricing', ...options }),

    /** GET /v1/machines/{id}. */
    get: (machineId: string, options: CallOptions = {}): Promise<ApiResult<Machine>> =>
      this.requestJson({
        method: 'GET',
        path: `/machines/${encodeURIComponent(machineId)}`,
        ...options,
      }),

    /** DELETE /v1/machines/{id} — terminate (ends all billing). */
    terminate: (
      machineId: string,
      options: CallOptions = {},
    ): Promise<ApiResult<MachineLifecycleResponse>> =>
      this.requestJson({
        method: 'DELETE',
        path: `/machines/${encodeURIComponent(machineId)}`,
        ...options,
      }),

    /** POST /v1/machines/{id}/start. */
    start: (
      machineId: string,
      options: CallOptions = {},
    ): Promise<ApiResult<MachineLifecycleResponse>> =>
      this.requestJson({
        method: 'POST',
        path: `/machines/${encodeURIComponent(machineId)}/start`,
        ...options,
      }),

    /** POST /v1/machines/{id}/stop — drops to the storage-only rate. */
    stop: (
      machineId: string,
      options: CallOptions = {},
    ): Promise<ApiResult<MachineLifecycleResponse>> =>
      this.requestJson({
        method: 'POST',
        path: `/machines/${encodeURIComponent(machineId)}/stop`,
        ...options,
      }),

    /** POST /v1/machines/{id}/restart. */
    restart: (
      machineId: string,
      options: CallOptions = {},
    ): Promise<ApiResult<MachineLifecycleResponse>> =>
      this.requestJson({
        method: 'POST',
        path: `/machines/${encodeURIComponent(machineId)}/restart`,
        ...options,
      }),

    /** PATCH /v1/machines/{id} — update the auto-destroy TTL (0 clears it). */
    patchTtl: (
      machineId: string,
      ttlMinutes: number,
      options: CallOptions = {},
    ): Promise<ApiResult<MachineLifecycleResponse>> =>
      this.requestJson({
        method: 'PATCH',
        path: `/machines/${encodeURIComponent(machineId)}`,
        body: { ttl_minutes: ttlMinutes },
        ...options,
      }),

    /** POST /v1/machines/{id}/snapshot — 1 credit (scope `snapshots:write`). Honors `Idempotency-Key`. */
    snapshot: (
      machineId: string,
      options: CreateCallOptions = {},
    ): Promise<ApiResult<SnapshotResponse>> =>
      this.requestJson({
        method: 'POST',
        path: `/machines/${encodeURIComponent(machineId)}/snapshot`,
        ...options,
      }),

    /** GET /v1/machines/{id}/screenshot — base64 ready for /predict. */
    screenshot: (
      machineId: string,
      options: CallOptions = {},
    ): Promise<ApiResult<MachineScreenshotResponse>> =>
      this.requestJson({
        method: 'GET',
        path: `/machines/${encodeURIComponent(machineId)}/screenshot`,
        ...options,
      }),

    /** POST /v1/machines/{id}/actions — one low-level action. */
    action: (
      machineId: string,
      body: MachineActionRequest,
      options: CallOptions = {},
    ): Promise<ApiResult<MachineActionResponse>> =>
      this.requestJson({
        method: 'POST',
        path: `/machines/${encodeURIComponent(machineId)}/actions`,
        body,
        ...options,
      }),

    /** POST /v1/machines/{id}/actions/batch — up to 50 ordered actions. */
    actionsBatch: (
      machineId: string,
      body: MachineActionsBatchRequest,
      options: CallOptions = {},
    ): Promise<ApiResult<MachineActionsBatchResponse>> =>
      this.requestJson({
        method: 'POST',
        path: `/machines/${encodeURIComponent(machineId)}/actions/batch`,
        body,
        ...options,
      }),

    /** POST /v1/machines/{id}/browser/{op} — browser convenience wrapper. */
    browser: (
      machineId: string,
      op: BrowserOp,
      body: BrowserOpRequest = {},
      options: CallOptions = {},
    ): Promise<ApiResult<BrowserOpResponse>> =>
      this.requestJson({
        method: 'POST',
        path: `/machines/${encodeURIComponent(machineId)}/browser/${op}`,
        body,
        ...options,
      }),

    /** POST /v1/machines/{id}/terminal (scope `terminal:exec`). */
    terminal: (
      machineId: string,
      body: TerminalRequest,
      options: CallOptions = {},
    ): Promise<ApiResult<TerminalResponse>> =>
      this.requestJson({
        method: 'POST',
        path: `/machines/${encodeURIComponent(machineId)}/terminal`,
        body,
        ...options,
      }),

    /** POST /v1/machines/{id}/files/{op} (scope `files:read` or `files:write`). */
    files: (
      machineId: string,
      op: FileOp,
      parameters: Record<string, unknown>,
      options: CallOptions = {},
    ): Promise<ApiResult<FileOpResponse>> =>
      this.requestJson({
        method: 'POST',
        path: `/machines/${encodeURIComponent(machineId)}/files/${op}`,
        body: { parameters },
        ...options,
      }),

    /**
     * GET /v1/machines/{id}/connection (scope `connection:read`).
     * HIGH-RISK: the response contains the SSH private key and VNC password.
     * Never log or persist it.
     */
    connection: (
      machineId: string,
      options: CallOptions = {},
    ): Promise<ApiResult<MachineConnectionSecrets>> =>
      this.requestJson({
        method: 'GET',
        path: `/machines/${encodeURIComponent(machineId)}/connection`,
        ...options,
      }),
  };

  // -------------------------------------------------------------------------
  // Transport
  // -------------------------------------------------------------------------

  private buildUrl(
    path: string,
    query?: Record<string, string | number | boolean | undefined>,
  ): string {
    const url = new URL(`${this.baseUrl}${path}`);
    if (query !== undefined) {
      for (const [key, value] of Object.entries(query)) {
        if (value !== undefined) url.searchParams.set(key, String(value));
      }
    }
    return url.toString();
  }

  private async fetchOnce(config: RequestConfig): Promise<Response> {
    const headers: Record<string, string> = {
      'X-API-Key': this.apiKey,
      Accept: 'application/json',
    };
    if (config.body !== undefined) headers['Content-Type'] = 'application/json';
    if (config.idempotencyKey !== undefined) headers['Idempotency-Key'] = config.idempotencyKey;

    const signals: AbortSignal[] = [AbortSignal.timeout(config.timeoutMs ?? this.timeoutMs)];
    if (config.signal !== undefined) signals.push(config.signal);

    return this.fetchImpl(this.buildUrl(config.path, config.query), {
      method: config.method,
      headers,
      body: config.body === undefined ? undefined : JSON.stringify(config.body),
      signal: AbortSignal.any(signals),
    });
  }

  /** Full jitter: delay = U(0, min(cap, base * 2^retryIndex)); Retry-After wins. */
  private backoffDelayMs(retryIndex: number, retryAfter: number | null): number {
    if (retryAfter !== null) return retryAfter;
    const ceiling = Math.min(this.retryCapMs, this.retryBaseMs * 2 ** retryIndex);
    return ceiling * this.random();
  }

  private canRetry(config: RequestConfig): boolean {
    if (IDEMPOTENT_METHODS.has(config.method)) return true;
    if (config.method === 'POST') {
      return config.retrySafe === true || config.idempotencyKey !== undefined;
    }
    return false; // PATCH and unguarded POSTs are never retried.
  }

  private async sendWithRetries(config: RequestConfig): Promise<Response> {
    const maxAttempts = this.canRetry(config) ? this.maxAttempts : 1;

    for (let attempt = 1; ; attempt += 1) {
      let response: Response;
      try {
        response = await this.fetchOnce(config);
      } catch (cause) {
        // Caller-initiated aborts propagate untouched; timeouts and transport
        // errors are retried like 5xx (subject to the POST guard).
        if (config.signal?.aborted === true) throw cause;
        if (attempt >= maxAttempts) {
          throw new CoastyError({
            code: 'NETWORK_ERROR',
            message: `${config.method} ${config.path} failed after ${String(attempt)} attempt(s): ${String(cause)}`,
            errorType: 'network_error',
            cause,
          });
        }
        await this.sleep(this.backoffDelayMs(attempt - 1, null));
        continue;
      }

      if (response.ok) return response;

      const bodyText = await response.text();
      const error = errorFromResponse(response.status, response.headers, bodyText);
      if (!RETRYABLE_STATUSES.has(response.status) || attempt >= maxAttempts) throw error;
      await this.sleep(this.backoffDelayMs(attempt - 1, retryAfterMs(response.headers, error)));
    }
  }

  private async requestJson<T>(config: RequestConfig): Promise<ApiResult<T>> {
    const response = await this.sendWithRetries(config);
    const meta = readMeta(response);
    const text = await response.text();
    if (response.status === 204 || text === '') {
      return { data: undefined as T, meta };
    }
    try {
      return { data: JSON.parse(text) as T, meta };
    } catch (cause) {
      throw new CoastyError({
        code: 'INVALID_RESPONSE',
        message: `expected JSON from ${config.method} ${config.path} but got: ${text.slice(0, 120)}`,
        errorType: 'server_error',
        requestId: meta.requestId,
        statusCode: response.status,
        cause,
      });
    }
  }

  // -------------------------------------------------------------------------
  // SSE
  // -------------------------------------------------------------------------

  private async *streamEvents(
    path: string,
    options: EventStreamOptions,
  ): AsyncGenerator<RunEvent, void, undefined> {
    const connect = async (lastEventId: string | null): Promise<ReadableStream<Uint8Array>> => {
      const headers: Record<string, string> = {
        'X-API-Key': this.apiKey,
        Accept: 'text/event-stream',
      };
      if (lastEventId !== null) headers['Last-Event-ID'] = lastEventId;
      const response = await this.fetchImpl(this.buildUrl(path), {
        method: 'GET',
        headers,
        signal: options.signal,
      });
      if (!response.ok) {
        throw errorFromResponse(response.status, response.headers, await response.text());
      }
      if (response.body === null) {
        throw new CoastyError({
          code: 'EMPTY_STREAM',
          message: `event stream ${path} returned no body`,
          errorType: 'server_error',
          requestId: response.headers.get('x-coasty-request-id'),
          statusCode: response.status,
        });
      }
      return response.body;
    };

    const initialLastEventId =
      options.lastEventId === undefined ? null : String(options.lastEventId);

    const messages = reconnectingSse(connect, {
      lastEventId: initialLastEventId,
      maxReconnects: options.maxReconnects ?? 5,
      reconnectDelayMs: options.reconnectDelayMs ?? this.retryBaseMs,
      sleep: this.sleep,
    });

    for await (const message of messages) {
      let data: unknown = message.data;
      try {
        data = JSON.parse(message.data);
      } catch {
        // Tolerate non-JSON data frames; surface the raw string.
      }
      yield {
        seq: message.id !== null && /^\d+$/.test(message.id) ? Number(message.id) : -1,
        type: message.event as RunEventType,
        data,
      };
    }
  }
}
