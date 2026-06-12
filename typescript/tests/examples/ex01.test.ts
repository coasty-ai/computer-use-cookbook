/**
 * Example 01 tests — the predict loop with a fake screenshot provider and
 * backend. Fully offline: HTTP is a queued FetchMock, no real screen.
 */
import { describe, expect, it } from 'vitest';
import { CoastyError } from '../../src/coasty/errors.js';
import { NullBackend, executeActions } from '../../src/coasty/executor.js';
import {
  createPageBackend,
  createPageScreenshotProvider,
  runPredictLoop,
  toPlaywrightKey,
  type PageLike,
  type PrintFn,
} from '../../src/examples/ex01-local-predict-loop.js';
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

describe('runPredictLoop', () => {
  it('executes actions each step and stops when the model says done', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(
      jsonResponse(makePredictResponse({ status: 'continue', actions: [makeClickAction()] })),
      jsonResponse(
        makePredictResponse({
          status: 'continue',
          actions: [{ action_type: 'type_text', params: { text: 'hello' } }],
        }),
      ),
      jsonResponse(
        makePredictResponse({ status: 'done', actions: [{ action_type: 'done', params: {} }] }),
      ),
    );
    const backend = new NullBackend();

    const result = await runPredictLoop({
      client,
      instruction: 'fill the form',
      screenshot: provider,
      backend,
      maxSteps: 5,
      print: silent,
    });

    expect(result.status).toBe('done');
    expect(result.stepsUsed).toBe(3);
    expect(result.creditsCharged).toBe(18); // 3 x 6 cr from makeUsage()
    expect(result.lastRequestId).toBe('req_predict_1');
    expect(backend.calls.map((call) => call.method)).toEqual(['click', 'typeText']);

    expect(fetchMock.calls).toHaveLength(3);
    const body = fetchMock.calls[0]?.body as Record<string, unknown>;
    expect(fetchMock.calls[0]?.path).toBe('/v1/predict');
    expect(body.instruction).toBe('fill the form');
    expect(body.screenshot).toBe(SCREENSHOT_B64);
    expect(body.screen_width).toBe(1280);
    expect(body.screen_height).toBe(720);
    expect(body.cua_version).toBe('v3');
  });

  it('sends a distinct Idempotency-Key on every step', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(
      jsonResponse(makePredictResponse()),
      jsonResponse(makePredictResponse()),
      jsonResponse(makePredictResponse({ status: 'done', actions: [] })),
    );

    await runPredictLoop({
      client,
      instruction: 'go',
      screenshot: provider,
      backend: new NullBackend(),
      maxSteps: 5,
      print: silent,
    });

    const keys = fetchMock.calls.map((call) => call.headers.get('idempotency-key'));
    expect(keys).toHaveLength(3);
    for (const key of keys) {
      expect(key).toMatch(/^ex01-step\d+-/);
    }
    expect(new Set(keys).size).toBe(3); // all distinct
  });

  it('stops on a fail status and surfaces the fail reason', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(
      jsonResponse(
        makePredictResponse({
          status: 'fail',
          actions: [{ action_type: 'fail', params: { reason: 'paywall blocked the page' } }],
        }),
      ),
    );

    const result = await runPredictLoop({
      client,
      instruction: 'go',
      screenshot: provider,
      backend: new NullBackend(),
      maxSteps: 5,
      print: silent,
    });

    expect(result.status).toBe('fail');
    expect(result.stepsUsed).toBe(1);
    expect(result.failReason).toBe('paywall blocked the page');
  });

  it('stops on a terminal done ACTION even when status still says continue', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(
      jsonResponse(
        makePredictResponse({
          status: 'continue',
          actions: [makeClickAction(), { action_type: 'done', params: {} }],
        }),
      ),
    );
    const backend = new NullBackend();

    const result = await runPredictLoop({
      client,
      instruction: 'go',
      screenshot: provider,
      backend,
      maxSteps: 5,
      print: silent,
    });

    expect(result.status).toBe('done');
    expect(backend.calls.map((call) => call.method)).toEqual(['click']);
    expect(fetchMock.calls).toHaveLength(1);
  });

  it('respects --max-steps and reports max_steps', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(jsonResponse(makePredictResponse()), jsonResponse(makePredictResponse()));

    const result = await runPredictLoop({
      client,
      instruction: 'go',
      screenshot: provider,
      backend: new NullBackend(),
      maxSteps: 2,
      print: silent,
    });

    expect(result.status).toBe('max_steps');
    expect(result.stepsUsed).toBe(2);
    expect(fetchMock.calls).toHaveLength(2); // never a third /predict
  });

  it('propagates API errors with the request_id attached', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(
      errorResponse(422, 'INVALID_SCREENSHOT', 'screenshot is not valid base64', {
        type: 'validation_error',
      }),
    );

    const attempt = runPredictLoop({
      client,
      instruction: 'go',
      screenshot: provider,
      backend: new NullBackend(),
      maxSteps: 2,
      print: silent,
    });

    await expect(attempt).rejects.toMatchObject({
      code: 'INVALID_SCREENSHOT',
      requestId: 'req_err_123',
    });
    await expect(attempt).rejects.toBeInstanceOf(CoastyError);
  });
});

// ---------------------------------------------------------------------------
// PageLike adapters
// ---------------------------------------------------------------------------

interface RecordedPageCall {
  method: string;
  args: unknown[];
}

function fakePage(): { page: PageLike; calls: RecordedPageCall[] } {
  const calls: RecordedPageCall[] = [];
  const record =
    (method: string) =>
    (...args: unknown[]): Promise<void> => {
      calls.push({ method, args });
      return Promise.resolve();
    };
  const page: PageLike = {
    screenshot: () => Promise.resolve(new Uint8Array([1, 2, 3])),
    mouse: {
      click: record('mouse.click'),
      move: record('mouse.move'),
      down: record('mouse.down'),
      up: record('mouse.up'),
      wheel: record('mouse.wheel'),
    },
    keyboard: {
      type: record('keyboard.type'),
      press: record('keyboard.press'),
    },
  };
  return { page, calls };
}

describe('createPageBackend', () => {
  it('maps executor actions onto Playwright-style mouse/keyboard calls', async () => {
    const { page, calls } = fakePage();
    const backend = createPageBackend(page);

    await executeActions(
      [
        { action_type: 'click', params: { x: 10, y: 20 } },
        { action_type: 'key_press', params: { key: 'enter' } },
        { action_type: 'key_combo', params: { keys: ['ctrl', 's'] } },
        { action_type: 'scroll', params: { clicks: -3 } }, // pyautogui shape: negative = down
        { action_type: 'drag', params: { x1: 1, y1: 2, x2: 3, y2: 4 } },
      ],
      backend,
      { logger: silent },
    );

    expect(calls).toEqual([
      { method: 'mouse.click', args: [10, 20, { button: 'left', clickCount: 1 }] },
      { method: 'keyboard.press', args: ['Enter'] },
      { method: 'keyboard.press', args: ['Control+s'] },
      { method: 'mouse.wheel', args: [0, 300] }, // down 3 x 100px
      { method: 'mouse.move', args: [1, 2] },
      { method: 'mouse.down', args: [] },
      { method: 'mouse.move', args: [3, 4] },
      { method: 'mouse.up', args: [] },
    ]);
  });

  it('scrolls up with a negative wheel delta', async () => {
    const { page, calls } = fakePage();
    const backend = createPageBackend(page);
    await executeActions(
      [{ action_type: 'scroll', params: { direction: 'up', amount: 2 } }],
      backend,
      { logger: silent },
    );
    expect(calls).toEqual([{ method: 'mouse.wheel', args: [0, -200] }]);
  });
});

describe('createPageScreenshotProvider', () => {
  it('returns raw base64 with no data: prefix', async () => {
    const { page } = fakePage();
    const screenshot = createPageScreenshotProvider(page);
    await expect(screenshot()).resolves.toBe('AQID'); // base64 of [1, 2, 3]
  });
});

describe('toPlaywrightKey', () => {
  it('maps Coasty/pyautogui key names to Playwright names', () => {
    expect(toPlaywrightKey('enter')).toBe('Enter');
    expect(toPlaywrightKey('esc')).toBe('Escape');
    expect(toPlaywrightKey('ctrl')).toBe('Control');
    expect(toPlaywrightKey('pagedown')).toBe('PageDown');
    expect(toPlaywrightKey('a')).toBe('a');
    expect(toPlaywrightKey('f5')).toBe('F5');
  });
});
