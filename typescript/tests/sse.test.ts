/**
 * sse.ts — frame parsing (id/event/data, multi-line data, comments) and the
 * reconnecting iterator (Last-Event-ID resume, no loss, no duplicates,
 * terminates after `done`), plus the client-level run-events stream.
 */
import { describe, expect, it } from 'vitest';

import { AuthenticationError } from '../src/coasty/errors.js';
import {
  SseStreamEndedError,
  parseSseStream,
  reconnectingSse,
  type SseMessage,
} from '../src/coasty/sse.js';
import { type RunEvent } from '../src/coasty/types.js';
import {
  errorResponse,
  makeClient,
  sseFrame,
  sseResponse,
  streamFromChunks,
  streamThatDrops,
} from './helpers.js';

async function collect(stream: AsyncGenerator<SseMessage>): Promise<SseMessage[]> {
  const messages: SseMessage[] = [];
  for await (const message of stream) messages.push(message);
  return messages;
}

describe('parseSseStream — framing', () => {
  it('parses id/event/data frames', async () => {
    const messages = await collect(
      parseSseStream(streamFromChunks(['id: 42\nevent: status\ndata: {"status":"running"}\n\n'])),
    );
    expect(messages).toEqual([{ id: '42', event: 'status', data: '{"status":"running"}' }]);
  });

  it('joins multi-line data with \\n', async () => {
    const messages = await collect(
      parseSseStream(streamFromChunks(['data: line one\ndata: line two\n\n'])),
    );
    expect(messages).toEqual([{ id: null, event: 'message', data: 'line one\nline two' }]);
  });

  it('defaults the event type to "message"', async () => {
    const messages = await collect(parseSseStream(streamFromChunks(['data: hi\n\n'])));
    expect(messages[0]?.event).toBe('message');
  });

  it('ignores comment / keepalive lines', async () => {
    const messages = await collect(
      parseSseStream(streamFromChunks([': keepalive\n\n: another comment\ndata: payload\n\n'])),
    );
    expect(messages).toEqual([{ id: null, event: 'message', data: 'payload' }]);
  });

  it('strips exactly one leading space after the colon', async () => {
    const messages = await collect(
      parseSseStream(streamFromChunks(['data:no-space\ndata:  two-spaces\n\n'])),
    );
    expect(messages[0]?.data).toBe('no-space\n two-spaces');
  });

  it('handles frames split across arbitrary chunk boundaries', async () => {
    const raw = 'id: 7\nevent: step\ndata: {"n":1}\n\nid: 8\nevent: done\ndata: {}\n\n';
    const chunks = [raw.slice(0, 5), raw.slice(5, 23), raw.slice(23, 24), raw.slice(24)];
    const messages = await collect(parseSseStream(streamFromChunks(chunks)));
    expect(messages).toEqual([
      { id: '7', event: 'step', data: '{"n":1}' },
      { id: '8', event: 'done', data: '{}' },
    ]);
  });

  it('handles CRLF and CR line endings', async () => {
    const messages = await collect(
      parseSseStream(streamFromChunks(['id: 1\r\ndata: crlf\r\n\r\n', 'id: 2\rdata: cr\r\r'])),
    );
    expect(messages).toEqual([
      { id: '1', event: 'message', data: 'crlf' },
      { id: '2', event: 'message', data: 'cr' },
    ]);
  });

  it('persists the last seen id across frames (SSE spec)', async () => {
    const messages = await collect(
      parseSseStream(streamFromChunks(['id: 5\ndata: first\n\n', 'data: second (no id line)\n\n'])),
    );
    expect(messages.map((m) => m.id)).toEqual(['5', '5']);
  });

  it('discards an unterminated trailing frame', async () => {
    const messages = await collect(
      parseSseStream(streamFromChunks(['data: complete\n\ndata: incomplete'])),
    );
    expect(messages).toEqual([{ id: null, event: 'message', data: 'complete' }]);
  });

  it('dispatches nothing for a blank line with no data buffered', async () => {
    const messages = await collect(parseSseStream(streamFromChunks(['\n\n\n', 'data: x\n\n'])));
    expect(messages).toEqual([{ id: null, event: 'message', data: 'x' }]);
  });
});

describe('reconnectingSse', () => {
  it('reconnects after a mid-stream drop, resuming via Last-Event-ID with no loss/dup', async () => {
    const connectIds: (string | null)[] = [];
    const streams = [
      // First connection delivers 1, 2 then drops (transport error).
      streamThatDrops(
        [sseFrame({ id: 1, event: 'status', data: '{"status":"running"}' })].concat(
          sseFrame({ id: 2, event: 'step', data: '{"n":1}' }),
        ),
      ),
      // Replay includes 1-2 again (must be filtered) plus 3 and done.
      streamFromChunks([
        sseFrame({ id: 1, event: 'status', data: '{"status":"running"}' }),
        sseFrame({ id: 2, event: 'step', data: '{"n":1}' }),
        sseFrame({ id: 3, event: 'step', data: '{"n":2}' }),
        sseFrame({ id: 4, event: 'done', data: '{}' }),
      ]),
    ];
    let connectCount = 0;
    const sleeps: number[] = [];

    const messages = await collect(
      reconnectingSse(
        (lastEventId) => {
          connectIds.push(lastEventId);
          const stream = streams[connectCount];
          connectCount += 1;
          if (stream === undefined) throw new Error('too many connects');
          return Promise.resolve(stream);
        },
        {
          sleep: (ms) => {
            sleeps.push(ms);
            return Promise.resolve();
          },
          reconnectDelayMs: 10,
        },
      ),
    );

    expect(connectIds).toEqual([null, '2']);
    expect(messages.map((m) => m.id)).toEqual(['1', '2', '3', '4']); // no loss, no dup
    expect(messages.at(-1)?.event).toBe('done');
    expect(sleeps).toEqual([10]);
  });

  it('also treats a clean end without `done` as a drop', async () => {
    const streams = [
      streamFromChunks([sseFrame({ id: 1, event: 'status', data: '{}' })]), // ends, no done
      streamFromChunks([sseFrame({ id: 2, event: 'done', data: '{}' })]),
    ];
    let connectCount = 0;

    const messages = await collect(
      reconnectingSse(
        () => {
          const stream = streams[connectCount];
          connectCount += 1;
          if (stream === undefined) throw new Error('too many connects');
          return Promise.resolve(stream);
        },
        { sleep: () => Promise.resolve() },
      ),
    );

    expect(messages.map((m) => m.id)).toEqual(['1', '2']);
  });

  it('passes the initial lastEventId option into the first connection', async () => {
    const connectIds: (string | null)[] = [];
    const messages = await collect(
      reconnectingSse(
        (lastEventId) => {
          connectIds.push(lastEventId);
          return Promise.resolve(
            streamFromChunks([
              // Server replays 7 (<= cursor) — it must be filtered out.
              sseFrame({ id: 7, event: 'step', data: '{}' }),
              sseFrame({ id: 8, event: 'done', data: '{}' }),
            ]),
          );
        },
        { lastEventId: '7', sleep: () => Promise.resolve() },
      ),
    );

    expect(connectIds).toEqual(['7']);
    expect(messages.map((m) => m.id)).toEqual(['8']);
  });

  it('throws SseStreamEndedError when the stream never reaches done', async () => {
    let connects = 0;
    const iterate = async (): Promise<void> => {
      await collect(
        reconnectingSse(
          () => {
            connects += 1;
            return Promise.resolve(streamFromChunks([]));
          },
          { maxReconnects: 2, sleep: () => Promise.resolve() },
        ),
      );
    };

    await expect(iterate()).rejects.toBeInstanceOf(SseStreamEndedError);
    expect(connects).toBe(3); // initial + 2 reconnects
  });

  it('propagates connection errors to the caller', async () => {
    const failing = reconnectingSse(() => Promise.reject(new Error('401 from server')), {
      sleep: () => Promise.resolve(),
    });
    await expect(failing.next()).rejects.toThrow('401 from server');
  });
});

describe('client.runs.events — end-to-end stream', () => {
  it('streams, reconnects with Last-Event-ID, decodes JSON, and stops after done', async () => {
    const { client, fetchMock, sleeps } = makeClient();
    fetchMock.enqueue(
      sseResponse(
        [
          sseFrame({ id: 1, event: 'status', data: '{"status":"running"}' }),
          sseFrame({ id: 2, event: 'reasoning', data: '{"text":"thinking"}' }),
        ],
        { drop: true }, // connection drops mid-stream
      ),
      sseResponse([
        sseFrame({ id: 2, event: 'reasoning', data: '{"text":"thinking"}' }), // replay
        sseFrame({ id: 3, event: 'status', data: '{"status":"succeeded"}' }),
        sseFrame({ id: 4, event: 'done', data: '{"status":"succeeded"}' }),
      ]),
    );

    const events: RunEvent[] = [];
    for await (const event of client.runs.events('run_42')) events.push(event);

    // Request contract
    const first = fetchMock.calls[0];
    const second = fetchMock.calls[1];
    expect(first?.path).toBe('/v1/runs/run_42/events');
    expect(first?.headers.get('accept')).toBe('text/event-stream');
    expect(first?.headers.get('x-api-key')).not.toBeNull();
    expect(first?.headers.get('last-event-id')).toBeNull();
    expect(second?.headers.get('last-event-id')).toBe('2');

    // No loss, no duplicates, JSON decoded, terminates after done.
    expect(events.map((e) => e.seq)).toEqual([1, 2, 3, 4]);
    expect(events.map((e) => e.type)).toEqual(['status', 'reasoning', 'status', 'done']);
    expect(events[0]?.data).toEqual({ status: 'running' });
    expect(events.at(-1)?.data).toEqual({ status: 'succeeded' });
    expect(sleeps).toEqual([500]); // one reconnect delay (client retryBaseMs)
  });

  it('carries an explicit resume cursor into the first request', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(sseResponse([sseFrame({ id: 11, event: 'done', data: '{}' })]));

    const events: RunEvent[] = [];
    for await (const event of client.runs.events('run_42', { lastEventId: 10 })) {
      events.push(event);
    }

    expect(fetchMock.calls[0]?.headers.get('last-event-id')).toBe('10');
    expect(events.map((e) => e.seq)).toEqual([11]);
  });

  it('surfaces typed errors when the stream endpoint rejects the connection', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(errorResponse(401, 'INVALID_API_KEY', 'bad key', { type: 'auth_error' }));

    const iterator = client.runs.events('run_42');
    await expect(iterator.next()).rejects.toBeInstanceOf(AuthenticationError);
  });

  it('workflow run events use the workflows path', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(sseResponse([sseFrame({ id: 1, event: 'done', data: '{}' })]));

    const events: RunEvent[] = [];
    for await (const event of client.workflows.runEvents('wfr_7')) events.push(event);

    expect(fetchMock.calls[0]?.path).toBe('/v1/workflows/runs/wfr_7/events');
    expect(events).toHaveLength(1);
  });

  it('keeps non-JSON data frames as raw strings', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(
      sseResponse([
        sseFrame({ id: 1, event: 'text', data: 'plain text, not JSON' }),
        sseFrame({ id: 2, event: 'done', data: '{}' }),
      ]),
    );

    const events: RunEvent[] = [];
    for await (const event of client.runs.events('run_42')) events.push(event);

    expect(events[0]?.data).toBe('plain text, not JSON');
  });
});
