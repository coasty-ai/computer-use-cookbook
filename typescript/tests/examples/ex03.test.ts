/**
 * Example 03 tests — session lifecycle. The critical property: the session
 * is ALWAYS deleted (finally), even when a predict throws mid-loop.
 */
import { describe, expect, it } from 'vitest';
import { NullBackend } from '../../src/coasty/executor.js';
import { type PredictResponse } from '../../src/coasty/types.js';
import { type PrintFn } from '../../src/examples/ex01-local-predict-loop.js';
import { runSessionLoop } from '../../src/examples/ex03-sessions.js';
import {
  SCREENSHOT_B64,
  errorResponse,
  jsonResponse,
  makeClickAction,
  makeClient,
  makePredictResponse,
} from '../helpers.js';

const provider = (): Promise<string> => Promise.resolve(SCREENSHOT_B64);
const silent: PrintFn = () => undefined;

const SESSION_ID = 'sess_test_1';

function createSessionResponse(): Response {
  return jsonResponse({
    session_id: SESSION_ID,
    cua_version: 'v3',
    screen_size: '1280x720',
    created_at: '2026-06-11T00:00:00Z',
    expires_at: '2026-06-11T01:00:00Z',
  });
}

function sessionPredictResponse(step: number, overrides: Partial<PredictResponse> = {}): Response {
  return jsonResponse({ ...makePredictResponse(overrides), session_id: SESSION_ID, step });
}

function ackResponse(): Response {
  return jsonResponse({ status: 'ok', session_id: SESSION_ID });
}

describe('runSessionLoop', () => {
  it('walks the full lifecycle: create, predict xN, info, reset, delete', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(
      createSessionResponse(),
      sessionPredictResponse(1, { status: 'continue', actions: [makeClickAction()] }),
      sessionPredictResponse(2, { status: 'done', actions: [] }),
      jsonResponse({
        session_id: SESSION_ID,
        cua_version: 'v3',
        screen_size: '1280x720',
        step_count: 2,
        created_at: '2026-06-11T00:00:00Z',
        expires_at: '2026-06-11T01:00:00Z',
        total_credits_used: 18,
      }),
      ackResponse(), // reset
      ackResponse(), // delete
    );
    const backend = new NullBackend();

    const result = await runSessionLoop({
      client,
      instruction: 'click through the wizard',
      screenshot: provider,
      backend,
      maxSteps: 5,
      print: silent,
    });

    expect(result).toEqual({
      sessionId: SESSION_ID,
      status: 'done',
      stepsUsed: 2,
      creditsCharged: 12, // 2 x 6 cr from makeUsage()
      stepCountReported: 2,
      totalCreditsReported: 18,
      failReason: null,
    });
    expect(backend.calls.map((call) => call.method)).toEqual(['click']);

    const trail = fetchMock.calls.map((call) => `${call.method} ${call.path}`);
    expect(trail).toEqual([
      'POST /v1/sessions',
      `POST /v1/sessions/${SESSION_ID}/predict`,
      `POST /v1/sessions/${SESSION_ID}/predict`,
      `GET /v1/sessions/${SESSION_ID}`,
      `POST /v1/sessions/${SESSION_ID}/reset`,
      `DELETE /v1/sessions/${SESSION_ID}`,
    ]);

    // Session predicts opt into safe retries with per-step Idempotency-Keys.
    const predictKeys = fetchMock.calls
      .filter((call) => call.path.endsWith('/predict'))
      .map((call) => call.headers.get('idempotency-key'));
    expect(predictKeys).toHaveLength(2);
    for (const key of predictKeys) expect(key).toMatch(/^ex03-step\d+-/);
    expect(new Set(predictKeys).size).toBe(2);
  });

  it('DELETEs the session even when a predict throws (finally)', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(
      createSessionResponse(),
      errorResponse(422, 'INVALID_SCREENSHOT', 'bad screenshot', { type: 'validation_error' }),
      ackResponse(), // the delete in finally
    );

    await expect(
      runSessionLoop({
        client,
        instruction: 'go',
        screenshot: provider,
        backend: new NullBackend(),
        maxSteps: 3,
        print: silent,
      }),
    ).rejects.toMatchObject({ code: 'INVALID_SCREENSHOT', requestId: 'req_err_123' });

    const trail = fetchMock.calls.map((call) => `${call.method} ${call.path}`);
    expect(trail).toEqual([
      'POST /v1/sessions',
      `POST /v1/sessions/${SESSION_ID}/predict`,
      `DELETE /v1/sessions/${SESSION_ID}`, // cleanup ran despite the throw
    ]);
  });

  it('does not mask the original error when the cleanup delete also fails', async () => {
    const { client, fetchMock } = makeClient({ maxAttempts: 1 });
    fetchMock.enqueue(
      createSessionResponse(),
      errorResponse(422, 'INVALID_SCREENSHOT', 'bad screenshot', { type: 'validation_error' }),
      errorResponse(500, 'INTERNAL_ERROR', 'delete exploded', { type: 'server_error' }),
    );
    const lines: string[] = [];

    await expect(
      runSessionLoop({
        client,
        instruction: 'go',
        screenshot: provider,
        backend: new NullBackend(),
        maxSteps: 3,
        print: (line) => {
          lines.push(line);
        },
      }),
    ).rejects.toMatchObject({ code: 'INVALID_SCREENSHOT' }); // NOT the delete error

    expect(lines.join('\n')).toContain('warning: failed to delete session');
  });

  it('stops the loop on max steps and still cleans up', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(
      createSessionResponse(),
      sessionPredictResponse(1, { status: 'continue' }),
      sessionPredictResponse(2, { status: 'continue' }),
      ackResponse(), // delete
    );

    const result = await runSessionLoop({
      client,
      instruction: 'go',
      screenshot: provider,
      backend: new NullBackend(),
      maxSteps: 2,
      showLifecycle: false, // skip info/reset to isolate the loop cap
      print: silent,
    });

    expect(result.status).toBe('max_steps');
    expect(result.stepsUsed).toBe(2);
    expect(fetchMock.calls.at(-1)?.method).toBe('DELETE');
  });
});
