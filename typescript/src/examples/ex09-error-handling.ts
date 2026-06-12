/**
 * Example 09 — Error-handling matrix.
 *
 * One scenario per row of the documented error catalog, each demonstrating
 * the typed error the shared client raises and the context extras it carries:
 *
 *   | scenario              | HTTP | code                  | typed class              |
 *   | --------------------- | ---- | --------------------- | ------------------------ |
 *   | invalid_api_key       | 401  | INVALID_API_KEY       | AuthenticationError      |
 *   | insufficient_scope    | 403  | INSUFFICIENT_SCOPE    | InsufficientScopeError   |
 *   | insufficient_credits  | 402  | INSUFFICIENT_CREDITS  | InsufficientCreditsError |
 *   | validation_error      | 422  | VALIDATION_ERROR      | ValidationError          |
 *   | not_found             | 404  | RUN_NOT_FOUND         | NotFoundError            |
 *   | not_awaiting_human    | 409  | NOT_AWAITING_HUMAN    | ConflictError            |
 *   | rate_limited          | 429  | RATE_LIMITED          | RateLimitError           |
 *   | internal_error        | 500  | INTERNAL_ERROR        | ServerError              |
 *   | upstream_unavailable  | 503  | UPSTREAM_UNAVAILABLE  | ServerError              |
 *
 * Branch on `error.code` (stable), never on `message`. 429/500/503/504 are
 * retried by the client (exponential backoff + full jitter, base 0.5s, cap
 * 8s, max 4 attempts, `Retry-After` honored) and only then surfaced; other
 * 4xx are NEVER retried. Every error carries `request_id` — quote it to
 * support.
 *
 * Estimated cost: 0 credits ($0.00). Every scenario either fails before
 * billing or hits an inference failure that is charged-then-auto-refunded.
 * Safety: pointed at the PRODUCTION base URL this example only LISTS the
 * scenarios; it executes them only against an explicitly overridden
 * `COASTY_BASE_URL` (e.g. the offline mock server at http://127.0.0.1:8787/v1).
 *
 * Run it:
 *   COASTY_BASE_URL=http://127.0.0.1:8787/v1 npx tsx src/examples/ex09-error-handling.ts
 */
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { CoastyClient, type CoastyClientOptions } from '../coasty/client.js';
import { DEFAULT_BASE_URL, getApiKey, getBaseUrl } from '../coasty/env.js';
import { CoastyError } from '../coasty/errors.js';

// ---------------------------------------------------------------------------
// Scenario catalog
// ---------------------------------------------------------------------------

/** > 100 chars of base64-looking padding, as /predict requires. */
const DEMO_SCREENSHOT = 'iVBORw0KGgo'.padEnd(120, 'A');

/** An obviously fake, syntactically invalid key for the 401 demo. */
export const OBVIOUSLY_INVALID_KEY = 'sk-coasty-test-invalid-key-for-the-401-demo';

export interface ErrorScenario {
  name: string;
  description: string;
  expectedStatus: number;
  expectedCode: string;
  /** The class name of the typed error the client raises. */
  expectedClass: string;
  /** True when the client retries before surfacing this error. */
  retried: boolean;
  /** Per-scenario client tweaks (e.g. the broken key for the 401 demo). */
  clientOptions?: Partial<CoastyClientOptions>;
  /** Fire the request that produces the documented failure. */
  trigger: (client: CoastyClient) => Promise<unknown>;
}

export const SCENARIOS: readonly ErrorScenario[] = [
  {
    name: 'invalid_api_key',
    description: 'Any call with a malformed/unknown key',
    expectedStatus: 401,
    expectedCode: 'INVALID_API_KEY',
    expectedClass: 'AuthenticationError',
    retried: false,
    clientOptions: { apiKey: OBVIOUSLY_INVALID_KEY },
    trigger: (client) => client.models(),
  },
  {
    name: 'insufficient_scope',
    description: 'GET /machines/{id}/connection without connection:read',
    expectedStatus: 403,
    expectedCode: 'INSUFFICIENT_SCOPE',
    expectedClass: 'InsufficientScopeError',
    retried: false,
    trigger: (client) => client.machines.connection('mch_test_demo'),
  },
  {
    name: 'insufficient_credits',
    description: 'POST /runs with a wallet below one step',
    expectedStatus: 402,
    expectedCode: 'INSUFFICIENT_CREDITS',
    expectedClass: 'InsufficientCreditsError',
    retried: false,
    trigger: (client) =>
      client.runs.create({ machine_id: 'mch_test_demo', task: 'Reconcile the invoices' }),
  },
  {
    name: 'validation_error',
    description: 'POST /predict with an empty instruction',
    expectedStatus: 422,
    expectedCode: 'VALIDATION_ERROR',
    expectedClass: 'ValidationError',
    retried: false,
    trigger: (client) => client.predict({ screenshot: DEMO_SCREENSHOT, instruction: '' }),
  },
  {
    name: 'not_found',
    description: 'GET /runs/{id} for a run that does not exist',
    expectedStatus: 404,
    expectedCode: 'RUN_NOT_FOUND',
    expectedClass: 'NotFoundError',
    retried: false,
    trigger: (client) => client.runs.get('run_does_not_exist'),
  },
  {
    name: 'not_awaiting_human',
    description: 'POST /runs/{id}/resume on a run that is not paused',
    expectedStatus: 409,
    expectedCode: 'NOT_AWAITING_HUMAN',
    expectedClass: 'ConflictError',
    retried: false,
    trigger: (client) => client.runs.resume('run_not_paused', { note: 'resume attempt' }),
  },
  {
    name: 'rate_limited',
    description: 'POST /predict over the rate limit (Retry-After honored, then surfaced)',
    expectedStatus: 429,
    expectedCode: 'RATE_LIMITED',
    expectedClass: 'RateLimitError',
    retried: true,
    trigger: (client) => client.predict({ screenshot: DEMO_SCREENSHOT, instruction: 'go' }),
  },
  {
    name: 'internal_error',
    description: 'POST /predict hitting a 500 (retried, auto-refunded, then surfaced)',
    expectedStatus: 500,
    expectedCode: 'INTERNAL_ERROR',
    expectedClass: 'ServerError',
    retried: true,
    trigger: (client) => client.predict({ screenshot: DEMO_SCREENSHOT, instruction: 'go' }),
  },
  {
    name: 'upstream_unavailable',
    description: 'POST /ground during an upstream outage (503 + Retry-After)',
    expectedStatus: 503,
    expectedCode: 'UPSTREAM_UNAVAILABLE',
    expectedClass: 'ServerError',
    retried: true,
    trigger: (client) => client.ground({ screenshot: DEMO_SCREENSHOT, element: 'Submit button' }),
  },
];

// ---------------------------------------------------------------------------
// Normalization + runner (testable core)
// ---------------------------------------------------------------------------

export interface ScenarioReport {
  scenario: string;
  /** True when the raised error matched the documented code AND class. */
  matched: boolean;
  errorClass: string;
  code: string;
  status: number | null;
  /** Always surfaced — quote it to support. */
  requestId: string | null;
  /** Human-readable context extras pulled off the typed error. */
  notes: string[];
}

/** Pull the documented context extras off a typed error into note lines. */
export function describeError(error: CoastyError): string[] {
  const notes: string[] = [];
  if (error.requiredScope !== undefined) {
    notes.push(
      `required scope: ${error.requiredScope}` +
        (error.currentScopes === undefined ? '' : ` (current: ${error.currentScopes.join(', ')})`),
    );
  }
  if (error.required !== undefined || error.balance !== undefined) {
    notes.push(
      `required ${String(error.required ?? '?')} cr vs balance ${String(error.balance ?? '?')} cr`,
    );
  }
  if (error.retryAfter !== undefined) {
    notes.push(`retry_after ${String(error.retryAfter)}s — the client slept exactly that long`);
  }
  if (error.currentState !== undefined) {
    notes.push(
      `current state: ${error.currentState}` +
        (error.allowedFrom === undefined ? '' : ` (allowed from: ${error.allowedFrom.join(', ')})`),
    );
  }
  if (error.details !== undefined) notes.push(`details: ${JSON.stringify(error.details)}`);
  if (error.suggestion !== undefined) notes.push(`suggestion: ${error.suggestion}`);
  return notes;
}

/** Run one scenario and normalize whatever it raised. Never throws on CoastyError. */
export async function runScenario(
  scenario: ErrorScenario,
  client: CoastyClient,
): Promise<ScenarioReport> {
  try {
    await scenario.trigger(client);
    return {
      scenario: scenario.name,
      matched: false,
      errorClass: '(none)',
      code: '(none)',
      status: null,
      requestId: null,
      notes: ['expected an error but the call succeeded'],
    };
  } catch (error) {
    if (!(error instanceof CoastyError)) throw error;
    const notes = describeError(error);
    if (scenario.retried) {
      notes.push('retried with exponential backoff + full jitter before surfacing');
    }
    return {
      scenario: scenario.name,
      matched:
        error.code === scenario.expectedCode && error.constructor.name === scenario.expectedClass,
      errorClass: error.constructor.name,
      code: error.code,
      status: error.statusCode,
      requestId: error.requestId,
      notes,
    };
  }
}

export interface RunAllOptions {
  /** Injectable target — tests point this at a mocked transport. */
  baseUrl: string;
  apiKey?: string;
  /** Forwarded to every per-scenario client (fetch/sleep/random injection). */
  clientOptions?: Partial<CoastyClientOptions>;
  logger?: (line: string) => void;
}

/** Build the per-scenario client (scenario overrides win over shared options). */
export function buildScenarioClient(scenario: ErrorScenario, options: RunAllOptions): CoastyClient {
  return new CoastyClient({
    baseUrl: options.baseUrl,
    ...(options.apiKey === undefined ? {} : { apiKey: options.apiKey }),
    ...options.clientOptions,
    ...scenario.clientOptions,
  });
}

export async function runAllScenarios(options: RunAllOptions): Promise<ScenarioReport[]> {
  const log = options.logger ?? ((line: string): void => void process.stdout.write(`${line}\n`));
  const reports: ScenarioReport[] = [];
  for (const scenario of SCENARIOS) {
    const report = await runScenario(scenario, buildScenarioClient(scenario, options));
    reports.push(report);
    log(formatReportLine(report));
    for (const note of report.notes) log(`      ${note}`);
  }
  return reports;
}

export function formatReportLine(report: ScenarioReport): string {
  const mark = report.matched ? 'OK ' : 'MISMATCH';
  return (
    `${mark} ${report.scenario.padEnd(22)} ${String(report.status ?? '-').padStart(3)} ` +
    `${report.code.padEnd(22)} ${report.errorClass.padEnd(26)} request_id=${report.requestId ?? 'n/a'}`
  );
}

// ---------------------------------------------------------------------------
// Thin CLI — live mode only LISTS; execution needs a non-production base URL
// ---------------------------------------------------------------------------

function isMain(): boolean {
  const entry = process.argv[1];
  return entry !== undefined && path.resolve(entry) === fileURLToPath(import.meta.url);
}

export async function main(): Promise<void> {
  const print = (line: string): void => void process.stdout.write(`${line}\n`);
  const baseUrl = getBaseUrl();

  if (baseUrl === DEFAULT_BASE_URL) {
    print('Pointed at the production API — listing the scenario catalog only (no calls made).');
    print('Set COASTY_BASE_URL to the offline mock server to execute them, e.g.:');
    print('  COASTY_BASE_URL=http://127.0.0.1:8787/v1  (make mock)');
    print('');
    for (const scenario of SCENARIOS) {
      print(
        `  ${scenario.name.padEnd(22)} ${String(scenario.expectedStatus)} ` +
          `${scenario.expectedCode.padEnd(22)} -> ${scenario.expectedClass.padEnd(26)} ${scenario.description}`,
      );
    }
    return;
  }

  print(`executing ${String(SCENARIOS.length)} scenarios against ${baseUrl} (cost: $0.00)`);
  const reports = await runAllScenarios({ baseUrl, apiKey: getApiKey() });
  const mismatched = reports.filter((report) => !report.matched);
  if (mismatched.length > 0) {
    console.error(`${String(mismatched.length)} scenario(s) did not match the documented catalog`);
    process.exitCode = 1;
  }
}

if (isMain()) {
  main().catch((error: unknown) => {
    if (error instanceof CoastyError) {
      console.error(`[${error.code}] ${error.message} (request_id: ${error.requestId ?? 'n/a'})`);
    } else {
      console.error(String(error));
    }
    process.exitCode = 1;
  });
}
