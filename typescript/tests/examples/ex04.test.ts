/**
 * Example 04 tests — /parse + dry-run through NullBackend (free, offline).
 */
import { describe, expect, it } from 'vitest';
import { type Action } from '../../src/coasty/types.js';
import {
  SAMPLE_PYAUTOGUI_CODE,
  formatActionTable,
  parseAndDryRun,
  parseArgs,
  type PrintFn,
} from '../../src/examples/ex04-parse.js';
import { errorResponse, jsonResponse, makeClient } from '../helpers.js';

const silent: PrintFn = () => undefined;

const PARSED_ACTIONS: Action[] = [
  { action_type: 'click', params: { x: 640, y: 360 } },
  { action_type: 'type_text', params: { text: 'hello@example.com' } },
  { action_type: 'key_press', params: { key: 'tab' } },
  { action_type: 'key_combo', params: { keys: ['ctrl', 's'] } },
  { action_type: 'scroll', params: { clicks: -3 } }, // pyautogui shape: signed clicks
  { action_type: 'raw', params: { code: 'pyautogui.moveTo(1, 2)' } },
];

describe('parseAndDryRun', () => {
  it('parses pyautogui code and dry-runs the actions without a real screen', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(jsonResponse({ actions: PARSED_ACTIONS }));
    const lines: string[] = [];

    const result = await parseAndDryRun({
      client,
      code: SAMPLE_PYAUTOGUI_CODE,
      print: (line) => {
        lines.push(line);
      },
    });

    expect(fetchMock.calls).toHaveLength(1);
    expect(fetchMock.calls[0]?.method).toBe('POST');
    expect(fetchMock.calls[0]?.path).toBe('/v1/parse');
    expect((fetchMock.calls[0]?.body as Record<string, unknown>).code).toBe(SAMPLE_PYAUTOGUI_CODE);

    // Every executable action was recorded against the NullBackend...
    expect(result.calls.map((call) => call.method)).toEqual([
      'click',
      'typeText',
      'keyPress',
      'keyCombo',
      'scroll',
    ]);
    // ...with both param shapes normalized (signed clicks -> direction+amount).
    expect(result.calls[4]?.args[0]).toMatchObject({ direction: 'down', amount: 3 });

    // The raw action is NEVER executed: logged + skipped.
    expect(result.results).toHaveLength(6);
    const rawOutcome = result.results[5];
    expect(rawOutcome?.actionType).toBe('raw');
    expect(rawOutcome?.executed).toBe(false);
    expect(rawOutcome?.detail).toContain('skipped raw action');

    const output = lines.join('\n');
    expect(output).toContain('FREE (0 credits)');
    expect(output).toContain('request_id=req_test_123');
    expect(output).toContain('click');
    expect(result.requestId).toBe('req_test_123');
  });

  it('propagates API errors with the request_id', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(
      errorResponse(422, 'VALIDATION_ERROR', 'code must be non-empty', {
        type: 'validation_error',
      }),
    );

    await expect(parseAndDryRun({ client, code: ' ', print: silent })).rejects.toMatchObject({
      code: 'VALIDATION_ERROR',
      requestId: 'req_err_123',
    });
  });
});

describe('formatActionTable', () => {
  it('renders numbered, aligned rows with params and descriptions', () => {
    const table = formatActionTable([
      { action_type: 'click', params: { x: 1, y: 2 }, description: 'Click OK' },
      { action_type: 'wait', params: { ms: 500 } },
    ]);
    expect(table).toContain('1. click');
    expect(table).toContain('{"x":1,"y":2}');
    expect(table).toContain('— Click OK');
    expect(table).toContain('2. wait');
  });

  it('handles an empty action list', () => {
    expect(formatActionTable([])).toBe('(no actions parsed)');
  });
});

describe('parseArgs', () => {
  it('parses --code and --file', () => {
    expect(parseArgs(['--code', 'pyautogui.click(1, 2)'])).toEqual({
      code: 'pyautogui.click(1, 2)',
      file: null,
    });
    expect(parseArgs(['--file', 'macro.py'])).toEqual({ code: null, file: 'macro.py' });
  });

  it('rejects unknown arguments', () => {
    expect(() => parseArgs(['--nope'])).toThrow(/unknown argument/);
  });
});
