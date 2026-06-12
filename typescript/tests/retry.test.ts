/**
 * Retry policy: exponential backoff + full jitter (base 500ms, cap 8s, max 4
 * attempts) on 429/500/503/504 and transport errors, honoring Retry-After.
 * Other 4xx are NEVER retried. POSTs are only retried when inherently safe
 * (predict/ground/parse) or when an Idempotency-Key was provided.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

import { CoastyClient } from '../src/coasty/client.js';
import {
  CoastyError,
  InsufficientCreditsError,
  RateLimitError,
  ServerError,
  ValidationError,
} from '../src/coasty/errors.js';
import {
  FAKE_API_KEY,
  FetchMock,
  SCREENSHOT_B64,
  TEST_BASE_URL,
  errorResponse,
  jsonResponse,
  makeClient,
  makePredictResponse,
  makeRun,
} from './helpers.js';

afterEach(() => {
  vi.useRealTimers();
});

describe('retryable statuses', () => {
  it('retries 429 and honors the Retry-After header (seconds -> ms)', async () => {
    const { client, fetchMock, sleeps } = makeClient();
    fetchMock.enqueue(
      errorResponse(429, 'RATE_LIMITED', 'slow down', {}, { headers: { 'retry-after': '2' } }),
      jsonResponse(makePredictResponse()),
    );

    const { data } = await client.predict({ screenshot: SCREENSHOT_B64, instruction: 'go' });

    expect(data.status).toBe('continue');
    expect(fetchMock.calls).toHaveLength(2);
    expect(sleeps).toEqual([2000]);
  });

  it('falls back to the body retry_after when the header is missing', async () => {
    const { client, fetchMock, sleeps } = makeClient();
    fetchMock.enqueue(
      errorResponse(503, 'UPSTREAM_UNAVAILABLE', 'try later', { retry_after: 1 }),
      jsonResponse(makePredictResponse()),
    );

    await client.predict({ screenshot: SCREENSHOT_B64, instruction: 'go' });

    expect(sleeps).toEqual([1000]);
  });

  it('uses full-jitter exponential backoff when no Retry-After is given', async () => {
    // random() = 1 -> delay is exactly min(cap, base * 2^retryIndex).
    const { client, fetchMock, sleeps } = makeClient({ maxAttempts: 6 });
    for (let i = 0; i < 5; i += 1) fetchMock.enqueue(errorResponse(500, 'INTERNAL_ERROR', 'boom'));
    fetchMock.enqueue(jsonResponse(makePredictResponse()));

    await client.predict({ screenshot: SCREENSHOT_B64, instruction: 'go' });

    expect(fetchMock.calls).toHaveLength(6);
    expect(sleeps).toEqual([500, 1000, 2000, 4000, 8000]); // capped at 8s
  });

  it('scales the jittered delay by the injected random source', async () => {
    const { client, fetchMock, sleeps } = makeClient({ random: () => 0.5 });
    fetchMock.enqueue(
      errorResponse(504, 'UPSTREAM_TIMEOUT', 'timeout'),
      jsonResponse(makePredictResponse()),
    );

    await client.predict({ screenshot: SCREENSHOT_B64, instruction: 'go' });

    expect(sleeps).toEqual([250]); // U(0, 500) with random=0.5
  });

  it('gives up after maxAttempts and throws the typed error', async () => {
    const { client, fetchMock } = makeClient();
    for (let i = 0; i < 4; i += 1) {
      fetchMock.enqueue(errorResponse(503, 'UPSTREAM_UNAVAILABLE', 'down'));
    }

    const error = await client
      .predict({ screenshot: SCREENSHOT_B64, instruction: 'go' })
      .catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ServerError);
    expect((error as ServerError).code).toBe('UPSTREAM_UNAVAILABLE');
    expect(fetchMock.calls).toHaveLength(4); // default max 4 attempts
  });

  it('drives the default setTimeout-based sleep with fake timers', async () => {
    vi.useFakeTimers();
    const fetchMock = new FetchMock();
    fetchMock.enqueue(
      errorResponse(500, 'INTERNAL_ERROR', 'boom'),
      jsonResponse(makePredictResponse()),
    );
    // No injected sleep: the real defaultSleep runs on (faked) setTimeout.
    const client = new CoastyClient({
      apiKey: FAKE_API_KEY,
      baseUrl: TEST_BASE_URL,
      fetch: fetchMock.fetch,
      random: () => 1,
    });

    const pending = client.predict({ screenshot: SCREENSHOT_B64, instruction: 'go' });
    await vi.advanceTimersByTimeAsync(500); // exactly base * 2^0
    const { data } = await pending;

    expect(data.status).toBe('continue');
    expect(fetchMock.calls).toHaveLength(2);
  });
});

describe('non-retryable errors', () => {
  it('does NOT retry 402 — throws InsufficientCreditsError with required/balance', async () => {
    const { client, fetchMock, sleeps } = makeClient();
    fetchMock.enqueue(
      errorResponse(402, 'INSUFFICIENT_CREDITS', 'Top up required', {
        type: 'billing_error',
        required: 5,
        balance: 2,
      }),
    );

    const error = await client
      .predict({ screenshot: SCREENSHOT_B64, instruction: 'go' })
      .catch((e: unknown) => e);

    expect(error).toBeInstanceOf(InsufficientCreditsError);
    expect((error as InsufficientCreditsError).required).toBe(5);
    expect((error as InsufficientCreditsError).balance).toBe(2);
    expect((error as InsufficientCreditsError).requestId).toBe('req_err_123');
    expect(fetchMock.calls).toHaveLength(1);
    expect(sleeps).toEqual([]);
  });

  it.each([
    [400, 'INVALID_LIMIT'],
    [401, 'INVALID_API_KEY'],
    [403, 'INSUFFICIENT_SCOPE'],
    [404, 'NOT_FOUND'],
    [409, 'INVALID_STATE'],
    [413, 'PAYLOAD_TOO_LARGE'],
    [422, 'VALIDATION_ERROR'],
  ] as const)('does NOT retry %i %s', async (status, code) => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(errorResponse(status, code, 'nope'));

    const error = await client
      .predict({ screenshot: SCREENSHOT_B64, instruction: 'go' })
      .catch((e: unknown) => e);

    expect(error).toBeInstanceOf(CoastyError);
    expect((error as CoastyError).code).toBe(code);
    expect(fetchMock.calls).toHaveLength(1);
  });

  it('422 carries the validation details', async () => {
    const { client, fetchMock } = makeClient();
    const details = [{ loc: ['body', 'task'], msg: 'field required' }];
    fetchMock.enqueue(errorResponse(422, 'VALIDATION_ERROR', 'invalid', { details }));

    const error = await client.parse({ code: 'x' }).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ValidationError);
    expect((error as ValidationError).details).toEqual(details);
  });
});

describe('POST retry guard', () => {
  it('retries predict/ground/parse on 500 (inherently safe POSTs)', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(
      errorResponse(500, 'PREDICTION_FAILED', 'boom'),
      jsonResponse(makePredictResponse()),
      errorResponse(500, 'GROUNDING_FAILED', 'boom'),
      jsonResponse({ x: 1, y: 2, usage: makePredictResponse().usage }),
      errorResponse(500, 'INTERNAL_ERROR', 'boom'),
      jsonResponse({ actions: [] }),
    );

    await client.predict({ screenshot: SCREENSHOT_B64, instruction: 'go' });
    await client.ground({ screenshot: SCREENSHOT_B64, element: 'button' });
    await client.parse({ code: 'pyautogui.click(1, 2)' });

    expect(fetchMock.calls).toHaveLength(6);
  });

  it('does NOT retry an unguarded POST create on 500', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(errorResponse(500, 'INTERNAL_ERROR', 'boom'));

    const error = await client.runs.create({ machine_id: 'm', task: 't' }).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ServerError);
    expect(fetchMock.calls).toHaveLength(1);
  });

  it('retries a POST create when an Idempotency-Key was provided', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(errorResponse(500, 'INTERNAL_ERROR', 'boom'), jsonResponse(makeRun()));

    await client.runs.create({ machine_id: 'm', task: 't' }, { idempotencyKey: 'order-1' });

    expect(fetchMock.calls).toHaveLength(2);
    expect(fetchMock.calls[0]?.headers.get('idempotency-key')).toBe('order-1');
    expect(fetchMock.calls[1]?.headers.get('idempotency-key')).toBe('order-1');
  });

  it('does NOT retry an unguarded session predict on 429 (server state advances)', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(errorResponse(429, 'RATE_LIMITED', 'slow down'));

    const error = await client.sessions
      .predict('s', { screenshot: SCREENSHOT_B64, instruction: 'go' })
      .catch((e: unknown) => e);

    expect(error).toBeInstanceOf(RateLimitError);
    expect(fetchMock.calls).toHaveLength(1);
  });

  it('never retries PATCH', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(errorResponse(500, 'INTERNAL_ERROR', 'boom'));

    const error = await client.machines.patchTtl('mch_1', 30).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ServerError);
    expect(fetchMock.calls).toHaveLength(1);
  });

  it('retries idempotent GETs', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(errorResponse(503, 'UPSTREAM_UNAVAILABLE', 'down'), jsonResponse(makeRun()));

    const { data } = await client.runs.get('run_1');

    expect(data.id).toBe('run_test_1');
    expect(fetchMock.calls).toHaveLength(2);
  });
});

describe('transport errors', () => {
  it('retries network failures and eventually succeeds', async () => {
    const { client, fetchMock, sleeps } = makeClient();
    fetchMock.enqueue(() => {
      throw new TypeError('fetch failed');
    }, jsonResponse(makePredictResponse()));

    const { data } = await client.predict({ screenshot: SCREENSHOT_B64, instruction: 'go' });

    expect(data.status).toBe('continue');
    expect(fetchMock.calls).toHaveLength(2);
    expect(sleeps).toEqual([500]);
  });

  it('wraps exhausted network failures in a NETWORK_ERROR CoastyError', async () => {
    const { client, fetchMock } = makeClient();
    for (let i = 0; i < 4; i += 1) {
      fetchMock.enqueue(() => {
        throw new TypeError('fetch failed');
      });
    }

    const error = await client
      .predict({ screenshot: SCREENSHOT_B64, instruction: 'go' })
      .catch((e: unknown) => e);

    expect(error).toBeInstanceOf(CoastyError);
    expect((error as CoastyError).code).toBe('NETWORK_ERROR');
    expect((error as CoastyError).cause).toBeInstanceOf(TypeError);
    expect(fetchMock.calls).toHaveLength(4);
  });

  it('does NOT retry a network failure on an unguarded POST', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(() => {
      throw new TypeError('fetch failed');
    });

    const error = await client.runs.create({ machine_id: 'm', task: 't' }).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(CoastyError);
    expect((error as CoastyError).code).toBe('NETWORK_ERROR');
    expect(fetchMock.calls).toHaveLength(1);
  });
});
