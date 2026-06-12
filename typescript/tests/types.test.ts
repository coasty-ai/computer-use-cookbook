/**
 * types.ts — runtime helpers around the literal unions.
 */
import { describe, expect, it } from 'vitest';

import {
  BROWSER_OPS,
  FILE_READ_OPS,
  FILE_WRITE_OPS,
  TERMINAL_RUN_STATUSES,
  isTerminalRunStatus,
} from '../src/coasty/types.js';

describe('run status helpers', () => {
  it('exposes the four documented terminal states', () => {
    expect(TERMINAL_RUN_STATUSES).toEqual(['succeeded', 'failed', 'cancelled', 'timed_out']);
  });

  it.each([
    ['succeeded', true],
    ['failed', true],
    ['cancelled', true],
    ['timed_out', true],
    ['queued', false],
    ['running', false],
    ['awaiting_human', false],
  ] as const)('isTerminalRunStatus(%s) -> %s', (status, expected) => {
    expect(isTerminalRunStatus(status)).toBe(expected);
  });
});

describe('op tuples', () => {
  it('lists the 16 documented browser ops', () => {
    expect(BROWSER_OPS).toHaveLength(16);
    expect(BROWSER_OPS).toContain('navigate');
    expect(BROWSER_OPS).toContain('switch-tab');
  });

  it('splits file ops into read (6) and write (5) scopes', () => {
    expect(FILE_READ_OPS).toEqual([
      'read',
      'exists',
      'list',
      'list-directory',
      'download',
      'list-downloads',
    ]);
    expect(FILE_WRITE_OPS).toEqual(['write', 'edit', 'append', 'delete', 'delete-directory']);
  });
});
