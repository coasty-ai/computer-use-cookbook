/**
 * Webhook signature verification for `Coasty-Signature: t=<unix>,v1=<hex>`.
 *
 * Signed payload = `"<t>." + raw_body`;
 * `v1 = hex(HMAC_SHA256(webhook_secret, signed_payload))`.
 *
 * Verification uses `crypto.timingSafeEqual` (constant-time, with an
 * equal-length guard) and enforces a timestamp tolerance (default ±300s, the
 * documented replay window). Malformed input returns `false` — this function
 * never throws.
 */
import { createHmac, timingSafeEqual } from 'node:crypto';

export const DEFAULT_TOLERANCE_SECONDS = 300;

export interface VerifySignatureOptions {
  /** Allowed |now - t| skew in seconds (default 300). */
  toleranceSeconds?: number;
  /** Unix seconds "now" override (for tests); defaults to wall clock. */
  now?: number;
}

const TIMESTAMP_PATTERN = /^\d{1,15}$/;
const HEX_SIGNATURE_PATTERN = /^[0-9a-fA-F]{64}$/;

/**
 * Verify a `Coasty-Signature` header against the raw (unparsed!) request body.
 *
 * @param rawBody  The exact request bytes. Pass the raw body — re-serialized
 *                 JSON will not match.
 * @param header   The `Coasty-Signature` header value (`t=...,v1=...`).
 * @param secret   The per-run/per-trigger `webhook_secret` (shown once on create).
 * @returns `true` only when the signature is valid AND the timestamp is within
 *          tolerance. Never throws.
 */
export function verifySignature(
  rawBody: Uint8Array | string,
  header: string,
  secret: string,
  options: VerifySignatureOptions = {},
): boolean {
  try {
    if (typeof header !== 'string' || typeof secret !== 'string' || secret.length === 0) {
      return false;
    }

    const parts = new Map<string, string>();
    for (const piece of header.split(',')) {
      const separator = piece.indexOf('=');
      if (separator === -1) continue;
      const key = piece.slice(0, separator).trim();
      const value = piece.slice(separator + 1).trim();
      if (!parts.has(key)) parts.set(key, value);
    }

    const timestamp = parts.get('t');
    const signature = parts.get('v1');
    if (timestamp === undefined || signature === undefined) return false;
    if (!TIMESTAMP_PATTERN.test(timestamp)) return false;
    if (!HEX_SIGNATURE_PATTERN.test(signature)) return false;

    const tolerance = options.toleranceSeconds ?? DEFAULT_TOLERANCE_SECONDS;
    const now = options.now ?? Math.floor(Date.now() / 1000);
    if (Math.abs(now - Number(timestamp)) > tolerance) return false;

    const bodyBytes =
      typeof rawBody === 'string' ? Buffer.from(rawBody, 'utf8') : Buffer.from(rawBody);
    const signedPayload = Buffer.concat([Buffer.from(`${timestamp}.`, 'utf8'), bodyBytes]);
    const expected = createHmac('sha256', secret).update(signedPayload).digest();
    const provided = Buffer.from(signature, 'hex');

    // Equal-length guard: timingSafeEqual throws on mismatched lengths.
    if (provided.length !== expected.length) return false;
    return timingSafeEqual(expected, provided);
  } catch {
    return false;
  }
}

/**
 * Compute the `Coasty-Signature` header for a payload — used by the mock
 * server and tests to emit correctly signed webhooks.
 */
export function signPayload(
  rawBody: Uint8Array | string,
  secret: string,
  timestamp: number,
): string {
  const bodyBytes =
    typeof rawBody === 'string' ? Buffer.from(rawBody, 'utf8') : Buffer.from(rawBody);
  const signedPayload = Buffer.concat([Buffer.from(`${String(timestamp)}.`, 'utf8'), bodyBytes]);
  const signature = createHmac('sha256', secret).update(signedPayload).digest('hex');
  return `t=${String(timestamp)},v1=${signature}`;
}
