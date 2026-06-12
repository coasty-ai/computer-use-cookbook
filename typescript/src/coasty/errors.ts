/**
 * Typed errors mirroring the documented Coasty error envelope:
 *
 * ```json
 * { "error": { "code", "message", "type", "request_id", "suggestion?",
 *              "docs_url?", ...context } }
 * ```
 *
 * Branch on `code` (stable), never on `message`. Every error carries the
 * `request_id` when the server provided one (body or `X-Coasty-Request-Id`).
 */

export interface CoastyErrorOptions {
  code: string;
  message: string;
  errorType?: string;
  requestId?: string | null;
  statusCode?: number | null;
  suggestion?: string;
  docsUrl?: string;
  /** 402 INSUFFICIENT_CREDITS: credits required for the call. */
  required?: number;
  /** 402 INSUFFICIENT_CREDITS: current wallet balance (cents). */
  balance?: number;
  /** 403 INSUFFICIENT_SCOPE */
  requiredScope?: string;
  /** 403 INSUFFICIENT_SCOPE */
  currentScopes?: string[];
  /** 429 / 503: seconds to wait before retrying. */
  retryAfter?: number;
  /** 422 VALIDATION_ERROR: field locations. */
  details?: unknown;
  /** 409 INVALID_STATE */
  currentState?: string;
  /** 409 INVALID_STATE */
  allowedFrom?: string[];
  cause?: unknown;
}

/** Base class for every Coasty API error. */
export class CoastyError extends Error {
  readonly code: string;
  readonly errorType: string;
  readonly requestId: string | null;
  readonly statusCode: number | null;
  readonly suggestion: string | undefined;
  readonly docsUrl: string | undefined;
  readonly required: number | undefined;
  readonly balance: number | undefined;
  readonly requiredScope: string | undefined;
  readonly currentScopes: string[] | undefined;
  readonly retryAfter: number | undefined;
  readonly details: unknown;
  readonly currentState: string | undefined;
  readonly allowedFrom: string[] | undefined;

  constructor(options: CoastyErrorOptions) {
    super(options.message, options.cause === undefined ? undefined : { cause: options.cause });
    this.name = new.target.name;
    this.code = options.code;
    this.errorType = options.errorType ?? 'unknown_error';
    this.requestId = options.requestId ?? null;
    this.statusCode = options.statusCode ?? null;
    this.suggestion = options.suggestion;
    this.docsUrl = options.docsUrl;
    this.required = options.required;
    this.balance = options.balance;
    this.requiredScope = options.requiredScope;
    this.currentScopes = options.currentScopes;
    this.retryAfter = options.retryAfter;
    this.details = options.details;
    this.currentState = options.currentState;
    this.allowedFrom = options.allowedFrom;
  }
}

/** 401 INVALID_API_KEY / INVALID_SIGNATURE. */
export class AuthenticationError extends CoastyError {}
/** 403 INSUFFICIENT_SCOPE (carries `requiredScope` + `currentScopes`). */
export class InsufficientScopeError extends CoastyError {}
/** 402 INSUFFICIENT_CREDITS / WALLET_EXHAUSTED (carries `required` + `balance`). */
export class InsufficientCreditsError extends CoastyError {}
/** 400 / 413 / 422 validation problems (carries `details` when present). */
export class ValidationError extends CoastyError {}
/** 404 *_NOT_FOUND. */
export class NotFoundError extends CoastyError {}
/** 409 state conflicts (carries `currentState` + `allowedFrom`). */
export class ConflictError extends CoastyError {}
/** 429 RATE_LIMITED (carries `retryAfter`). */
export class RateLimitError extends CoastyError {}
/** 5xx server-side failures. */
export class ServerError extends CoastyError {}

type CoastyErrorClass = new (options: CoastyErrorOptions) => CoastyError;

/**
 * Codes whose class is canonical regardless of HTTP status. The docs list
 * IDEMPOTENCY_KEY_REUSED under both 422 and 409 — per docs the CODE wins,
 * so it always maps to ConflictError.
 */
const CLASS_BY_CODE: Readonly<Record<string, CoastyErrorClass>> = {
  INVALID_API_KEY: AuthenticationError,
  INVALID_SIGNATURE: AuthenticationError,
  INSUFFICIENT_SCOPE: InsufficientScopeError,
  INSUFFICIENT_CREDITS: InsufficientCreditsError,
  WALLET_EXHAUSTED: InsufficientCreditsError,
  RATE_LIMITED: RateLimitError,
  IDEMPOTENCY_KEY_REUSED: ConflictError,
  NOT_AWAITING_HUMAN: ConflictError,
  RESUME_CONFLICT: ConflictError,
  INVALID_STATE: ConflictError,
};

function classByStatus(status: number): CoastyErrorClass {
  if (status === 401) return AuthenticationError;
  if (status === 402) return InsufficientCreditsError;
  if (status === 403) return InsufficientScopeError;
  if (status === 404) return NotFoundError;
  if (status === 409) return ConflictError;
  if (status === 429) return RateLimitError;
  if (status === 400 || status === 413 || status === 422) return ValidationError;
  if (status >= 500) return ServerError;
  return CoastyError;
}

function typeByStatus(status: number): string {
  if (status === 401) return 'auth_error';
  if (status === 402) return 'billing_error';
  if (status === 403) return 'auth_error';
  if (status === 404) return 'not_found_error';
  if (status === 409) return 'state_error';
  if (status === 429) return 'rate_limit_error';
  if (status === 400 || status === 413 || status === 422) return 'validation_error';
  if (status >= 500) return 'server_error';
  return 'unknown_error';
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function asString(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined;
}

function asNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

function asStringArray(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  return value.every((item): item is string => typeof item === 'string') ? value : undefined;
}

function truncate(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, max)}…`;
}

/**
 * Build the typed error for a non-2xx response. Tolerates non-JSON bodies
 * (proxies, load balancers) by synthesizing a `HTTP_<status>` code and taking
 * the request id from the `X-Coasty-Request-Id` header.
 */
export function errorFromResponse(status: number, headers: Headers, bodyText: string): CoastyError {
  const headerRequestId = headers.get('x-coasty-request-id');

  let envelope: Record<string, unknown> | null = null;
  try {
    const parsed: unknown = JSON.parse(bodyText);
    if (isRecord(parsed) && isRecord(parsed.error)) envelope = parsed.error;
  } catch {
    envelope = null;
  }

  if (envelope === null) {
    const Cls = classByStatus(status);
    const trimmed = bodyText.trim();
    return new Cls({
      code: `HTTP_${status}`,
      message: trimmed === '' ? `HTTP ${status}` : truncate(trimmed, 200),
      errorType: typeByStatus(status),
      requestId: headerRequestId,
      statusCode: status,
    });
  }

  const code = asString(envelope.code) ?? `HTTP_${status}`;
  const Cls = CLASS_BY_CODE[code] ?? classByStatus(status);
  return new Cls({
    code,
    message: asString(envelope.message) ?? `HTTP ${status}`,
    errorType: asString(envelope.type) ?? typeByStatus(status),
    requestId: asString(envelope.request_id) ?? headerRequestId,
    statusCode: status,
    suggestion: asString(envelope.suggestion),
    docsUrl: asString(envelope.docs_url),
    required: asNumber(envelope.required),
    balance: asNumber(envelope.balance),
    requiredScope: asString(envelope.required_scope),
    currentScopes: asStringArray(envelope.current_scopes),
    retryAfter: asNumber(envelope.retry_after),
    details: envelope.details,
    currentState: asString(envelope.current_state),
    allowedFrom: asStringArray(envelope.allowed_from),
  });
}
