/**
 * env.ts — configuration helpers. All cases pass an explicit env record so the
 * tests never touch process.env or the repo-root .env file.
 */
import { describe, expect, it } from 'vitest';

import {
  DEFAULT_BASE_URL,
  MissingApiKeyError,
  SANDBOX_KEY_PREFIX,
  getApiKey,
  getBaseUrl,
  isSandboxKey,
  spendConfirmed,
} from '../src/coasty/env.js';
import { FAKE_API_KEY } from './helpers.js';

describe('getApiKey', () => {
  it('returns the key from the env record', () => {
    expect(getApiKey({ COASTY_API_KEY: FAKE_API_KEY })).toBe(FAKE_API_KEY);
  });

  it('trims surrounding whitespace', () => {
    expect(getApiKey({ COASTY_API_KEY: `  ${FAKE_API_KEY}\n` })).toBe(FAKE_API_KEY);
  });

  it('throws MissingApiKeyError when unset', () => {
    expect(() => getApiKey({})).toThrow(MissingApiKeyError);
  });

  it('throws MissingApiKeyError when blank', () => {
    expect(() => getApiKey({ COASTY_API_KEY: '   ' })).toThrow(MissingApiKeyError);
  });

  it('never leaks the key in the error message', () => {
    try {
      getApiKey({});
      expect.unreachable('should have thrown');
    } catch (error) {
      expect((error as Error).message).toContain('COASTY_API_KEY');
      expect((error as Error).message).not.toContain(FAKE_API_KEY);
    }
  });
});

describe('getBaseUrl', () => {
  it('defaults to the public API', () => {
    expect(getBaseUrl({})).toBe('https://coasty.ai/v1');
    expect(DEFAULT_BASE_URL).toBe('https://coasty.ai/v1');
  });

  it('honors COASTY_BASE_URL', () => {
    expect(getBaseUrl({ COASTY_BASE_URL: 'http://127.0.0.1:8787/v1' })).toBe(
      'http://127.0.0.1:8787/v1',
    );
  });

  it('strips trailing slashes', () => {
    expect(getBaseUrl({ COASTY_BASE_URL: 'http://127.0.0.1:8787/v1///' })).toBe(
      'http://127.0.0.1:8787/v1',
    );
  });

  it('treats a blank value as unset', () => {
    expect(getBaseUrl({ COASTY_BASE_URL: '   ' })).toBe(DEFAULT_BASE_URL);
  });
});

describe('spendConfirmed', () => {
  it.each([
    ['1', true],
    ['true', true],
    ['TRUE', true],
    [' 1 ', true],
    ['0', false],
    ['false', false],
    ['yes', false],
    [undefined, false],
  ])('COASTY_CONFIRM_SPEND=%j -> %s', (value, expected) => {
    const env = value === undefined ? {} : { COASTY_CONFIRM_SPEND: value };
    expect(spendConfirmed(env)).toBe(expected);
  });
});

describe('isSandboxKey', () => {
  it('detects the sk-coasty-test- prefix', () => {
    expect(SANDBOX_KEY_PREFIX).toBe('sk-coasty-test-');
    expect(isSandboxKey(FAKE_API_KEY)).toBe(true);
  });

  it('rejects live and legacy keys', () => {
    expect(isSandboxKey(`sk-coasty-live-${'0'.repeat(48)}`)).toBe(false);
    expect(isSandboxKey(`cua_sk_${'0'.repeat(48)}`)).toBe(false);
  });

  it('falls back to the env record when no key is given', () => {
    expect(isSandboxKey(undefined, { COASTY_API_KEY: FAKE_API_KEY })).toBe(true);
    expect(isSandboxKey(undefined, { COASTY_API_KEY: `sk-coasty-live-${'0'.repeat(48)}` })).toBe(
      false,
    );
    expect(isSandboxKey(undefined, {})).toBe(false);
  });

  it('prefers an explicitly passed key over the env', () => {
    expect(isSandboxKey(`sk-coasty-live-${'0'.repeat(48)}`, { COASTY_API_KEY: FAKE_API_KEY })).toBe(
      false,
    );
  });
});
