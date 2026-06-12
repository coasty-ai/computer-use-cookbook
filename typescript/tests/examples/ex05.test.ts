/**
 * Example 05 tests — task runs: idempotent create, poll-to-terminal, SSE
 * with Last-Event-ID reconnect (no loss / no duplication), the
 * awaiting_human -> resume flow in both modes, and the spend gate.
 * Fully offline; all sleeps are injected and resolve instantly.
 */
import { describe, expect, it } from 'vitest';
import {
  buildCreateRunRequest,
  createRun,
  ensureSpendApproved,
  formatRunSummary,
  parseArgs,
  pollRunUntilTerminal,
  watchRunEvents,
  type PrintFn,
} from '../../src/examples/ex05-runs.js';
import {
  FAKE_API_KEY,
  errorResponse,
  jsonResponse,
  makeClient,
  makeRun,
  recordingSleep,
  sseFrame,
  sseResponse,
} from '../helpers.js';

const silent: PrintFn = () => undefined;

/** Obviously fake live-prefixed key for spend-gate tests (never a real key). */
const FAKE_LIVE_KEY = `sk-coasty-live-${'0'.repeat(48)}`;

describe('createRun', () => {
  it('POSTs /runs with an Idempotency-Key and the documented body fields', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(jsonResponse(makeRun({ cua_version: 'v4', max_steps: 7 })));

    const run = await createRun({
      client,
      request: buildCreateRunRequest({
        machineId: 'mch_test_1',
        task: 'export the report',
        cuaVersion: 'v4', // pro+ tier flag path
        maxSteps: 7,
      }),
      idempotencyKey: 'ex05-run-fixed-key',
      print: silent,
    });

    expect(run.id).toBe('run_test_1');
    const call = fetchMock.calls[0];
    expect(call?.method).toBe('POST');
    expect(call?.path).toBe('/v1/runs');
    expect(call?.headers.get('idempotency-key')).toBe('ex05-run-fixed-key');
    expect(call?.body).toEqual({
      machine_id: 'mch_test_1',
      task: 'export the report',
      cua_version: 'v4',
      max_steps: 7,
      on_awaiting_human: 'pause',
    });
  });

  it('defaults to v3 and generates a fresh idempotency key', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(jsonResponse(makeRun()));

    const request = buildCreateRunRequest({ machineId: 'mch_test_1', task: 'go' });
    expect(request.cua_version).toBe('v3');

    await createRun({ client, request, print: silent });
    expect(fetchMock.calls[0]?.headers.get('idempotency-key')).toMatch(/^ex05-run-/);
  });

  it('surfaces the request_id on create errors (e.g. v4 without pro+)', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(
      errorResponse(400, 'FEATURE_NOT_AVAILABLE', 'cua_version v4 requires the pro tier', {
        type: 'validation_error',
      }),
    );

    await expect(
      createRun({
        client,
        request: buildCreateRunRequest({ machineId: 'mch_test_1', task: 'go', cuaVersion: 'v4' }),
        print: silent,
      }),
    ).rejects.toMatchObject({ code: 'FEATURE_NOT_AVAILABLE', requestId: 'req_err_123' });
  });
});

describe('pollRunUntilTerminal', () => {
  it('polls until a terminal status, sleeping between polls', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(
      jsonResponse(makeRun({ status: 'queued' })),
      jsonResponse(makeRun({ status: 'running', steps_completed: 1 })),
      jsonResponse(
        makeRun({ status: 'succeeded', steps_completed: 3, credits_charged: 15, cost_cents: 15 }),
      ),
    );
    const { sleeps, sleep } = recordingSleep();
    const transitions: string[] = [];

    const run = await pollRunUntilTerminal({
      client,
      runId: 'run_test_1',
      intervalMs: 5,
      sleep,
      onUpdate: (current) => {
        transitions.push(current.status);
      },
      print: silent,
    });

    expect(run.status).toBe('succeeded');
    expect(run.cost_cents).toBe(15);
    expect(transitions).toEqual(['queued', 'running', 'succeeded']);
    expect(sleeps).toEqual([5, 5]); // no sleep after the terminal poll
    expect(
      fetchMock.calls.every((call) => call.method === 'GET' && call.path === '/v1/runs/run_test_1'),
    ).toBe(true);
  });

  it('resumes an awaiting_human run via the handler, then finishes', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(
      jsonResponse(makeRun({ status: 'awaiting_human', awaiting_human_reason: 'captcha' })),
      jsonResponse(makeRun({ status: 'running' })), // resume response
      jsonResponse(makeRun({ status: 'succeeded' })), // next poll
    );
    const { sleep } = recordingSleep();
    const reasons: (string | null)[] = [];

    const run = await pollRunUntilTerminal({
      client,
      runId: 'run_test_1',
      intervalMs: 1,
      sleep,
      onAwaitingHuman: (paused) => {
        reasons.push(paused.awaiting_human_reason);
        return Promise.resolve({ note: 'captcha solved, continue' });
      },
      print: silent,
    });

    expect(run.status).toBe('succeeded');
    expect(reasons).toEqual(['captcha']);
    const resumeCall = fetchMock.calls[1];
    expect(resumeCall?.method).toBe('POST');
    expect(resumeCall?.path).toBe('/v1/runs/run_test_1/resume');
    expect(resumeCall?.body).toEqual({ note: 'captcha solved, continue' });
  });

  it('gives up after maxPolls instead of polling forever', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(
      jsonResponse(makeRun({ status: 'running' })),
      jsonResponse(makeRun({ status: 'running' })),
    );
    const { sleep } = recordingSleep();

    await expect(
      pollRunUntilTerminal({
        client,
        runId: 'run_test_1',
        intervalMs: 1,
        sleep,
        maxPolls: 2,
        print: silent,
      }),
    ).rejects.toThrow(/did not reach a terminal status after 2 poll/);
  });
});

describe('watchRunEvents (SSE)', () => {
  it('reconnects with Last-Event-ID after a drop — no loss, no duplication', async () => {
    const { client, fetchMock } = makeClient();
    const billing = '{"credits_charged":5,"cost_cents":5}';
    fetchMock.enqueue(
      // First connection drops mid-stream after seq 2.
      sseResponse(
        [
          sseFrame({ id: 1, event: 'status', data: '{"status":"running"}' }),
          sseFrame({ id: 2, event: 'billing', data: billing }),
        ],
        { drop: true },
      ),
      // Replay resumes AFTER seq 2; the server also replays seq 2 itself,
      // which the client must filter out (no duplication).
      sseResponse([
        sseFrame({ id: 2, event: 'billing', data: billing }),
        sseFrame({ id: 3, event: 'step', data: '{"step":1}' }),
        sseFrame({ id: 4, event: 'done', data: '{"status":"succeeded"}' }),
      ]),
      jsonResponse(makeRun({ status: 'succeeded', credits_charged: 5, cost_cents: 5 })),
    );

    const watched = await watchRunEvents({ client, runId: 'run_test_1', print: silent });

    expect(watched.seqs).toEqual([1, 2, 3, 4]); // gap-free, duplicate-free
    expect(watched.billingEvents).toEqual([{ credits_charged: 5, cost_cents: 5 }]);
    expect(watched.run.status).toBe('succeeded');
    expect(watched.run.cost_cents).toBe(5);

    expect(fetchMock.calls[0]?.path).toBe('/v1/runs/run_test_1/events');
    expect(fetchMock.calls[0]?.headers.get('last-event-id')).toBeNull(); // fresh start
    expect(fetchMock.calls[1]?.path).toBe('/v1/runs/run_test_1/events');
    expect(fetchMock.calls[1]?.headers.get('last-event-id')).toBe('2'); // resume cursor
  });

  it('triggers the resume flow when an awaiting_human event arrives', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(
      sseResponse([
        sseFrame({ id: 1, event: 'status', data: '{"status":"running"}' }),
        sseFrame({ id: 2, event: 'awaiting_human', data: '{"reason":"otp required"}' }),
        sseFrame({ id: 3, event: 'resumed', data: '{}' }),
        sseFrame({ id: 4, event: 'done', data: '{"status":"succeeded"}' }),
      ]),
      jsonResponse(makeRun({ status: 'awaiting_human', awaiting_human_reason: 'otp required' })),
      jsonResponse(makeRun({ status: 'running' })), // resume response
      jsonResponse(makeRun({ status: 'succeeded' })), // final GET
    );

    const watched = await watchRunEvents({
      client,
      runId: 'run_test_1',
      onAwaitingHuman: (paused) =>
        Promise.resolve({ note: `handled: ${paused.awaiting_human_reason ?? 'unknown'}` }),
      print: silent,
    });

    expect(watched.seqs).toEqual([1, 2, 3, 4]);
    expect(watched.run.status).toBe('succeeded');
    const resumeCall = fetchMock.calls.find((call) => call.path.endsWith('/resume'));
    expect(resumeCall?.method).toBe('POST');
    expect(resumeCall?.body).toEqual({ note: 'handled: otp required' });
  });
});

describe('ensureSpendApproved (spend gate)', () => {
  const items = [{ label: 'run: <= 10 steps @ v3', credits: 50 }];

  it('BLOCKS a non-sandbox key without --confirm or COASTY_CONFIRM_SPEND', () => {
    const lines: string[] = [];
    const approved = ensureSpendApproved({
      apiKey: FAKE_LIVE_KEY,
      items,
      confirmFlag: false,
      env: {}, // stubbed: no COASTY_CONFIRM_SPEND
      print: (line) => {
        lines.push(line);
      },
    });
    expect(approved).toBe(false);
    const output = lines.join('\n');
    expect(output).toContain('BLOCKED');
    expect(output).toContain('--confirm');
    expect(output).toContain('COASTY_CONFIRM_SPEND=1');
  });

  it('allows a non-sandbox key with the --confirm flag', () => {
    expect(
      ensureSpendApproved({
        apiKey: FAKE_LIVE_KEY,
        items,
        confirmFlag: true,
        env: {},
        print: silent,
      }),
    ).toBe(true);
  });

  it('allows a non-sandbox key with COASTY_CONFIRM_SPEND=1 (env stubbed)', () => {
    expect(
      ensureSpendApproved({
        apiKey: FAKE_LIVE_KEY,
        items,
        confirmFlag: false,
        env: { COASTY_CONFIRM_SPEND: '1' },
        print: silent,
      }),
    ).toBe(true);
  });

  it('always allows sandbox keys and prints "$0 (sandbox)"', () => {
    const lines: string[] = [];
    const approved = ensureSpendApproved({
      apiKey: FAKE_API_KEY, // sk-coasty-test-...
      items,
      confirmFlag: false,
      env: {},
      print: (line) => {
        lines.push(line);
      },
    });
    expect(approved).toBe(true);
    const output = lines.join('\n');
    expect(output).toContain('$0 (sandbox)');
    expect(output).toContain('sandbox key, never bills');
  });
});

describe('formatRunSummary', () => {
  it('includes status, steps, credits, cost_cents and the result', () => {
    const summary = formatRunSummary(
      makeRun({
        status: 'succeeded',
        steps_completed: 3,
        credits_charged: 15,
        cost_cents: 15,
        result: { passed: true, status: 'succeeded', summary: 'Report exported.' },
      }),
    );
    expect(summary).toContain('run run_test_1: succeeded');
    expect(summary).toContain('steps_completed: 3 / 50');
    expect(summary).toContain('credits_charged: 15 cr');
    expect(summary).toContain('cost_cents:      15 ($0.15)');
    expect(summary).toContain('passed=true — Report exported.');
    expect(summary).toContain('request_id: req_run_1');
  });
});

describe('parseArgs', () => {
  it('parses --v4 and --events with env-driven defaults', () => {
    const config = parseArgs(['--v4', '--events', '--confirm', '--max-steps', '12'], {
      COASTY_MACHINE_ID: 'mch_test_9',
      EX05_TASK: 'do the thing',
    });
    expect(config.machineId).toBe('mch_test_9');
    expect(config.task).toBe('do the thing');
    expect(config.cuaVersion).toBe('v4');
    expect(config.events).toBe(true);
    expect(config.confirm).toBe(true);
    expect(config.maxSteps).toBe(12);
  });

  it('requires a machine id', () => {
    expect(() => parseArgs([], {})).toThrow(/machine id is required/);
  });

  it('rejects unknown arguments', () => {
    expect(() => parseArgs(['--bogus'], { COASTY_MACHINE_ID: 'mch_test_1' })).toThrow(
      /unknown argument/,
    );
  });
});
