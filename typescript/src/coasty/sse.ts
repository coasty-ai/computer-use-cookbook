/**
 * Server-Sent Events parsing + reconnection.
 *
 * Framing (UTF-8): events are separated by a blank line; `id:`/`event:`/`data:`
 * lines; multiple `data:` lines join with `\n`; lines starting with `:` are
 * comments (keepalives) and are ignored. The client tracks the last seen `id`
 * and sends it as `Last-Event-ID` on reconnect — Coasty replays everything
 * after that seq with no loss and no duplicates.
 */

export interface SseMessage {
  /** Last seen `id:` value (the durable seq for Coasty streams). */
  id: string | null;
  /** `event:` field, defaulting to "message" per the SSE spec. */
  event: string;
  /** Joined `data:` payload. */
  data: string;
}

export type SleepFn = (ms: number) => Promise<void>;

export const defaultSleep: SleepFn = (ms) =>
  new Promise((resolve) => {
    setTimeout(resolve, ms);
  });

/**
 * Parse a raw SSE byte stream into messages. Incomplete trailing events (no
 * blank-line terminator before EOF) are discarded, per the SSE spec.
 */
export async function* parseSseStream(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<SseMessage, void, undefined> {
  const reader = stream.getReader();
  const decoder = new TextDecoder('utf-8');

  let buffer = '';
  let dataLines: string[] = [];
  let eventType = '';
  let lastId: string | null = null;
  let sawField = false;

  const flush = (): SseMessage | null => {
    if (!sawField || dataLines.length === 0) {
      // Per spec: a blank line with an empty data buffer dispatches nothing.
      dataLines = [];
      eventType = '';
      sawField = false;
      return null;
    }
    const message: SseMessage = {
      id: lastId,
      event: eventType === '' ? 'message' : eventType,
      data: dataLines.join('\n'),
    };
    dataLines = [];
    eventType = '';
    sawField = false;
    return message;
  };

  const handleLine = (line: string): SseMessage | null => {
    if (line === '') return flush();
    if (line.startsWith(':')) return null; // comment / keepalive
    const colon = line.indexOf(':');
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? '' : line.slice(colon + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    switch (field) {
      case 'id':
        // Per spec, ids containing NUL are ignored.
        if (!value.includes('\0')) {
          lastId = value;
          sawField = true;
        }
        break;
      case 'event':
        eventType = value;
        sawField = true;
        break;
      case 'data':
        dataLines.push(value);
        sawField = true;
        break;
      default:
        // Unknown fields (e.g. "retry") are ignored.
        break;
    }
    return null;
  };

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // Normalize CRLF / CR line endings, then emit complete lines.
      for (;;) {
        const match = /\r\n|\n|\r/.exec(buffer);
        if (match === null) break;
        const line = buffer.slice(0, match.index);
        buffer = buffer.slice(match.index + match[0].length);
        const message = handleLine(line);
        if (message !== null) yield message;
      }
    }
    // EOF: a final line without a trailing newline could close a frame only
    // via a blank line, which requires a newline — so leftover buffer content
    // belongs to an unterminated (discarded) event.
  } finally {
    try {
      await reader.cancel();
    } catch {
      // The stream may already be closed/errored; cancellation is best-effort.
    }
  }
}

/** Returns a fresh SSE byte stream, resuming after `lastEventId` when given. */
export type SseConnector = (lastEventId: string | null) => Promise<ReadableStream<Uint8Array>>;

export interface ReconnectingSseOptions {
  /** Max reconnect attempts after a dropped stream (default 5). */
  maxReconnects?: number;
  /** Delay between reconnect attempts in ms (default 500). */
  reconnectDelayMs?: number;
  sleep?: SleepFn;
  /** Stream terminator (default: the documented `done` event). */
  isTerminal?: (message: SseMessage) => boolean;
  /** Resume cursor carried into the first connection. */
  lastEventId?: string | null;
}

/** Raised when the stream keeps dropping without ever reaching `done`. */
export class SseStreamEndedError extends Error {
  constructor(reconnects: number) {
    super(
      `SSE stream ended without a 'done' event after ${String(reconnects)} reconnect attempt(s)`,
    );
    this.name = 'SseStreamEndedError';
  }
}

/**
 * Async iterator over SSE messages that transparently reconnects when the
 * stream drops mid-flight, resuming via `Last-Event-ID`. Numeric ids are used
 * as a monotonic cursor: replayed events with `seq <= last seen` are filtered
 * out, so consumers observe no loss and no duplicates. Terminates after the
 * `done` event.
 */
export async function* reconnectingSse(
  connect: SseConnector,
  options: ReconnectingSseOptions = {},
): AsyncGenerator<SseMessage, void, undefined> {
  const maxReconnects = options.maxReconnects ?? 5;
  const reconnectDelayMs = options.reconnectDelayMs ?? 500;
  const sleep = options.sleep ?? defaultSleep;
  const isTerminal = options.isTerminal ?? ((message: SseMessage) => message.event === 'done');

  let lastEventId: string | null = options.lastEventId ?? null;
  let lastSeq: number | null =
    lastEventId !== null && /^\d+$/.test(lastEventId) ? Number(lastEventId) : null;
  let reconnects = 0;

  for (;;) {
    // Connection errors (e.g. 401/404 envelopes) propagate to the caller.
    const stream = await connect(lastEventId);
    let sawTerminal = false;
    try {
      for await (const message of parseSseStream(stream)) {
        if (message.id !== null) {
          if (/^\d+$/.test(message.id)) {
            const seq = Number(message.id);
            if (lastSeq !== null && seq <= lastSeq) continue; // replayed duplicate
            lastSeq = seq;
          }
          lastEventId = message.id;
        }
        yield message;
        if (isTerminal(message)) {
          sawTerminal = true;
          break;
        }
      }
    } catch {
      // Mid-stream transport failure: treat as a drop and reconnect below.
    }
    if (sawTerminal) return;
    reconnects += 1;
    if (reconnects > maxReconnects) throw new SseStreamEndedError(maxReconnects);
    await sleep(reconnectDelayMs);
  }
}
