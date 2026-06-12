/**
 * Example 02 tests — ground an element, then click it through the injected
 * backend. Fully offline via the queued FetchMock.
 */
import { describe, expect, it } from 'vitest';
import { NullBackend } from '../../src/coasty/executor.js';
import { type PrintFn } from '../../src/examples/ex01-local-predict-loop.js';
import { groundAndClick, parseArgs } from '../../src/examples/ex02-grounding.js';
import { SCREENSHOT_B64, errorResponse, jsonResponse, makeClient, makeUsage } from '../helpers.js';

const provider = (): Promise<string> => Promise.resolve(SCREENSHOT_B64);
const silent: PrintFn = () => undefined;

describe('groundAndClick', () => {
  it('grounds the element then clicks the returned point', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(
      jsonResponse({ x: 200, y: 100, usage: makeUsage({ credits_charged: 3, cost_cents: 3 }) }),
    );
    const backend = new NullBackend();

    const result = await groundAndClick({
      client,
      element: 'the blue Submit button',
      screenshot: provider,
      backend,
      print: silent,
    });

    expect(result).toEqual({ x: 200, y: 100, creditsCharged: 3, requestId: 'req_test_123' });
    expect(backend.calls).toEqual([
      { method: 'click', args: [200, 100, { button: 'left', clicks: 1 }] },
    ]);

    expect(fetchMock.calls).toHaveLength(1);
    const call = fetchMock.calls[0];
    expect(call?.method).toBe('POST');
    expect(call?.path).toBe('/v1/ground');
    const body = call?.body as Record<string, unknown>;
    expect(body.element).toBe('the blue Submit button');
    expect(body.screenshot).toBe(SCREENSHOT_B64);
    expect(body.screen_width).toBe(1280);
    expect(body.screen_height).toBe(720);
  });

  it('scales coordinates by real/sent factors before clicking', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(jsonResponse({ x: 200, y: 100, usage: makeUsage() }));
    const backend = new NullBackend();

    // Simulates a desktop backend that downscaled a 2560x1080 capture to
    // 1280x720 before sending: model coords must be multiplied back up.
    await groundAndClick({
      client,
      element: 'the gear icon',
      screenshot: provider,
      backend,
      scaleX: 2,
      scaleY: 1.5,
      print: silent,
    });

    expect(backend.calls).toEqual([
      { method: 'click', args: [400, 150, { button: 'left', clicks: 1 }] },
    ]);
  });

  it('propagates API errors with the request_id and does not click', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(
      errorResponse(403, 'INSUFFICIENT_SCOPE', 'key lacks the ground scope', {
        type: 'auth_error',
        required_scope: 'ground',
      }),
    );
    const backend = new NullBackend();

    await expect(
      groundAndClick({
        client,
        element: 'anything',
        screenshot: provider,
        backend,
        print: silent,
      }),
    ).rejects.toMatchObject({
      code: 'INSUFFICIENT_SCOPE',
      requestId: 'req_err_123',
      requiredScope: 'ground',
    });
    expect(backend.calls).toEqual([]); // nothing executed on failure
  });
});

describe('parseArgs', () => {
  it('reads flags and env defaults', () => {
    const config = parseArgs(['--element', 'the login button', '--confirm'], {
      EX02_URL: 'https://internal.example',
    });
    expect(config).toEqual({
      url: 'https://internal.example',
      element: 'the login button',
      headless: true,
      confirm: true,
    });
  });

  it('rejects unknown arguments', () => {
    expect(() => parseArgs(['--nope'], {})).toThrow(/unknown argument/);
  });
});
