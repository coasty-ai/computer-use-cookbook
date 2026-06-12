/**
 * Example 03 — Stateful sessions: create -> multi-step predict -> delete.
 *
 * Purpose: sessions keep the trajectory (recent screenshots + actions)
 * SERVER-side, so each step is a smaller request AND cheaper than stateless
 * /predict with a client-side trajectory (4 cr vs 5 cr + 2 cr per attached
 * trajectory screenshot). This example walks the full lifecycle:
 *
 *   1. POST /v1/sessions                     (create — 10 cr one-time)
 *   2. POST /v1/sessions/{id}/predict  x N   (4 cr each at <=1280x720)
 *   3. GET  /v1/sessions/{id}                (info: step_count, credits — free)
 *   4. POST /v1/sessions/{id}/reset          (clear trajectory, keep slot — free)
 *   5. DELETE /v1/sessions/{id}              (ALWAYS, in `finally` — free)
 *
 * The delete is in a `finally` block on purpose: sessions hold a concurrency
 * slot until they expire, so leaking them on errors throttles future work.
 *
 * The screen is driven through the same injected screenshot-provider +
 * ExecutorBackend pattern as example 01 (optional Playwright in `main()`).
 *
 * Estimated cost (computed at runtime via src/coasty/cost.ts and printed by
 * the spend gate): estimateSessionCreateCredits() + maxSteps x
 * estimateSessionPredictCredits({1280, 720}) = 10 + 6 x 4 = 34 cr = $0.34
 * ceiling with the defaults. Sandbox keys never bill ("$0 (sandbox)").
 *
 * Run it:
 *   npx tsx src/examples/ex03-sessions.ts \
 *     --url https://example.com --instruction "Click the More information link" \
 *     --max-steps 6 --confirm
 *
 * Env config: COASTY_API_KEY (required), COASTY_BASE_URL, EX03_URL,
 * EX03_INSTRUCTION, EX03_MAX_STEPS, COASTY_CONFIRM_SPEND=1.
 */
import { randomUUID } from 'node:crypto';
import { CoastyClient } from '../coasty/client.js';
import {
  estimateSessionCreateCredits,
  estimateSessionPredictCredits,
  formatUsd,
} from '../coasty/cost.js';
import { getApiKey } from '../coasty/env.js';
import { executeActions, type ExecutorBackend } from '../coasty/executor.js';
import { type CuaVersion } from '../coasty/types.js';
import {
  SCREEN_HEIGHT,
  SCREEN_WIDTH,
  createPageBackend,
  createPageScreenshotProvider,
  describeError,
  ensureSpendApproved,
  isCliEntry,
  openPlaywrightPage,
  stdoutPrint,
  type PredictLoopStatus,
  type PrintFn,
  type ScreenshotProvider,
} from './ex01-local-predict-loop.js';

// ---------------------------------------------------------------------------
// Core (pure: everything injected, fully testable offline)
// ---------------------------------------------------------------------------

export interface SessionLoopOptions {
  client: CoastyClient;
  instruction: string;
  screenshot: ScreenshotProvider;
  backend: ExecutorBackend;
  /** Hard cap on session predict calls (default 6). */
  maxSteps?: number;
  screenWidth?: number;
  screenHeight?: number;
  cuaVersion?: CuaVersion;
  /** Server-side trajectory window, 1-20 (default 3). */
  maxTrajectoryLength?: number;
  /** Also demonstrate GET info + POST reset after the loop (default true). */
  showLifecycle?: boolean;
  print?: PrintFn;
}

export interface SessionLoopResult {
  sessionId: string;
  status: PredictLoopStatus;
  /** Number of /sessions/{id}/predict calls made. */
  stepsUsed: number;
  /** Client-side sum of usage.credits_charged (excludes the 10 cr create fee). */
  creditsCharged: number;
  /** step_count reported by GET /sessions/{id} (null when showLifecycle=false). */
  stepCountReported: number | null;
  /** total_credits_used reported by GET /sessions/{id}. */
  totalCreditsReported: number | null;
  failReason: string | null;
}

/**
 * Full session lifecycle. The session is ALWAYS deleted — on success AND
 * when a predict throws mid-loop (finally semantics, structured as an
 * explicit catch + rethrow so a cleanup failure during error unwind is
 * printed, never thrown, and the original error is what callers see).
 */
export async function runSessionLoop(options: SessionLoopOptions): Promise<SessionLoopResult> {
  const maxSteps = options.maxSteps ?? 6;
  const print = options.print ?? stdoutPrint;

  const { data: created } = await options.client.sessions.create({
    cua_version: options.cuaVersion ?? 'v3',
    screen_width: options.screenWidth ?? SCREEN_WIDTH,
    screen_height: options.screenHeight ?? SCREEN_HEIGHT,
    max_trajectory_length: options.maxTrajectoryLength ?? 3,
  });
  const sessionId = created.session_id;
  print(
    `created session ${sessionId} (cua_version=${created.cua_version}, ` +
      `screen=${created.screen_size}, expires_at=${created.expires_at})`,
  );

  // ALWAYS delete: sessions hold a concurrency slot until they expire.
  // `suppressError` is true on the error-unwind path so a cleanup failure
  // can never mask the original error (it is printed instead).
  const deleteSession = async (suppressError: boolean): Promise<void> => {
    try {
      await options.client.sessions.delete(sessionId);
      print(`deleted session ${sessionId} (concurrency slot freed).`);
    } catch (deleteError) {
      if (!suppressError) throw deleteError;
      print(`warning: failed to delete session ${sessionId}: ${describeError(deleteError)}`);
    }
  };

  let result: SessionLoopResult;
  try {
    let status: PredictLoopStatus = 'max_steps';
    let stepsUsed = 0;
    let creditsCharged = 0;
    let failReason: string | null = null;

    for (let step = 1; step <= maxSteps; step += 1) {
      const screenshot = await options.screenshot();
      const { data } = await options.client.sessions.predict(
        sessionId,
        { screenshot, instruction: options.instruction },
        // Session predicts advance server-side trajectory state, so they are
        // NOT inherently retry-safe; an Idempotency-Key opts into retries.
        { idempotencyKey: `ex03-step${String(step)}-${randomUUID()}` },
      );
      stepsUsed = step;
      creditsCharged += data.usage.credits_charged;
      print(
        `[step ${String(data.step)}] status=${data.status} actions=${String(data.actions.length)} ` +
          `credits=${String(data.usage.credits_charged)}` +
          (data.reasoning !== null ? ` reasoning=${data.reasoning}` : ''),
      );

      const results = await executeActions(data.actions, options.backend, { logger: print });
      const terminal = results.find((result) => result.terminal !== null) ?? null;
      if (data.status === 'fail' || terminal?.terminal === 'fail') {
        status = 'fail';
        failReason = terminal?.detail ?? data.reasoning;
        break;
      }
      if (data.status === 'done' || terminal?.terminal === 'done') {
        status = 'done';
        break;
      }
    }

    let stepCountReported: number | null = null;
    let totalCreditsReported: number | null = null;
    if (options.showLifecycle ?? true) {
      // GET info is free: the server's own view of steps + credits spent.
      const { data: info } = await options.client.sessions.get(sessionId);
      stepCountReported = info.step_count;
      totalCreditsReported = info.total_credits_used;
      print(
        `session info: step_count=${String(info.step_count)}, ` +
          `total_credits_used=${String(info.total_credits_used)} ` +
          `(${formatUsd(info.total_credits_used)})`,
      );
      // Reset (free) clears the trajectory but keeps the session: start the
      // next task on the same session without paying the 10 cr create fee.
      await options.client.sessions.reset(sessionId);
      print('session reset: trajectory cleared, session (and its slot) kept.');
    }

    result = {
      sessionId,
      status,
      stepsUsed,
      creditsCharged,
      stepCountReported,
      totalCreditsReported,
      failReason,
    };
  } catch (error) {
    await deleteSession(true); // cleanup runs even though the loop threw
    throw error;
  }
  await deleteSession(false); // success path: a delete failure is a real error
  return result;
}

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

export interface Ex03Config {
  url: string;
  instruction: string;
  maxSteps: number;
  headless: boolean;
  confirm: boolean;
}

function intOption(name: string, raw: string | undefined, fallback: number): number {
  if (raw === undefined || raw.trim() === '') return fallback;
  const value = Number(raw);
  if (!Number.isInteger(value) || value < 1) {
    throw new Error(`${name} must be a positive integer, got: ${raw}`);
  }
  return value;
}

export function parseArgs(
  argv: readonly string[],
  env: NodeJS.ProcessEnv = process.env,
): Ex03Config {
  const config: Ex03Config = {
    url: env.EX03_URL ?? 'https://example.com',
    instruction: env.EX03_INSTRUCTION ?? 'Click the "More information..." link.',
    maxSteps: intOption('EX03_MAX_STEPS', env.EX03_MAX_STEPS, 6),
    headless: true,
    confirm: false,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    const next = (): string => {
      i += 1;
      const value = argv[i];
      if (value === undefined) throw new Error(`missing value for ${String(arg)}`);
      return value;
    };
    switch (arg) {
      case '--url':
        config.url = next();
        break;
      case '--instruction':
      case '-i':
        config.instruction = next();
        break;
      case '--max-steps':
        config.maxSteps = intOption('--max-steps', next(), config.maxSteps);
        break;
      case '--headed':
        config.headless = false;
        break;
      case '--confirm':
        config.confirm = true;
        break;
      default:
        throw new Error(
          `unknown argument: ${String(arg)} (expected --url, --instruction, --max-steps, --headed, --confirm)`,
        );
    }
  }
  return config;
}

export async function main(argv: readonly string[] = process.argv.slice(2)): Promise<number> {
  const config = parseArgs(argv);
  const apiKey = getApiKey();
  const perStep = estimateSessionPredictCredits({
    screenWidth: SCREEN_WIDTH,
    screenHeight: SCREEN_HEIGHT,
  });
  const approved = ensureSpendApproved({
    apiKey,
    confirmFlag: config.confirm,
    items: [
      { label: '1 x POST /sessions (create)', credits: estimateSessionCreateCredits() },
      {
        label: `${String(config.maxSteps)} x POST /sessions/{id}/predict @ ${String(SCREEN_WIDTH)}x${String(SCREEN_HEIGHT)}`,
        credits: config.maxSteps * perStep,
      },
      { label: 'get + reset + delete', credits: 0 },
    ],
  });
  if (!approved) return 1;

  const client = new CoastyClient({ apiKey });
  const { page, close } = await openPlaywrightPage({ url: config.url, headless: config.headless });
  try {
    const result = await runSessionLoop({
      client,
      instruction: config.instruction,
      screenshot: createPageScreenshotProvider(page),
      backend: createPageBackend(page),
      maxSteps: config.maxSteps,
    });
    stdoutPrint(
      `result: ${result.status} after ${String(result.stepsUsed)} step(s), ` +
        `${String(result.creditsCharged)} cr in predicts (${formatUsd(result.creditsCharged)})`,
    );
    return result.status === 'fail' ? 1 : 0;
  } finally {
    await close();
  }
}

if (isCliEntry(import.meta.url)) {
  void main()
    .then((code) => {
      process.exitCode = code;
    })
    .catch((error: unknown) => {
      console.error(describeError(error));
      process.exitCode = 1;
    });
}
