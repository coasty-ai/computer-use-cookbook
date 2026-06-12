/**
 * ex06 — webhook receiver: the pure handleWebhook core against the SHARED
 * HMAC vectors (docs/API_NOTES.md), the dispatch table, and one test driving
 * the REAL node:http server on a 127.0.0.1 ephemeral port (loopback only).
 */
import { type Server } from 'node:http';
import { afterEach, describe, expect, it } from 'vitest';

import {
  RUN_WEBHOOK_EVENTS,
  createWebhookServer,
  dispatchWebhookEvent,
  handleWebhook,
  type RunWebhookEvent,
} from '../../src/examples/ex06-webhook-server.js';
import { signPayload } from '../../src/coasty/webhooks.js';
import { HMAC_VECTOR_1, HMAC_VECTOR_2 } from '../helpers.js';

const NOW_1 = HMAC_VECTOR_1.timestamp;

describe('handleWebhook — shared vectors', () => {
  it('accepts vector 1 and surfaces the run.succeeded event', () => {
    const outcome = handleWebhook(
      HMAC_VECTOR_1.rawBody,
      HMAC_VECTOR_1.header,
      HMAC_VECTOR_1.secret,
      NOW_1,
    );
    expect(outcome.status).toBe(200);
    expect(outcome.disposition).toBe('ok');
    expect(outcome.event).toEqual({
      event: 'run.succeeded',
      run_id: 'run_123',
      status: 'succeeded',
      extra: {},
    });
  });

  it('accepts vector 2 (second key) and surfaces run.awaiting_human with its reason', () => {
    const outcome = handleWebhook(
      HMAC_VECTOR_2.rawBody,
      HMAC_VECTOR_2.header,
      HMAC_VECTOR_2.secret,
      HMAC_VECTOR_2.timestamp,
    );
    expect(outcome.status).toBe(200);
    expect(outcome.event?.event).toBe('run.awaiting_human');
    expect(outcome.event?.run_id).toBe('run_456');
    expect(outcome.event?.reason).toBe('captcha');
  });

  it('accepts the raw body as bytes', () => {
    const bytes = new TextEncoder().encode(HMAC_VECTOR_1.rawBody);
    const outcome = handleWebhook(bytes, HMAC_VECTOR_1.header, HMAC_VECTOR_1.secret, NOW_1);
    expect(outcome.status).toBe(200);
    expect(outcome.event?.run_id).toBe('run_123');
  });

  it('(a) answers 401 for a tampered body', () => {
    const tampered = HMAC_VECTOR_1.rawBody.replace('run_123', 'run_124');
    const outcome = handleWebhook(tampered, HMAC_VECTOR_1.header, HMAC_VECTOR_1.secret, NOW_1);
    expect(outcome).toEqual({ status: 401, disposition: 'invalid_signature' });
  });

  it('(b) answers 401 for a stale timestamp (now > t + 300s)', () => {
    const outcome = handleWebhook(
      HMAC_VECTOR_1.rawBody,
      HMAC_VECTOR_1.header,
      HMAC_VECTOR_1.secret,
      NOW_1 + 301,
    );
    expect(outcome).toEqual({ status: 401, disposition: 'invalid_signature' });
  });

  it('(c) answers 401 for malformed or missing headers', () => {
    const malformed = [
      undefined,
      '',
      'garbage',
      `v1=${HMAC_VECTOR_1.v1}`,
      `t=${String(NOW_1)}`,
      `t:${String(NOW_1)};v1:${HMAC_VECTOR_1.v1}`,
    ];
    for (const header of malformed) {
      expect(handleWebhook(HMAC_VECTOR_1.rawBody, header, HMAC_VECTOR_1.secret, NOW_1)).toEqual({
        status: 401,
        disposition: 'invalid_signature',
      });
    }
  });

  it("(d) answers 401 when signed with the wrong key (vector 2's secret)", () => {
    const wrongKeyHeader = signPayload(HMAC_VECTOR_1.rawBody, HMAC_VECTOR_2.secret, NOW_1);
    const outcome = handleWebhook(
      HMAC_VECTOR_1.rawBody,
      wrongKeyHeader,
      HMAC_VECTOR_1.secret,
      NOW_1,
    );
    expect(outcome.status).toBe(401);
  });

  it('answers 400 for a correctly signed but non-JSON body', () => {
    const body = 'this is not json';
    const header = signPayload(body, HMAC_VECTOR_1.secret, NOW_1);
    const outcome = handleWebhook(body, header, HMAC_VECTOR_1.secret, NOW_1);
    expect(outcome).toEqual({ status: 400, disposition: 'invalid_payload' });
  });

  it('answers 400 for a signed JSON body missing event/run_id', () => {
    for (const body of ['{}', '{"event":"run.succeeded"}', '{"run_id":"run_1"}', '[1,2]']) {
      const header = signPayload(body, HMAC_VECTOR_1.secret, NOW_1);
      expect(handleWebhook(body, header, HMAC_VECTOR_1.secret, NOW_1).status).toBe(400);
    }
  });

  it('acks (200) but does not dispatch an unknown event name', () => {
    const body = '{"event":"run.exploded","run_id":"run_999"}';
    const header = signPayload(body, HMAC_VECTOR_1.secret, NOW_1);
    const outcome = handleWebhook(body, header, HMAC_VECTOR_1.secret, NOW_1);
    expect(outcome.status).toBe(200);
    expect(outcome.disposition).toBe('ignored_unknown_event');
    expect(outcome.event).toBeUndefined();
  });

  it('signature is checked BEFORE the payload (tampered garbage stays 401, not 400)', () => {
    const outcome = handleWebhook('not json', HMAC_VECTOR_1.header, HMAC_VECTOR_1.secret, NOW_1);
    expect(outcome.status).toBe(401);
  });
});

describe('dispatchWebhookEvent', () => {
  const baseEvent = (event: RunWebhookEvent['event']): RunWebhookEvent => ({
    event,
    run_id: 'run_42',
    extra: {},
  });

  it('covers all five documented events', () => {
    for (const name of RUN_WEBHOOK_EVENTS) {
      const line = dispatchWebhookEvent(baseEvent(name));
      expect(line).toContain('run_42');
    }
  });

  it('run.awaiting_human points at client.runs.resume(...)', () => {
    const line = dispatchWebhookEvent({ ...baseEvent('run.awaiting_human'), reason: 'captcha' });
    expect(line).toContain("client.runs.resume('run_42'");
    expect(line).toContain('captcha');
  });

  it('run.succeeded points at fetching the result', () => {
    expect(dispatchWebhookEvent(baseEvent('run.succeeded'))).toContain("runs.get('run_42')");
  });
});

describe('webhook server on 127.0.0.1 (loopback)', () => {
  let server: Server | undefined;

  afterEach(async () => {
    if (server !== undefined) {
      await new Promise<void>((resolve, reject) => {
        server?.close((error) => (error === undefined ? resolve() : reject(error)));
      });
      server = undefined;
    }
  });

  it('verifies, acks 200 fast, dispatches; rejects tampered deliveries with 401', async () => {
    const received: RunWebhookEvent[] = [];
    const logLines: string[] = [];
    let resolveEvent: ((event: RunWebhookEvent) => void) | undefined;
    const firstEvent = new Promise<RunWebhookEvent>((resolve) => {
      resolveEvent = resolve;
    });

    server = createWebhookServer({
      secret: HMAC_VECTOR_1.secret,
      now: () => NOW_1, // pinned clock so the shared vector is in-tolerance
      onEvent: (event) => {
        received.push(event);
        resolveEvent?.(event);
      },
      logger: (line) => logLines.push(line),
    });
    const listening = server;
    await new Promise<void>((resolve) => listening.listen(0, '127.0.0.1', resolve));
    const address = listening.address();
    if (address === null || typeof address === 'string') throw new Error('no bound port');
    const url = `http://127.0.0.1:${String(address.port)}/webhooks/coasty`;

    // 1. A valid delivery (shared vector 1) -> 200, then dispatched.
    const ok = await fetch(url, {
      method: 'POST',
      headers: { 'coasty-signature': HMAC_VECTOR_1.header, 'content-type': 'application/json' },
      body: HMAC_VECTOR_1.rawBody,
    });
    expect(ok.status).toBe(200);
    const event = await firstEvent;
    expect(event.event).toBe('run.succeeded');
    expect(event.run_id).toBe('run_123');

    // 2. An awaiting_human delivery signed with the SERVER's secret -> the
    //    dispatch log shows where runs.resume would be called.
    const pauseBody = HMAC_VECTOR_2.rawBody;
    const pauseHeader = signPayload(pauseBody, HMAC_VECTOR_1.secret, NOW_1);
    const paused = await fetch(url, {
      method: 'POST',
      headers: { 'coasty-signature': pauseHeader, 'content-type': 'application/json' },
      body: pauseBody,
    });
    expect(paused.status).toBe(200);
    await new Promise((resolve) => setImmediate(resolve)); // let dispatch flush
    expect(logLines.some((line) => line.includes('client.runs.resume('))).toBe(true);

    // 3. Tampered body -> 401, NOT dispatched.
    const tampered = await fetch(url, {
      method: 'POST',
      headers: { 'coasty-signature': HMAC_VECTOR_1.header, 'content-type': 'application/json' },
      body: HMAC_VECTOR_1.rawBody.replace('run_123', 'run_124'),
    });
    expect(tampered.status).toBe(401);

    // 4. Missing signature header -> 401.
    const unsigned = await fetch(url, { method: 'POST', body: HMAC_VECTOR_1.rawBody });
    expect(unsigned.status).toBe(401);

    // 5. Non-POST -> 405.
    const wrongMethod = await fetch(url, { method: 'GET' });
    expect(wrongMethod.status).toBe(405);

    await new Promise((resolve) => setImmediate(resolve));
    expect(received).toHaveLength(2); // only the two verified deliveries
  });
});
