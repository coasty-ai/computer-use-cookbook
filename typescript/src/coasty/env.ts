/**
 * Environment / configuration helpers for the Coasty cookbook.
 *
 * Loads the repo-root `.env` (if present) exactly once via dotenv. Values are
 * merged into `process.env` and are NEVER logged by this module.
 */
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import dotenv from 'dotenv';

export const DEFAULT_BASE_URL = 'https://coasty.ai/v1';
export const SANDBOX_KEY_PREFIX = 'sk-coasty-test-';

let dotenvLoaded = false;

function repoRootEnvPath(): string {
  // This file lives at <repo>/typescript/src/coasty/env.ts -> repo root is 3 levels up.
  const here = path.dirname(fileURLToPath(import.meta.url));
  return path.resolve(here, '..', '..', '..', '.env');
}

/** Load the repo-root `.env` into `process.env` once. Existing vars win. */
export function loadEnvFile(): void {
  if (dotenvLoaded) return;
  dotenvLoaded = true;
  const envPath = repoRootEnvPath();
  if (existsSync(envPath)) {
    // `quiet` suppresses dotenv's injection banner; values are never printed.
    dotenv.config({ path: envPath, quiet: true });
  }
}

/** Thrown when `COASTY_API_KEY` is missing. */
export class MissingApiKeyError extends Error {
  constructor() {
    super(
      'COASTY_API_KEY is not set. Export it in your shell or add it to the repo-root .env file ' +
        '(see .env.example). Use an sk-coasty-test-* sandbox key for free, unbilled development.',
    );
    this.name = 'MissingApiKeyError';
  }
}

/**
 * The API key from `COASTY_API_KEY` (after loading `.env`).
 * @throws {MissingApiKeyError} when unset or blank.
 */
export function getApiKey(env: NodeJS.ProcessEnv = process.env): string {
  if (env === process.env) loadEnvFile();
  const key = env.COASTY_API_KEY?.trim();
  if (key === undefined || key === '') throw new MissingApiKeyError();
  return key;
}

/** Base URL from `COASTY_BASE_URL`, defaulting to the public API. Trailing slashes stripped. */
export function getBaseUrl(env: NodeJS.ProcessEnv = process.env): string {
  if (env === process.env) loadEnvFile();
  const raw = env.COASTY_BASE_URL?.trim();
  const url = raw === undefined || raw === '' ? DEFAULT_BASE_URL : raw;
  return url.replace(/\/+$/, '');
}

/** True when the user pre-confirmed spend via `COASTY_CONFIRM_SPEND=1` (or `true`). */
export function spendConfirmed(env: NodeJS.ProcessEnv = process.env): boolean {
  if (env === process.env) loadEnvFile();
  const value = env.COASTY_CONFIRM_SPEND?.trim().toLowerCase();
  return value === '1' || value === 'true';
}

/**
 * True when the given key (or `COASTY_API_KEY` if omitted) is a sandbox key
 * (`sk-coasty-test-*` — never bills).
 */
export function isSandboxKey(key?: string, env: NodeJS.ProcessEnv = process.env): boolean {
  if (key === undefined && env === process.env) loadEnvFile();
  const candidate = key ?? env.COASTY_API_KEY;
  return typeof candidate === 'string' && candidate.startsWith(SANDBOX_KEY_PREFIX);
}
