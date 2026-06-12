/**
 * webhooks.ts — HMAC verification against the EXACT shared vectors from
 * docs/API_NOTES.md (same vectors in every language track), plus the
 * documented negative cases. "Now" is pinned in every test for determinism.
 */
import { describe, expect, it } from 'vitest';

import { DEFAULT_TOLERANCE_SECONDS, signPayload, verifySignature } from '../src/coasty/webhooks.js';
import { HMAC_VECTOR_1, HMAC_VECTOR_2 } from './helpers.js';

describe('verifySignature — shared vectors', () => {
  it('accepts vector 1', () => {
    expect(
      verifySignature(HMAC_VECTOR_1.rawBody, HMAC_VECTOR_1.header, HMAC_VECTOR_1.secret, {
        now: HMAC_VECTOR_1.timestamp,
      }),
    ).toBe(true);
  });

  it('accepts vector 2 (second key)', () => {
    expect(
      verifySignature(HMAC_VECTOR_2.rawBody, HMAC_VECTOR_2.header, HMAC_VECTOR_2.secret, {
        now: HMAC_VECTOR_2.timestamp,
      }),
    ).toBe(true);
  });

  it('accepts the raw body as bytes (Uint8Array)', () => {
    const bytes = new TextEncoder().encode(HMAC_VECTOR_1.rawBody);
    expect(
      verifySignature(bytes, HMAC_VECTOR_1.header, HMAC_VECTOR_1.secret, {
        now: HMAC_VECTOR_1.timestamp,
      }),
    ).toBe(true);
  });

  it('(a) rejects a tampered body (single byte flipped)', () => {
    const tampered = HMAC_VECTOR_1.rawBody.replace('succeeded', 'succeeren');
    expect(
      verifySignature(tampered, HMAC_VECTOR_1.header, HMAC_VECTOR_1.secret, {
        now: HMAC_VECTOR_1.timestamp,
      }),
    ).toBe(false);
  });

  it('(b) rejects a stale timestamp (t outside ±300s of pinned now)', () => {
    // Valid signature, but "now" is 301s after t.
    expect(
      verifySignature(HMAC_VECTOR_1.rawBody, HMAC_VECTOR_1.header, HMAC_VECTOR_1.secret, {
        now: HMAC_VECTOR_1.timestamp + 301,
      }),
    ).toBe(false);
  });

  it('(b) rejects a future timestamp (t ahead of pinned now)', () => {
    expect(
      verifySignature(HMAC_VECTOR_1.rawBody, HMAC_VECTOR_1.header, HMAC_VECTOR_1.secret, {
        now: HMAC_VECTOR_1.timestamp - 301,
      }),
    ).toBe(false);
  });

  it('accepts exactly ±300s (tolerance boundary is inclusive)', () => {
    expect(DEFAULT_TOLERANCE_SECONDS).toBe(300);
    for (const skew of [300, -300]) {
      expect(
        verifySignature(HMAC_VECTOR_1.rawBody, HMAC_VECTOR_1.header, HMAC_VECTOR_1.secret, {
          now: HMAC_VECTOR_1.timestamp + skew,
        }),
      ).toBe(true);
    }
  });

  it('(c) rejects malformed headers without throwing', () => {
    const malformed = [
      '',
      'garbage',
      `v1=${HMAC_VECTOR_1.v1}`, // missing t=
      `t=${String(HMAC_VECTOR_1.timestamp)}`, // missing v1=
      `t=,v1=${HMAC_VECTOR_1.v1}`, // empty t
      `t=${String(HMAC_VECTOR_1.timestamp)},v1=`, // empty v1
      `t=notanumber,v1=${HMAC_VECTOR_1.v1}`,
      `t=${String(HMAC_VECTOR_1.timestamp)},v1=nothex`,
      `t=${String(HMAC_VECTOR_1.timestamp)},v1=${HMAC_VECTOR_1.v1.slice(0, 32)}`, // short sig
      `t:${String(HMAC_VECTOR_1.timestamp)};v1:${HMAC_VECTOR_1.v1}`, // wrong separators
    ];
    for (const header of malformed) {
      expect(
        verifySignature(HMAC_VECTOR_1.rawBody, header, HMAC_VECTOR_1.secret, {
          now: HMAC_VECTOR_1.timestamp,
        }),
      ).toBe(false);
    }
  });

  it('(d) rejects a signature computed with the wrong secret', () => {
    // Vector 1's body/timestamp signed with vector 2's secret.
    const wrongSecretHeader = signPayload(
      HMAC_VECTOR_1.rawBody,
      HMAC_VECTOR_2.secret,
      HMAC_VECTOR_1.timestamp,
    );
    expect(
      verifySignature(HMAC_VECTOR_1.rawBody, wrongSecretHeader, HMAC_VECTOR_1.secret, {
        now: HMAC_VECTOR_1.timestamp,
      }),
    ).toBe(false);
  });

  it('rejects an empty secret', () => {
    expect(
      verifySignature(HMAC_VECTOR_1.rawBody, HMAC_VECTOR_1.header, '', {
        now: HMAC_VECTOR_1.timestamp,
      }),
    ).toBe(false);
  });

  it('never throws on adversarial non-string inputs', () => {
    const badHeader = verifySignature(
      HMAC_VECTOR_1.rawBody,
      undefined as unknown as string,
      HMAC_VECTOR_1.secret,
      { now: HMAC_VECTOR_1.timestamp },
    );
    const badSecret = verifySignature(
      HMAC_VECTOR_1.rawBody,
      HMAC_VECTOR_1.header,
      null as unknown as string,
      { now: HMAC_VECTOR_1.timestamp },
    );
    expect(badHeader).toBe(false);
    expect(badSecret).toBe(false);
  });

  it('uses the first occurrence of duplicated header fields', () => {
    const duplicated = `${HMAC_VECTOR_1.header},t=9999999999,v1=${'0'.repeat(64)}`;
    expect(
      verifySignature(HMAC_VECTOR_1.rawBody, duplicated, HMAC_VECTOR_1.secret, {
        now: HMAC_VECTOR_1.timestamp,
      }),
    ).toBe(true);
  });

  it('honors a custom toleranceSeconds', () => {
    expect(
      verifySignature(HMAC_VECTOR_1.rawBody, HMAC_VECTOR_1.header, HMAC_VECTOR_1.secret, {
        now: HMAC_VECTOR_1.timestamp + 30,
        toleranceSeconds: 10,
      }),
    ).toBe(false);
  });
});

describe('signPayload', () => {
  it('reproduces vector 1 exactly', () => {
    expect(signPayload(HMAC_VECTOR_1.rawBody, HMAC_VECTOR_1.secret, HMAC_VECTOR_1.timestamp)).toBe(
      HMAC_VECTOR_1.header,
    );
  });

  it('reproduces vector 2 exactly', () => {
    expect(signPayload(HMAC_VECTOR_2.rawBody, HMAC_VECTOR_2.secret, HMAC_VECTOR_2.timestamp)).toBe(
      HMAC_VECTOR_2.header,
    );
  });

  it('round-trips through verifySignature', () => {
    const body = '{"event":"run.failed","run_id":"run_789"}';
    const header = signPayload(body, 'whsec_roundtrip', 1750000500);
    expect(verifySignature(body, header, 'whsec_roundtrip', { now: 1750000500 })).toBe(true);
  });
});
