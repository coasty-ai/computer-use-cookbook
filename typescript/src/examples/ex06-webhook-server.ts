/**
 * Example 06 — Webhook receiver (node:http stdlib only).
 *
 * Coasty POSTs a signed JSON payload to your `webhook_url` on run lifecycle
 * transitions: `run.succeeded`, `run.failed`, `run.cancelled`,
 * `run.timed_out`, and `run.awaiting_human`. Every callback carries
 * `Coasty-Signature: t=<unix>,v1=<hex>` where the signed payload is
 * `"<t>." + raw_body` and `v1 = hex(HMAC_SHA256(webhook_secret, payload))`.
 *
 * This receiver:
 *   - verifies the signature with a CONSTANT-TIME compare and a ±5 minute
 *     timestamp tolerance (via the shared `webhooks.ts` verifier);
 *   - answers 401 for invalid, stale, or tampered deliveries;
 *   - answers 200 FAST (dispatch happens after the response is written, so a
 *     slow handler can never trigger sender-side retries);
 *   - on `run.awaiting_human` shows exactly where `client.runs.resume(...)`
 *     would be called to hand control back to the agent.
 *
 * Estimated cost: 0 credits ($0.00) — this is a receiver; it makes no API
 * calls. (Resuming a paused run is also free; only completed run STEPS bill,
 * at 5 credits / $0.05 each on v3/v4.)
 *
 * Run it:
 *   COASTY_WEBHOOK_SECRET=whsec_... npx tsx src/examples/ex06-webhook-server.ts [--port 8788]
 *
 * The secret comes from the `webhook_secret` field returned ONCE by
 * `POST /v1/runs` when you pass a `webhook_url` — store it then, export it as
 * `COASTY_WEBHOOK_SECRET` here. Never commit it.
 */
import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { loadEnvFile } from '../coasty/env.js';
import { verifySignature } from '../coasty/webhooks.js';

// ---------------------------------------------------------------------------
// Pure core — fully testable without sockets
// ---------------------------------------------------------------------------

/** The five documented run webhook events. */
export const RUN_WEBHOOK_EVENTS = [
  'run.succeeded',
  'run.failed',
  'run.cancelled',
  'run.timed_out',
  'run.awaiting_human',
] as const;

export type RunWebhookEventName = (typeof RUN_WEBHOOK_EVENTS)[number];

/** A verified, well-formed run webhook payload. */
export interface RunWebhookEvent {
  event: RunWebhookEventName;
  run_id: string;
  /** Terminal events carry the run status (e.g. "succeeded"). */
  status?: string;
  /** `run.awaiting_human` carries the pause reason (e.g. "captcha"). */
  reason?: string;
  /** Any extra documented fields ride along untouched. */
  extra: Record<string, unknown>;
}

export type WebhookDisposition =
  | 'ok'
  | 'ignored_unknown_event'
  | 'invalid_signature'
  | 'invalid_payload';

export interface WebhookOutcome {
  /** HTTP status the receiver should answer with (200 | 400 | 401). */
  status: 200 | 400 | 401;
  disposition: WebhookDisposition;
  /** Present only when the delivery verified AND parsed as a known event. */
  event?: RunWebhookEvent;
}

function parseEvent(rawBody: string): RunWebhookEvent | 'invalid_payload' | 'unknown_event' {
  let parsed: unknown;
  try {
    parsed = JSON.parse(rawBody);
  } catch {
    return 'invalid_payload';
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    return 'invalid_payload';
  }
  const record = parsed as Record<string, unknown>;
  const { event, run_id: runId, status, reason, ...extra } = record;
  if (typeof event !== 'string' || typeof runId !== 'string' || runId === '') {
    return 'invalid_payload';
  }
  if (!(RUN_WEBHOOK_EVENTS as readonly string[]).includes(event)) {
    return 'unknown_event';
  }
  const result: RunWebhookEvent = { event: event as RunWebhookEventName, run_id: runId, extra };
  if (typeof status === 'string') result.status = status;
  if (typeof reason === 'string') result.reason = reason;
  return result;
}

/**
 * Pure webhook handler: verify the `Coasty-Signature` header over the EXACT
 * raw body bytes, then parse + classify the event.
 *
 *   - bad/missing/stale/tampered signature -> 401 (never reveal which check failed)
 *   - verified but not a JSON object with `event` + `run_id` -> 400
 *   - verified, well-formed, unknown event name -> 200 (ack so the sender
 *     does not retry forever) without dispatching
 *   - verified known event -> 200 + the event
 *
 * @param rawBody The exact request bytes (NOT re-serialized JSON).
 * @param header  The `Coasty-Signature` header value, or undefined if absent.
 * @param secret  The per-run `webhook_secret` (shown once on run create).
 * @param now     Unix seconds "now" (injectable for tests; defaults to wall clock).
 */
export function handleWebhook(
  rawBody: Uint8Array | string,
  header: string | undefined,
  secret: string,
  now: number = Math.floor(Date.now() / 1000),
): WebhookOutcome {
  if (header === undefined || !verifySignature(rawBody, header, secret, { now })) {
    return { status: 401, disposition: 'invalid_signature' };
  }
  const text = typeof rawBody === 'string' ? rawBody : Buffer.from(rawBody).toString('utf8');
  const parsed = parseEvent(text);
  if (parsed === 'invalid_payload') return { status: 400, disposition: 'invalid_payload' };
  if (parsed === 'unknown_event') return { status: 200, disposition: 'ignored_unknown_event' };
  return { status: 200, disposition: 'ok', event: parsed };
}

/**
 * What this receiver does for each verified event. Returns the log line so
 * tests can assert the dispatch table — including that `run.awaiting_human`
 * points at `client.runs.resume(...)`.
 */
export function dispatchWebhookEvent(event: RunWebhookEvent): string {
  switch (event.event) {
    case 'run.succeeded':
      return `[${event.run_id}] succeeded — fetch the result via client.runs.get('${event.run_id}')`;
    case 'run.failed':
      return `[${event.run_id}] failed — inspect run.error via client.runs.get('${event.run_id}')`;
    case 'run.cancelled':
      return `[${event.run_id}] cancelled — terminal state, nothing to do`;
    case 'run.timed_out':
      return `[${event.run_id}] timed out — consider a higher deadline_seconds and retry`;
    case 'run.awaiting_human':
      // This is where a real operator UI would page someone. Once the human
      // has resolved the blocker (e.g. a captcha), hand control back with:
      //
      //   await client.runs.resume(event.run_id, { note: 'captcha solved' });
      //
      // (resume is only valid from awaiting_human; otherwise the API answers
      // 409 NOT_AWAITING_HUMAN).
      return (
        `[${event.run_id}] PAUSED (${event.reason ?? 'no reason given'}) — resolve it, then call ` +
        `client.runs.resume('${event.run_id}', { note: '...' }) to continue`
      );
  }
}

// ---------------------------------------------------------------------------
// node:http server (stdlib only)
// ---------------------------------------------------------------------------

/** Deliveries larger than this are rejected outright (Coasty payloads are tiny). */
export const MAX_BODY_BYTES = 1_048_576;

export interface WebhookServerOptions {
  secret: string;
  /** Unix-seconds clock, injectable for tests. */
  now?: () => number;
  /** Called AFTER the 200 has been written, once per verified known event. */
  onEvent?: (event: RunWebhookEvent) => void;
  logger?: (line: string) => void;
}

function readRawBody(request: IncomingMessage, maxBytes: number): Promise<Buffer | null> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    let total = 0;
    request.on('data', (chunk: Buffer) => {
      total += chunk.length;
      if (total > maxBytes) {
        resolve(null); // too large
        request.destroy();
        return;
      }
      chunks.push(chunk);
    });
    request.on('end', () => {
      resolve(Buffer.concat(chunks));
    });
    request.on('error', reject);
  });
}

/**
 * Build (but do not bind) the webhook receiver. The caller picks the port —
 * tests listen on 127.0.0.1:0 (an ephemeral loopback port).
 */
export function createWebhookServer(options: WebhookServerOptions): Server {
  const log = options.logger ?? ((line: string): void => void process.stdout.write(`${line}\n`));
  const now = options.now ?? ((): number => Math.floor(Date.now() / 1000));

  const respond = (response: ServerResponse, status: number, body: string): void => {
    response.writeHead(status, { 'content-type': 'application/json' });
    response.end(JSON.stringify({ status: body }));
  };

  return createServer((request, response) => {
    void (async (): Promise<void> => {
      if (request.method !== 'POST') {
        respond(response, 405, 'method_not_allowed');
        return;
      }
      const rawBody = await readRawBody(request, MAX_BODY_BYTES);
      if (rawBody === null) {
        respond(response, 413, 'payload_too_large');
        return;
      }
      const header = request.headers['coasty-signature'];
      const headerValue = Array.isArray(header) ? header[0] : header;
      const outcome = handleWebhook(rawBody, headerValue, options.secret, now());

      // Answer FIRST (200 fast — the sender's retry clock stops here), then
      // dispatch on the next tick so slow handlers never delay the ack.
      respond(response, outcome.status, outcome.disposition);

      const { event } = outcome;
      if (event !== undefined) {
        setImmediate(() => {
          log(dispatchWebhookEvent(event));
          options.onEvent?.(event);
        });
      } else if (outcome.status !== 200) {
        log(`rejected delivery: ${outcome.disposition} (answered ${String(outcome.status)})`);
      }
    })().catch((error: unknown) => {
      // Never leak internals to the sender; log and fail closed.
      console.error(`webhook receiver error: ${String(error)}`);
      if (!response.headersSent) respond(response, 500, 'internal_error');
    });
  });
}

// ---------------------------------------------------------------------------
// Thin CLI
// ---------------------------------------------------------------------------

function isMain(): boolean {
  const entry = process.argv[1];
  return entry !== undefined && path.resolve(entry) === fileURLToPath(import.meta.url);
}

export function main(argv: string[] = process.argv.slice(2)): void {
  loadEnvFile();
  const secret = process.env.COASTY_WEBHOOK_SECRET?.trim();
  if (secret === undefined || secret === '') {
    console.error(
      'COASTY_WEBHOOK_SECRET is not set. It is the webhook_secret returned ONCE by ' +
        'POST /v1/runs when you pass a webhook_url — export it before starting the receiver.',
    );
    process.exitCode = 1;
    return;
  }

  const portFlag = argv.indexOf('--port');
  const portRaw = portFlag !== -1 ? argv[portFlag + 1] : process.env.COASTY_WEBHOOK_PORT;
  const port = portRaw === undefined ? 8788 : Number(portRaw);
  if (!Number.isInteger(port) || port < 0 || port > 65535) {
    console.error(`invalid port: ${String(portRaw)}`);
    process.exitCode = 1;
    return;
  }

  const print = (line: string): void => void process.stdout.write(`${line}\n`);
  const server = createWebhookServer({ secret, logger: print });
  server.listen(port, '127.0.0.1', () => {
    print(`Coasty webhook receiver listening on http://127.0.0.1:${String(port)}`);
    print('Estimated cost: 0 credits ($0.00) — receiver only, no API calls.');
    print('Point your run webhook_url here (via a tunnel — Coasty requires https).');
  });
}

if (isMain()) main();
