/**
 * errors.ts — envelope parsing, status/code -> class mapping, context extras,
 * request-id propagation, and tolerance for non-JSON bodies.
 */
import { describe, expect, it } from 'vitest';

import {
  AuthenticationError,
  CoastyError,
  ConflictError,
  InsufficientCreditsError,
  InsufficientScopeError,
  NotFoundError,
  RateLimitError,
  ServerError,
  ValidationError,
  errorFromResponse,
} from '../src/coasty/errors.js';

function envelope(
  status: number,
  error: Record<string, unknown>,
  headers: Record<string, string> = {},
): CoastyError {
  return errorFromResponse(status, new Headers(headers), JSON.stringify({ error }));
}

describe('errorFromResponse — class mapping by status', () => {
  it.each([
    [401, 'INVALID_API_KEY', AuthenticationError, 'auth_error'],
    [402, 'INSUFFICIENT_CREDITS', InsufficientCreditsError, 'billing_error'],
    [403, 'INSUFFICIENT_SCOPE', InsufficientScopeError, 'auth_error'],
    [404, 'RUN_NOT_FOUND', NotFoundError, 'not_found_error'],
    [409, 'NOT_AWAITING_HUMAN', ConflictError, 'state_error'],
    [422, 'VALIDATION_ERROR', ValidationError, 'validation_error'],
    [413, 'PAYLOAD_TOO_LARGE', ValidationError, 'validation_error'],
    [400, 'INVALID_LIMIT', ValidationError, 'validation_error'],
    [429, 'RATE_LIMITED', RateLimitError, 'rate_limit_error'],
    [500, 'INTERNAL_ERROR', ServerError, 'server_error'],
    [503, 'UPSTREAM_UNAVAILABLE', ServerError, 'server_error'],
    [504, 'UPSTREAM_TIMEOUT', ServerError, 'server_error'],
  ] as const)('%i %s -> %o', (status, code, expectedClass, expectedType) => {
    const error = envelope(status, { code, message: 'boom', type: expectedType });
    expect(error).toBeInstanceOf(expectedClass);
    expect(error).toBeInstanceOf(CoastyError);
    expect(error.code).toBe(code);
    expect(error.statusCode).toBe(status);
    expect(error.errorType).toBe(expectedType);
  });

  it('treats the CODE as canonical when status disagrees (IDEMPOTENCY_KEY_REUSED)', () => {
    // Docs list this code under both 422 and 409; the code wins -> ConflictError.
    const as422 = envelope(422, { code: 'IDEMPOTENCY_KEY_REUSED', message: 'reuse' });
    const as409 = envelope(409, { code: 'IDEMPOTENCY_KEY_REUSED', message: 'reuse' });
    expect(as422).toBeInstanceOf(ConflictError);
    expect(as409).toBeInstanceOf(ConflictError);
  });

  it('maps WALLET_EXHAUSTED to InsufficientCreditsError', () => {
    expect(envelope(402, { code: 'WALLET_EXHAUSTED', message: 'dry' })).toBeInstanceOf(
      InsufficientCreditsError,
    );
  });
});

describe('errorFromResponse — context extras', () => {
  it('parses required + balance on 402', () => {
    const error = envelope(402, {
      code: 'INSUFFICIENT_CREDITS',
      message: 'Need more credits',
      type: 'billing_error',
      required: 5,
      balance: 2,
    });
    expect(error).toBeInstanceOf(InsufficientCreditsError);
    expect(error.required).toBe(5);
    expect(error.balance).toBe(2);
  });

  it('parses required_scope + current_scopes on 403', () => {
    const error = envelope(403, {
      code: 'INSUFFICIENT_SCOPE',
      message: 'missing scope',
      required_scope: 'runs:write',
      current_scopes: ['predict', 'ground'],
    });
    expect(error.requiredScope).toBe('runs:write');
    expect(error.currentScopes).toEqual(['predict', 'ground']);
  });

  it('parses retry_after on 429', () => {
    const error = envelope(429, { code: 'RATE_LIMITED', message: 'slow down', retry_after: 7 });
    expect(error.retryAfter).toBe(7);
  });

  it('parses details on 422', () => {
    const details = [{ loc: ['body', 'screenshot'], msg: 'too short' }];
    const error = envelope(422, { code: 'VALIDATION_ERROR', message: 'invalid', details });
    expect(error.details).toEqual(details);
  });

  it('parses current_state + allowed_from on 409', () => {
    const error = envelope(409, {
      code: 'NOT_AWAITING_HUMAN',
      message: 'not paused',
      current_state: 'running',
      allowed_from: ['awaiting_human'],
    });
    expect(error.currentState).toBe('running');
    expect(error.allowedFrom).toEqual(['awaiting_human']);
  });

  it('parses suggestion and docs_url', () => {
    const error = envelope(401, {
      code: 'INVALID_API_KEY',
      message: 'bad key',
      suggestion: 'Check the key',
      docs_url: 'https://coasty.ai/docs/errors',
    });
    expect(error.suggestion).toBe('Check the key');
    expect(error.docsUrl).toBe('https://coasty.ai/docs/errors');
  });
});

describe('errorFromResponse — request id', () => {
  it('takes request_id from the body envelope', () => {
    const error = envelope(404, {
      code: 'NOT_FOUND',
      message: 'gone',
      request_id: 'req_from_body',
    });
    expect(error.requestId).toBe('req_from_body');
  });

  it('falls back to the X-Coasty-Request-Id header', () => {
    const error = envelope(
      404,
      { code: 'NOT_FOUND', message: 'gone' },
      { 'x-coasty-request-id': 'req_from_header' },
    );
    expect(error.requestId).toBe('req_from_header');
  });

  it('is null when neither is present', () => {
    expect(envelope(404, { code: 'NOT_FOUND', message: 'gone' }).requestId).toBeNull();
  });
});

describe('errorFromResponse — non-JSON bodies', () => {
  it('synthesizes HTTP_<status> for an HTML error page', () => {
    const error = errorFromResponse(
      502,
      new Headers({ 'x-coasty-request-id': 'req_proxy' }),
      '<html><body>Bad Gateway</body></html>',
    );
    expect(error).toBeInstanceOf(ServerError);
    expect(error.code).toBe('HTTP_502');
    expect(error.statusCode).toBe(502);
    expect(error.requestId).toBe('req_proxy');
    expect(error.message).toContain('Bad Gateway');
  });

  it('handles an empty body', () => {
    const error = errorFromResponse(500, new Headers(), '');
    expect(error.code).toBe('HTTP_500');
    expect(error.message).toBe('HTTP 500');
  });

  it('handles JSON without an error envelope', () => {
    const error = errorFromResponse(503, new Headers(), JSON.stringify({ oops: true }));
    expect(error).toBeInstanceOf(ServerError);
    expect(error.code).toBe('HTTP_503');
  });

  it('truncates very long non-JSON bodies', () => {
    const error = errorFromResponse(500, new Headers(), 'x'.repeat(5000));
    expect(error.message.length).toBeLessThanOrEqual(201);
  });
});
