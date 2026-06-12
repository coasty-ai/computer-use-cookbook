/**
 * ex09 — error-handling matrix: every scenario surfaces the documented typed
 * error (code + class + request_id + context extras), retryable statuses are
 * retried (Retry-After honored, exact counts asserted via the injected sleep
 * and via fake timers), and non-retryable 4xx are NEVER retried.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

import { CoastyClient } from '../../src/coasty/client.js';
import { CoastyError } from '../../src/coasty/errors.js';
import {
  OBVIOUSLY_INVALID_KEY,
  SCENARIOS,
  describeError,
  formatReportLine,
  runAllScenarios,
  runScenario,
  type ErrorScenario,
} from '../../src/examples/ex09-error-handling.js';
import {
  FAKE_API_KEY,
  FetchMock,
  TEST_BASE_URL,
  errorResponse,
  jsonResponse,
  makeClient,
  recordingSleep,
} from '../helpers.js';

afterEach(() => {
  vi.useRealTimers();
});

function scenario(name: string): ErrorScenario {
  const found = SCENARIOS.find((candidate) => candidate.name === name);
  if (found === undefined) throw new Error(`unknown scenario ${name}`);
  return found;
}

/** The documented envelope for each catalog row (context extras included). */
function envelopeFor(name: string): Response {
  switch (name) {
    case 'invalid_api_key':
      return errorResponse(401, 'INVALID_API_KEY', 'Invalid API key', { type: 'auth_error' });
    case 'insufficient_scope':
      return errorResponse(403, 'INSUFFICIENT_SCOPE', 'Missing scope', {
        type: 'auth_error',
        required_scope: 'connection:read',
        current_scopes: ['predict', 'session', 'runs:read'],
      });
    case 'insufficient_credits':
      return errorResponse(402, 'INSUFFICIENT_CREDITS', 'Operation needs 20 credits; you have 5.', {
        type: 'billing_error',
        required: 20,
        balance: 5,
        suggestion: 'Top up at https://coasty.ai/credits, or use a sk-coasty-test- key (free).',
      });
    case 'validation_error':
      return errorResponse(422, 'VALIDATION_ERROR', 'instruction must be non-empty', {
        type: 'validation_error',
        details: [{ loc: ['body', 'instruction'], msg: 'must not be empty' }],
      });
    case 'not_found':
      return errorResponse(404, 'RUN_NOT_FOUND', 'No such run', { type: 'not_found_error' });
    case 'not_awaiting_human':
      return errorResponse(409, 'NOT_AWAITING_HUMAN', 'Run is not paused', {
        type: 'state_error',
        current_state: 'succeeded',
        allowed_from: ['awaiting_human'],
      });
    case 'rate_limited':
      return errorResponse(
        429,
        'RATE_LIMITED',
        'Too many requests',
        { type: 'rate_limit_error', retry_after: 1 },
        { headers: { 'retry-after': '1' } },
      );
    case 'internal_error':
      return errorResponse(500, 'INTERNAL_ERROR', 'Something broke (auto-refunded)', {
        type: 'server_error',
      });
    case 'upstream_unavailable':
      return errorResponse(
        503,
        'UPSTREAM_UNAVAILABLE',
        'Upstream outage',
        { type: 'server_error', retry_after: 2 },
        { headers: { 'retry-after': '2' } },
      );
    default:
      throw new Error(`no envelope for ${name}`);
  }
}

describe('non-retried scenarios surface the typed error on the FIRST attempt', () => {
  const expectations: Record<string, { notes: string[] }> = {
    invalid_api_key: { notes: [] },
    insufficient_scope: { notes: ['required scope: connection:read'] },
    insufficient_credits: {
      notes: ['required 20 cr vs balance 5 cr', 'suggestion: Top up at https://coasty.ai/credits'],
    },
    validation_error: { notes: ['details: '] },
    not_found: { notes: [] },
    not_awaiting_human: { notes: ['current state: succeeded (allowed from: awaiting_human)'] },
  };

  for (const [name, expectation] of Object.entries(expectations)) {
    it(`${name}: matches the catalog (code, class, status, request_id)`, async () => {
      const { client, fetchMock } = makeClient();
      fetchMock.enqueue(envelopeFor(name));

      const target = scenario(name);
      const report = await runScenario(target, client);

      expect(report.matched).toBe(true);
      expect(report.code).toBe(target.expectedCode);
      expect(report.errorClass).toBe(target.expectedClass);
      expect(report.status).toBe(target.expectedStatus);
      expect(report.requestId).toBe('req_err_123');
      expect(fetchMock.calls).toHaveLength(1); // 4xx (non-429) is NEVER retried
      for (const note of expectation.notes) {
        expect(report.notes.some((line) => line.includes(note))).toBe(true);
      }
    });
  }
});

describe('retried scenarios (429/500/503) — exact retry counts', () => {
  it('429: Retry-After honored on every backoff, then surfaced after 4 attempts', async () => {
    const { client, fetchMock, sleeps } = makeClient();
    for (let i = 0; i < 4; i += 1) fetchMock.enqueue(envelopeFor('rate_limited'));

    const report = await runScenario(scenario('rate_limited'), client);

    expect(report.matched).toBe(true);
    expect(report.code).toBe('RATE_LIMITED');
    expect(fetchMock.calls).toHaveLength(4); // max 4 attempts
    expect(sleeps).toEqual([1000, 1000, 1000]); // Retry-After: 1 -> exactly 1000ms each
    expect(report.notes.some((line) => line.includes('retry_after 1s'))).toBe(true);
  });

  it('500: retried with backoff then surfaced as ServerError INTERNAL_ERROR', async () => {
    const { client, fetchMock, sleeps } = makeClient();
    for (let i = 0; i < 4; i += 1) fetchMock.enqueue(envelopeFor('internal_error'));

    const report = await runScenario(scenario('internal_error'), client);

    expect(report.matched).toBe(true);
    expect(report.errorClass).toBe('ServerError');
    expect(fetchMock.calls).toHaveLength(4);
    expect(sleeps).toEqual([500, 1000, 2000]); // full jitter with random()=1
  });

  it('503: body/header retry_after of 2s honored, then surfaced', async () => {
    const { client, fetchMock, sleeps } = makeClient();
    for (let i = 0; i < 4; i += 1) fetchMock.enqueue(envelopeFor('upstream_unavailable'));

    const report = await runScenario(scenario('upstream_unavailable'), client);

    expect(report.matched).toBe(true);
    expect(report.code).toBe('UPSTREAM_UNAVAILABLE');
    expect(sleeps).toEqual([2000, 2000, 2000]);
    expect(fetchMock.calls).toHaveLength(4);
  });

  it('drives the real setTimeout backoff with fake timers (429 -> 4 attempts)', async () => {
    vi.useFakeTimers();
    const fetchMock = new FetchMock();
    for (let i = 0; i < 4; i += 1) fetchMock.enqueue(envelopeFor('rate_limited'));
    // No injected sleep: the client's defaultSleep runs on (faked) setTimeout.
    const client = new CoastyClient({
      apiKey: FAKE_API_KEY,
      baseUrl: TEST_BASE_URL,
      fetch: fetchMock.fetch,
      random: () => 1,
    });

    const pending = runScenario(scenario('rate_limited'), client);
    await vi.advanceTimersByTimeAsync(3000); // 3 x Retry-After(1s)
    const report = await pending;

    expect(report.matched).toBe(true);
    expect(fetchMock.calls).toHaveLength(4);
  });
});

describe('runAllScenarios', () => {
  it('executes the whole catalog against one injectable target and matches every row', async () => {
    const fetchMock = new FetchMock();
    for (const target of SCENARIOS) {
      const attempts = target.retried ? 4 : 1;
      for (let i = 0; i < attempts; i += 1) fetchMock.enqueue(envelopeFor(target.name));
    }
    const { sleep } = recordingSleep();
    const lines: string[] = [];

    const reports = await runAllScenarios({
      baseUrl: TEST_BASE_URL,
      apiKey: FAKE_API_KEY,
      clientOptions: { fetch: fetchMock.fetch, sleep, random: () => 1 },
      logger: (line) => lines.push(line),
    });

    expect(reports).toHaveLength(SCENARIOS.length);
    expect(reports.every((report) => report.matched)).toBe(true);
    // The 401 scenario must have used its own (obviously fake) broken key.
    expect(fetchMock.calls[0]?.headers.get('x-api-key')).toBe(OBVIOUSLY_INVALID_KEY);
    // Every report line surfaces the request id.
    expect(lines.filter((line) => line.includes('request_id=req_err_123'))).toHaveLength(
      SCENARIOS.length,
    );
  });

  it('flags a scenario whose call unexpectedly succeeds', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(jsonResponse({ models: [], cua_versions: [], action_types: [] }));

    const report = await runScenario(scenario('invalid_api_key'), client);

    expect(report.matched).toBe(false);
    expect(report.code).toBe('(none)');
    expect(report.notes).toContain('expected an error but the call succeeded');
    expect(formatReportLine(report)).toContain('MISMATCH');
  });
});

describe('describeError', () => {
  it('extracts all documented context extras', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(envelopeFor('insufficient_credits'));
    const error = await client.runs.create({ machine_id: 'm', task: 't' }).catch((e: unknown) => e);
    if (!(error instanceof CoastyError)) throw new Error('expected a CoastyError');

    const notes = describeError(error);
    expect(notes.join('\n')).toContain('required 20 cr vs balance 5 cr');
    expect(notes.join('\n')).toContain('suggestion:');
  });
});
