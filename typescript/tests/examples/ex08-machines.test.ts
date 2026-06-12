/**
 * ex08 — machines lifecycle: exact operation order, screenshot persisted to
 * disk, guards (spend + ttl) firing BEFORE any request, stop+terminate always
 * running from `finally`, and the cost readout sourced from cost.ts.
 */
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ServerError } from '../../src/coasty/errors.js';
import {
  DEFAULT_TTL_MINUTES,
  MachineOpFailedError,
  REMOTE_FILE_CONTENT,
  REMOTE_FILE_PATH,
  SpendNotConfirmedError,
  TtlGuardError,
  runMachineLifecycle,
  validateTtlMinutes,
} from '../../src/examples/ex08-machines.js';
import {
  errorResponse,
  jsonResponse,
  makeClient,
  makeMachine,
  makeProvisionResponse,
  recordingSleep,
  type TestClient,
} from '../helpers.js';

const SCREENSHOT_BYTES = 'fake png bytes (ex08)';

function screenshotResponse(): Response {
  return jsonResponse({
    machine_id: 'mch_test_1',
    image_b64: Buffer.from(SCREENSHOT_BYTES, 'utf8').toString('base64'),
    mime_type: 'image/png',
    width: 1280,
    height: 720,
    captured_at: '2026-06-11T00:00:00Z',
    request_id: 'req_shot_1',
  });
}

/** Responses for steps 3..12 (screenshot through terminate). */
function tailResponses(): Response[] {
  return [
    screenshotResponse(),
    jsonResponse({
      machine_id: 'mch_test_1',
      command: 'click',
      success: true,
      result: null,
      error: null,
      duration_ms: 12,
      screenshot: null,
      request_id: 'req_act_1',
    }),
    jsonResponse({
      machine_id: 'mch_test_1',
      results: [{}, {}, {}],
      completed_count: 3,
      failed_count: 0,
      aborted: false,
      request_id: 'req_batch_1',
    }),
    jsonResponse({ stdout: 'hello from the coasty cookbook', exit_code: 0 }),
    jsonResponse({ success: true }),
    jsonResponse({ content: REMOTE_FILE_CONTENT }),
    jsonResponse({ url: 'https://example.com', status: 'complete' }),
    jsonResponse(
      {
        machine_id: 'mch_test_1',
        snapshot_id: 'snap_1',
        name: 'auto',
        created_at: '2026-06-11T00:10:00Z',
        credits_charged: 1,
        request_id: 'req_snap_1',
      },
      { headers: { 'x-credits-charged': '1' } },
    ),
    jsonResponse({ machine_id: 'mch_test_1', status: 'stopping', message: 'ok', request_id: 'r' }),
    jsonResponse({
      machine_id: 'mch_test_1',
      status: 'terminated',
      message: 'ok',
      request_id: 'r',
    }),
  ];
}

const EXPECTED_ORDER = [
  'provision',
  'poll',
  'screenshot',
  'action',
  'actions_batch',
  'terminal',
  'file_write',
  'file_read',
  'browser_navigate',
  'snapshot',
  'stop',
  'terminate',
];

describe('runMachineLifecycle', () => {
  let tempDir: string;
  let screenshotPath: string;

  beforeEach(async () => {
    tempDir = await mkdtemp(path.join(os.tmpdir(), 'coasty-ex08-'));
    screenshotPath = path.join(tempDir, 'shot.png');
  });

  afterEach(async () => {
    await rm(tempDir, { recursive: true, force: true });
  });

  function baseOptions(
    overrides: Record<string, unknown> = {},
  ): Parameters<typeof runMachineLifecycle>[1] {
    return {
      sandbox: true,
      confirmedSpend: false,
      screenshotPath,
      logger: () => undefined,
      ...overrides,
    };
  }

  it('runs the full lifecycle in the documented order and saves the screenshot', async () => {
    const { client, fetchMock } = makeClient();
    const nowValues = [0, 360_000]; // 6 minutes elapsed at readout time
    fetchMock.enqueue(jsonResponse(makeProvisionResponse()), jsonResponse(makeMachine()));
    fetchMock.enqueue(...tailResponses());

    const report = await runMachineLifecycle(client, {
      ...baseOptions(),
      idempotencyKey: 'cookbook-ex08-demo',
      now: () => nowValues.shift() ?? 360_000,
    });

    expect(report.operations).toEqual(EXPECTED_ORDER);
    expect(fetchMock.calls.map((call) => `${call.method} ${call.path}`)).toEqual([
      'POST /v1/machines',
      'GET /v1/machines/mch_test_1',
      'GET /v1/machines/mch_test_1/screenshot',
      'POST /v1/machines/mch_test_1/actions',
      'POST /v1/machines/mch_test_1/actions/batch',
      'POST /v1/machines/mch_test_1/terminal',
      'POST /v1/machines/mch_test_1/files/write',
      'POST /v1/machines/mch_test_1/files/read',
      'POST /v1/machines/mch_test_1/browser/navigate',
      'POST /v1/machines/mch_test_1/snapshot',
      'POST /v1/machines/mch_test_1/stop',
      'DELETE /v1/machines/mch_test_1',
    ]);

    // Provision body carries the mandatory auto-terminate TTL.
    const provisionBody = fetchMock.calls[0]?.body as Record<string, unknown>;
    expect(provisionBody).toEqual({
      display_name: 'cookbook-ex08',
      os_type: 'linux',
      desktop_enabled: true,
      ttl_minutes: DEFAULT_TTL_MINUTES,
    });
    expect(fetchMock.calls[0]?.headers.get('idempotency-key')).toBe('cookbook-ex08-demo');
    expect(fetchMock.calls[9]?.headers.get('idempotency-key')).toBe('cookbook-ex08-demo-snap');

    // File ops hit the documented body shape ({parameters}).
    expect(fetchMock.calls[6]?.body).toEqual({
      parameters: { path: REMOTE_FILE_PATH, content: REMOTE_FILE_CONTENT },
    });
    expect(fetchMock.calls[8]?.body).toEqual({ parameters: { url: 'https://example.com' } });

    // Screenshot really landed on disk.
    expect(await readFile(screenshotPath, 'utf8')).toBe(SCREENSHOT_BYTES);
    expect(report.screenshotBytes).toBe(SCREENSHOT_BYTES.length);

    // Cost readout: 6 min linux running floors to 0 cr; snapshot adds 1.
    expect(report.elapsedMinutes).toBe(6);
    expect(report.estimatedCredits).toBe(1);
    expect(report.creditsObserved).toBe(1); // only the snapshot response carried the header
    expect(report.snapshotId).toBe('snap_1');
    expect(report.fileContent).toEqual({ content: REMOTE_FILE_CONTENT });
  });

  it('polls until the machine is running (instant on sandbox, looped on live)', async () => {
    const { client, fetchMock } = makeClient();
    const { sleeps, sleep } = recordingSleep();
    fetchMock.enqueue(
      jsonResponse(makeProvisionResponse({ machine: makeMachine({ status: 'creating' }) })),
      jsonResponse(makeMachine({ status: 'starting' })),
      jsonResponse(makeMachine({ status: 'running' })),
    );
    fetchMock.enqueue(...tailResponses());

    const report = await runMachineLifecycle(client, {
      ...baseOptions(),
      pollIntervalMs: 10,
      sleep,
      now: () => 0,
    });

    expect(report.operations).toEqual(EXPECTED_ORDER);
    expect(sleeps).toEqual([10]); // slept once after the not-yet-running poll
    expect(fetchMock.calls[1]?.path).toBe('/v1/machines/mch_test_1');
    expect(fetchMock.calls[2]?.path).toBe('/v1/machines/mch_test_1');
  });

  it('still stops + terminates from finally when a mid-lifecycle call fails', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(jsonResponse(makeProvisionResponse()), jsonResponse(makeMachine()));
    const tail = tailResponses();
    fetchMock.enqueue(
      tail[0] as Response, // screenshot
      tail[1] as Response, // action
      tail[2] as Response, // batch
      errorResponse(500, 'INTERNAL_ERROR', 'terminal backend crashed'), // terminal (POST: not retried)
      jsonResponse({
        machine_id: 'mch_test_1',
        status: 'stopping',
        message: 'ok',
        request_id: 'r',
      }),
      jsonResponse({
        machine_id: 'mch_test_1',
        status: 'terminated',
        message: 'ok',
        request_id: 'r',
      }),
    );

    const error = await runMachineLifecycle(client, { ...baseOptions(), now: () => 0 }).catch(
      (e: unknown) => e,
    );

    expect(error).toBeInstanceOf(ServerError);
    expect((error as ServerError).code).toBe('INTERNAL_ERROR');
    expect((error as ServerError).requestId).toBe('req_err_123'); // errors carry the request id
    const calls = fetchMock.calls.map((call) => `${call.method} ${call.path}`);
    expect(calls.at(-2)).toBe('POST /v1/machines/mch_test_1/stop');
    expect(calls.at(-1)).toBe('DELETE /v1/machines/mch_test_1');
  });

  it('surfaces success=false action results loudly (with the request id)', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(
      jsonResponse(makeProvisionResponse()),
      jsonResponse(makeMachine()),
      screenshotResponse(),
      jsonResponse({
        machine_id: 'mch_test_1',
        command: 'click',
        success: false,
        result: null,
        error: 'no display attached',
        duration_ms: 5,
        screenshot: null,
        request_id: 'req_act_fail',
      }),
      jsonResponse({
        machine_id: 'mch_test_1',
        status: 'stopping',
        message: 'ok',
        request_id: 'r',
      }),
      jsonResponse({
        machine_id: 'mch_test_1',
        status: 'terminated',
        message: 'ok',
        request_id: 'r',
      }),
    );

    const error = await runMachineLifecycle(client, { ...baseOptions(), now: () => 0 }).catch(
      (e: unknown) => e,
    );

    expect(error).toBeInstanceOf(MachineOpFailedError);
    expect((error as MachineOpFailedError).message).toContain('no display attached');
    expect((error as MachineOpFailedError).requestId).toBe('req_test_123');
    const calls = fetchMock.calls.map((call) => call.path);
    expect(calls.at(-2)).toBe('/v1/machines/mch_test_1/stop');
    expect(calls.at(-1)).toBe('/v1/machines/mch_test_1');
  });

  it('a cleanup failure is reported but never masks the result (TTL is the backstop)', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(jsonResponse(makeProvisionResponse()), jsonResponse(makeMachine()));
    const tail = tailResponses();
    fetchMock.enqueue(...tail.slice(0, 8)); // screenshot .. snapshot
    fetchMock.enqueue(
      errorResponse(500, 'INTERNAL_ERROR', 'stop hiccup'), // stop fails (POST: 1 attempt)
      jsonResponse({
        machine_id: 'mch_test_1',
        status: 'terminated',
        message: 'ok',
        request_id: 'r',
      }),
    );

    const report = await runMachineLifecycle(client, { ...baseOptions(), now: () => 0 });

    expect(report.operations).not.toContain('stop');
    expect(report.operations).toContain('terminate');
    expect(consoleError).toHaveBeenCalledWith(expect.stringContaining('cleanup "stop" failed'));
  });

  it('spend gate: an unconfirmed LIVE key never reaches the network', async () => {
    const { client, fetchMock }: TestClient = makeClient();

    await expect(
      runMachineLifecycle(client, baseOptions({ sandbox: false, confirmedSpend: false })),
    ).rejects.toBeInstanceOf(SpendNotConfirmedError);
    expect(fetchMock.calls).toHaveLength(0);
  });

  it('ttl guard: out-of-range or fractional TTLs are refused before any request', async () => {
    const { client, fetchMock } = makeClient();
    for (const ttlMinutes of [4, 10081, 7.5, 0, -1]) {
      await expect(runMachineLifecycle(client, baseOptions({ ttlMinutes }))).rejects.toBeInstanceOf(
        TtlGuardError,
      );
    }
    expect(fetchMock.calls).toHaveLength(0);
    expect(() => validateTtlMinutes(5)).not.toThrow();
    expect(() => validateTtlMinutes(10080)).not.toThrow();
  });
});
