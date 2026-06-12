/**
 * Shared test helpers for the TypeScript track.
 *
 * Everything here is OFFLINE and deterministic: HTTP is mocked by injecting a
 * recording `fetch` into `CoastyClient` (no sockets, no MSW server needed),
 * sleeps resolve immediately while recording the requested delay, and the
 * jitter source is pinned.
 *
 * Example tests should reuse: `FAKE_API_KEY`, `makeClient()`, the response
 * builders (`jsonResponse` / `errorResponse` / `sseResponse`), the payload
 * factories (`makePredictResponse` / `makeRun` / ...), and the shared HMAC
 * vectors (`HMAC_VECTOR_1` / `HMAC_VECTOR_2` from docs/API_NOTES.md).
 */
import { CoastyClient, type CoastyClientOptions } from '../src/coasty/client.js';
import { type SleepFn } from '../src/coasty/sse.js';
import {
  type Action,
  type Machine,
  type MachineConnectionInfo,
  type PredictResponse,
  type ProvisionMachineResponse,
  type Run,
  type Usage,
  type Workflow,
  type WorkflowRun,
} from '../src/coasty/types.js';

// ---------------------------------------------------------------------------
// Constants (obviously fake — never a real key)
// ---------------------------------------------------------------------------

/** Sandbox-prefixed, obviously fake API key used by every test. */
export const FAKE_API_KEY = `sk-coasty-test-${'0'.repeat(48)}`;

/** Non-routable base URL; nothing ever actually connects to it. */
export const TEST_BASE_URL = 'https://coasty.test/v1';

/** > 100 chars of fake base64 (no `data:` prefix), as /predict requires. */
export const SCREENSHOT_B64 = 'iVBORw0KGgo'.padEnd(120, 'A');

// ---------------------------------------------------------------------------
// Shared HMAC test vectors (docs/API_NOTES.md §Shared HMAC test vectors)
// ---------------------------------------------------------------------------

export interface HmacVector {
  readonly secret: string;
  readonly timestamp: number;
  /** Exact raw body bytes (no trailing newline). */
  readonly rawBody: string;
  readonly v1: string;
  /** The full `Coasty-Signature` header value. */
  readonly header: string;
}

export const HMAC_VECTOR_1: HmacVector = {
  secret: 'whsec_test_secret_123',
  timestamp: 1750000000,
  rawBody: '{"event":"run.succeeded","run_id":"run_123","status":"succeeded"}',
  v1: '5f70978eab52dbf5838da76e5eb6c6c465560ce8e746ed8e6113c159d8bbb2d4',
  header: 't=1750000000,v1=5f70978eab52dbf5838da76e5eb6c6c465560ce8e746ed8e6113c159d8bbb2d4',
};

export const HMAC_VECTOR_2: HmacVector = {
  secret: 'whsec_other_secret_456',
  timestamp: 1750000300,
  rawBody: '{"event":"run.awaiting_human","run_id":"run_456","reason":"captcha"}',
  v1: '844504f42b7498094a83cedd7e050fc2f7fa32593b0814cc514c4be52a932e63',
  header: 't=1750000300,v1=844504f42b7498094a83cedd7e050fc2f7fa32593b0814cc514c4be52a932e63',
};

// ---------------------------------------------------------------------------
// Recording fetch mock
// ---------------------------------------------------------------------------

/** One outbound request as the client actually sent it. */
export interface RecordedRequest {
  method: string;
  url: URL;
  /** `url.pathname`, e.g. `/v1/runs`. */
  path: string;
  headers: Headers;
  /** Raw request body string (null when no body was sent). */
  rawBody: string | null;
  /** JSON-decoded body (undefined when no body was sent). */
  body: unknown;
}

export type Responder = Response | ((request: RecordedRequest) => Response | Promise<Response>);

/**
 * A queue-based `fetch` double. Enqueue one responder per expected request;
 * an exhausted queue rejects loudly (which surfaces as a NETWORK_ERROR in the
 * client — a sign the test queued too few responses).
 */
export class FetchMock {
  readonly calls: RecordedRequest[] = [];
  private readonly queue: Responder[] = [];

  enqueue(...responders: Responder[]): void {
    this.queue.push(...responders);
  }

  readonly fetch: typeof globalThis.fetch = (input, init) => {
    const url = new URL(
      typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url,
    );
    const rawBody = typeof init?.body === 'string' ? init.body : null;
    let body: unknown;
    if (rawBody !== null) {
      try {
        body = JSON.parse(rawBody);
      } catch {
        body = rawBody;
      }
    }
    const request: RecordedRequest = {
      method: init?.method ?? 'GET',
      url,
      path: url.pathname,
      headers: new Headers(init?.headers),
      rawBody,
      body,
    };
    this.calls.push(request);

    const next = this.queue.shift();
    if (next === undefined) {
      return Promise.reject(
        new Error(`FetchMock: no response queued for ${request.method} ${request.path}`),
      );
    }
    if (typeof next === 'function') {
      try {
        return Promise.resolve(next(request));
      } catch (cause) {
        return Promise.reject(cause instanceof Error ? cause : new Error(String(cause)));
      }
    }
    return Promise.resolve(next);
  };
}

// ---------------------------------------------------------------------------
// Response builders
// ---------------------------------------------------------------------------

export interface ResponseOptions {
  status?: number;
  /** `X-Coasty-Request-Id` (defaults to `req_test_123`; null omits it). */
  requestId?: string | null;
  headers?: Record<string, string>;
}

/** A JSON success (or arbitrary-status) response with Coasty headers. */
export function jsonResponse(body: unknown, options: ResponseOptions = {}): Response {
  const headers = new Headers({
    'content-type': 'application/json',
    ...options.headers,
  });
  const requestId = options.requestId === undefined ? 'req_test_123' : options.requestId;
  if (requestId !== null) headers.set('x-coasty-request-id', requestId);
  return new Response(JSON.stringify(body), { status: options.status ?? 200, headers });
}

/** A documented error envelope: `{"error": {code, message, type, request_id, ...extras}}`. */
export function errorResponse(
  status: number,
  code: string,
  message: string,
  extras: Record<string, unknown> = {},
  options: ResponseOptions = {},
): Response {
  const requestId = options.requestId === undefined ? 'req_err_123' : options.requestId;
  const envelope = {
    error: {
      code,
      message,
      type: extras.type ?? 'server_error',
      ...(requestId === null ? {} : { request_id: requestId }),
      ...extras,
    },
  };
  return jsonResponse(envelope, { ...options, status, requestId });
}

/** Build a `ReadableStream<Uint8Array>` from string chunks (closes at the end). */
export function streamFromChunks(chunks: readonly string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let index = 0;
  return new ReadableStream<Uint8Array>({
    pull(controller): void {
      const chunk = chunks[index];
      index += 1;
      if (chunk === undefined) {
        controller.close();
      } else {
        controller.enqueue(encoder.encode(chunk));
      }
    },
  });
}

/** Like {@link streamFromChunks} but ERRORS after the chunks (mid-stream drop). */
export function streamThatDrops(chunks: readonly string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let index = 0;
  return new ReadableStream<Uint8Array>({
    pull(controller): void {
      const chunk = chunks[index];
      index += 1;
      if (chunk === undefined) {
        controller.error(new Error('connection reset (simulated)'));
      } else {
        controller.enqueue(encoder.encode(chunk));
      }
    },
  });
}

/** An SSE (`text/event-stream`) response from raw frame chunks. */
export function sseResponse(
  chunks: readonly string[],
  options: ResponseOptions & { drop?: boolean } = {},
): Response {
  const headers = new Headers({ 'content-type': 'text/event-stream', ...options.headers });
  const requestId = options.requestId === undefined ? 'req_sse_123' : options.requestId;
  if (requestId !== null) headers.set('x-coasty-request-id', requestId);
  const stream = options.drop === true ? streamThatDrops(chunks) : streamFromChunks(chunks);
  return new Response(stream, { status: options.status ?? 200, headers });
}

/** Render one SSE frame (`id:` / `event:` / `data:` lines + blank-line terminator). */
export function sseFrame(fields: { id?: number | string; event?: string; data: string }): string {
  const lines: string[] = [];
  if (fields.id !== undefined) lines.push(`id: ${String(fields.id)}`);
  if (fields.event !== undefined) lines.push(`event: ${fields.event}`);
  for (const dataLine of fields.data.split('\n')) lines.push(`data: ${dataLine}`);
  return `${lines.join('\n')}\n\n`;
}

// ---------------------------------------------------------------------------
// Client factory
// ---------------------------------------------------------------------------

/** An immediately-resolving sleep that records every requested delay. */
export function recordingSleep(): { sleeps: number[]; sleep: SleepFn } {
  const sleeps: number[] = [];
  const sleep: SleepFn = (ms) => {
    sleeps.push(ms);
    return Promise.resolve();
  };
  return { sleeps, sleep };
}

export interface TestClient {
  client: CoastyClient;
  fetchMock: FetchMock;
  /** Delays (ms) the client asked to sleep — backoff assertions read this. */
  sleeps: number[];
}

/**
 * A `CoastyClient` wired to a fresh {@link FetchMock}: fake sandbox key,
 * non-routable base URL, instant recorded sleeps, and `random: () => 1` so
 * full-jitter backoff is exactly `min(cap, base * 2^retryIndex)`.
 */
export function makeClient(overrides: Partial<CoastyClientOptions> = {}): TestClient {
  const fetchMock = new FetchMock();
  const { sleeps, sleep } = recordingSleep();
  const client = new CoastyClient({
    apiKey: FAKE_API_KEY,
    baseUrl: TEST_BASE_URL,
    fetch: fetchMock.fetch,
    sleep,
    random: () => 1,
    ...overrides,
  });
  return { client, fetchMock, sleeps };
}

// ---------------------------------------------------------------------------
// Payload factories (documented response shapes)
// ---------------------------------------------------------------------------

export function makeUsage(overrides: Partial<Usage> = {}): Usage {
  return {
    input_tokens: 1200,
    output_tokens: 80,
    credits_charged: 6,
    cost_cents: 6,
    ...overrides,
  };
}

export function makeClickAction(overrides: Partial<Action> = {}): Action {
  return {
    action_type: 'click',
    params: { x: 640, y: 360 },
    description: 'Click the Submit button',
    raw_code: 'pyautogui.click(640, 360)',
    ...overrides,
  };
}

export function makePredictResponse(overrides: Partial<PredictResponse> = {}): PredictResponse {
  return {
    request_id: 'req_predict_1',
    status: 'continue',
    reasoning: 'The form is visible; clicking Submit.',
    actions: [makeClickAction()],
    raw_code: ['pyautogui.click(640, 360)'],
    usage: makeUsage(),
    ...overrides,
  };
}

export function makeRun(overrides: Partial<Run> = {}): Run {
  return {
    id: 'run_test_1',
    object: 'agent.run',
    status: 'queued',
    machine_id: 'mch_test_1',
    task: 'Open the dashboard and export the report',
    cua_version: 'v3',
    instructions: null,
    max_steps: 50,
    on_awaiting_human: 'pause',
    steps_completed: 0,
    credits_charged: 0,
    cost_cents: 0,
    result: null,
    error: null,
    awaiting_human_reason: null,
    metadata: null,
    webhook_url: null,
    webhook_secret: null,
    created_at: '2026-06-11T00:00:00Z',
    started_at: null,
    awaiting_human_since: null,
    finished_at: null,
    request_id: 'req_run_1',
    ...overrides,
  };
}

export function makeWorkflow(overrides: Partial<Workflow> = {}): Workflow {
  return {
    id: 'wf_test_1',
    object: 'workflow',
    name: 'Nightly export',
    slug: 'nightly-export',
    version: 1,
    dsl_version: '2026-06-01',
    definition: { steps: [{ id: 'export', type: 'task', task: 'Export the report' }] },
    inputs_schema: null,
    description: null,
    status: 'active',
    metadata: null,
    created_at: '2026-06-11T00:00:00Z',
    updated_at: '2026-06-11T00:00:00Z',
    request_id: 'req_wf_1',
    ...overrides,
  };
}

export function makeWorkflowRun(overrides: Partial<WorkflowRun> = {}): WorkflowRun {
  return {
    id: 'wfr_test_1',
    object: 'workflow.run',
    status: 'queued',
    workflow_id: 'wf_test_1',
    workflow_version: 1,
    machine_id: 'mch_test_1',
    inputs: {},
    output: null,
    error: null,
    awaiting_human_reason: null,
    awaiting_step_id: null,
    iterations_used: 0,
    spent_cents: 0,
    budget_cents: 0,
    webhook_url: null,
    webhook_secret: null,
    metadata: null,
    created_at: '2026-06-11T00:00:00Z',
    started_at: null,
    finished_at: null,
    request_id: 'req_wfr_1',
    ...overrides,
  };
}

export function makeMachine(overrides: Partial<Machine> = {}): Machine {
  return {
    id: 'mch_test_1',
    display_name: 'cookbook-vm',
    status: 'running',
    os_type: 'linux',
    provider: 'sandbox',
    desktop_enabled: true,
    cpu_cores: 2,
    memory_gb: 4,
    storage_gb: 32,
    public_ip: '127.0.0.1',
    is_test: true,
    created_at: '2026-06-11T00:00:00Z',
    metadata: null,
    ...overrides,
  };
}

export function makeConnectionInfo(
  overrides: Partial<MachineConnectionInfo> = {},
): MachineConnectionInfo {
  return {
    public_ip: '127.0.0.1',
    ssh_port: 22,
    ssh_username: 'coasty',
    vnc_port: 5900,
    websocket_port: 8080,
    has_ssh_key: true,
    has_vnc_password: true,
    ...overrides,
  };
}

export function makeProvisionResponse(
  overrides: Partial<ProvisionMachineResponse> = {},
): ProvisionMachineResponse {
  return {
    machine: makeMachine(),
    connection: makeConnectionInfo(),
    request_id: 'req_mch_1',
    ...overrides,
  };
}
