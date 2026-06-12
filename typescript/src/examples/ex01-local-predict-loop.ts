/**
 * Example 01 — Local screen predict-loop (browser target).
 *
 * Purpose: drive a live screen with the stateless `/predict` endpoint:
 * screenshot -> POST /v1/predict -> execute the returned actions -> repeat
 * while `status === "continue"`, capped by `--max-steps`.
 *
 * The "screen" here is a Playwright page pinned to a 1280x720 viewport:
 * `page.screenshot()` goes in, `page.mouse` / `page.keyboard` actions come
 * out. Playwright is OPTIONAL — the core loop only needs an injected
 * screenshot provider + an `ExecutorBackend` (src/coasty/executor.ts), and
 * the real Playwright wiring sits behind a dynamic import in `main()`.
 *
 * Endpoints: POST /v1/predict (5 credits per call at <=1280x720; the HD
 * surcharge does NOT apply because exactly 1280x720 is not HD).
 *
 * Estimated cost (computed at runtime via src/coasty/cost.ts and printed by
 * the spend gate): maxSteps x estimatePredictCredits({1280, 720}) =
 * 8 x 5 cr = 40 cr = $0.40 ceiling with the defaults — the loop usually
 * finishes sooner and only completed steps bill. Sandbox keys
 * (sk-coasty-test-*) never bill: the gate prints "$0 (sandbox)".
 *
 * Run it:
 *   npx tsx src/examples/ex01-local-predict-loop.ts \
 *     --url https://example.com --instruction "Click the More information link" \
 *     --max-steps 8 --confirm
 *
 * Env config: COASTY_API_KEY (required), COASTY_BASE_URL, EX01_URL,
 * EX01_INSTRUCTION, EX01_MAX_STEPS, COASTY_CONFIRM_SPEND=1 (instead of
 * --confirm).
 */
import { randomUUID } from 'node:crypto';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { CoastyClient, type CreateCallOptions } from '../coasty/client.js';
import {
  estimatePredictCredits,
  formatEstimate,
  formatUsd,
  type EstimateLineItem,
} from '../coasty/cost.js';
import { getApiKey, isSandboxKey, spendConfirmed } from '../coasty/env.js';
import { CoastyError } from '../coasty/errors.js';
import { executeActions, type ExecutorBackend } from '../coasty/executor.js';
import { type CuaVersion } from '../coasty/types.js';

// ---------------------------------------------------------------------------
// COORDINATE SCALING PITFALL (read this before pointing the loop at anything)
// ---------------------------------------------------------------------------
// /predict returns coordinates in the pixel space of the screenshot you SENT,
// together with the screen_width/screen_height you declared. This example
// pins the Playwright viewport to EXACTLY 1280x720 and sends
// screen_width=1280 / screen_height=720, so model coordinates map 1:1 onto
// page.mouse coordinates (scaleX = scaleY = 1). Bonus: 1280x720 stays just
// under the HD surcharge (strictly >1280 wide OR >720 tall adds +1 credit
// per image; exactly 1280x720 is NOT HD).
//
// A DESKTOP backend (pyautogui / nut-js) differs in two ways:
//   1. You capture the PHYSICAL screen (e.g. 2560x1440, often DPI-scaled),
//      so you should downscale the PNG to <=1280x720 before sending, declare
//      the DOWNSCALED dimensions, and then multiply the returned x/y by
//      (real / sent) before moving the real mouse — that is exactly what
//      ExecuteOptions.scaleX / scaleY in src/coasty/executor.ts are for.
//   2. OS DPI scaling can make "screenshot pixels" differ from "mouse
//      coordinates" even without downscaling. Browser viewports avoid the
//      trap entirely: page.screenshot() captures CSS pixels that match
//      page.mouse coordinates 1:1 (at deviceScaleFactor 1).
// ---------------------------------------------------------------------------

export const SCREEN_WIDTH = 1280;
export const SCREEN_HEIGHT = 720;

export type PrintFn = (line: string) => void;

export const stdoutPrint: PrintFn = (line) => {
  process.stdout.write(`${line}\n`);
};

/** Returns one screenshot as raw base64 (NO `data:` prefix), as /predict expects. */
export type ScreenshotProvider = () => Promise<string>;

// ---------------------------------------------------------------------------
// PageLike — the minimal structural subset of a Playwright `Page` we need.
// Because the loop depends only on this interface (not on playwright types),
// the example compiles, lints and tests WITHOUT playwright installed.
// ---------------------------------------------------------------------------

export interface PageLikeMouse {
  click(
    x: number,
    y: number,
    options?: { button?: 'left' | 'right' | 'middle'; clickCount?: number },
  ): Promise<void>;
  move(x: number, y: number): Promise<void>;
  down(): Promise<void>;
  up(): Promise<void>;
  wheel(deltaX: number, deltaY: number): Promise<void>;
}

export interface PageLikeKeyboard {
  type(text: string): Promise<void>;
  press(key: string): Promise<void>;
}

export interface PageLike {
  screenshot(options?: { type?: 'png' }): Promise<Uint8Array>;
  mouse: PageLikeMouse;
  keyboard: PageLikeKeyboard;
}

/** Coasty key names (lowercase pyautogui style) -> Playwright key names. */
const PLAYWRIGHT_KEY_MAP: Readonly<Record<string, string>> = {
  enter: 'Enter',
  return: 'Enter',
  tab: 'Tab',
  esc: 'Escape',
  escape: 'Escape',
  space: 'Space',
  backspace: 'Backspace',
  delete: 'Delete',
  del: 'Delete',
  up: 'ArrowUp',
  down: 'ArrowDown',
  left: 'ArrowLeft',
  right: 'ArrowRight',
  home: 'Home',
  end: 'End',
  pageup: 'PageUp',
  pagedown: 'PageDown',
  ctrl: 'Control',
  control: 'Control',
  alt: 'Alt',
  shift: 'Shift',
  win: 'Meta',
  cmd: 'Meta',
  meta: 'Meta',
};

/** Map a Coasty/pyautogui key name ("ctrl", "enter", "a") to Playwright's. */
export function toPlaywrightKey(key: string): string {
  const mapped = PLAYWRIGHT_KEY_MAP[key.toLowerCase()];
  if (mapped !== undefined) return mapped;
  if (key.length === 1) return key; // letters, digits, punctuation
  return key.charAt(0).toUpperCase() + key.slice(1); // F1..F12, Insert, ...
}

/** Pixels scrolled per scroll "click" / amount unit. */
export const SCROLL_PIXELS_PER_UNIT = 100;

/** Wrap a PageLike into the ExecutorBackend the defensive executor drives. */
export function createPageBackend(page: PageLike): ExecutorBackend {
  return {
    async click(x, y, options): Promise<void> {
      await page.mouse.click(x, y, { button: options.button, clickCount: options.clicks });
    },
    async move(x, y): Promise<void> {
      await page.mouse.move(x, y);
    },
    async typeText(text): Promise<void> {
      await page.keyboard.type(text);
    },
    async keyPress(key): Promise<void> {
      await page.keyboard.press(toPlaywrightKey(key));
    },
    async keyCombo(keys): Promise<void> {
      // Playwright chords are "+"-joined: "Control+Shift+S".
      await page.keyboard.press(keys.map(toPlaywrightKey).join('+'));
    },
    async scroll(options): Promise<void> {
      if (options.x !== null && options.y !== null) await page.mouse.move(options.x, options.y);
      const pixels = options.amount * SCROLL_PIXELS_PER_UNIT;
      const deltaX =
        options.direction === 'left' ? -pixels : options.direction === 'right' ? pixels : 0;
      const deltaY =
        options.direction === 'up' ? -pixels : options.direction === 'down' ? pixels : 0;
      await page.mouse.wheel(deltaX, deltaY);
    },
    async drag(fromX, fromY, toX, toY): Promise<void> {
      await page.mouse.move(fromX, fromY);
      await page.mouse.down();
      await page.mouse.move(toX, toY);
      await page.mouse.up();
    },
    async wait(ms): Promise<void> {
      await new Promise((resolve) => setTimeout(resolve, ms));
    },
  };
}

/** Screenshot provider: PNG bytes from the page -> raw base64 (no data: prefix). */
export function createPageScreenshotProvider(page: PageLike): ScreenshotProvider {
  return async () => Buffer.from(await page.screenshot({ type: 'png' })).toString('base64');
}

// ---------------------------------------------------------------------------
// Core loop (pure: everything injected, fully testable offline)
// ---------------------------------------------------------------------------

export interface PredictLoopOptions {
  client: CoastyClient;
  instruction: string;
  screenshot: ScreenshotProvider;
  backend: ExecutorBackend;
  /** Hard cap on /predict calls (default 8). */
  maxSteps?: number;
  /** Declared screenshot dimensions (default 1280x720 — matches the viewport). */
  screenWidth?: number;
  screenHeight?: number;
  cuaVersion?: CuaVersion;
  /** real/sent coordinate factors for desktop backends (default 1 — see pitfall note). */
  scaleX?: number;
  scaleY?: number;
  /** Per-step Idempotency-Key factory (default: random, prefixed with the step). */
  idempotencyKey?: (step: number) => string;
  print?: PrintFn;
}

export type PredictLoopStatus = 'done' | 'fail' | 'max_steps';

export interface PredictLoopResult {
  status: PredictLoopStatus;
  /** Number of /predict calls made (each one bills). */
  stepsUsed: number;
  /** Sum of usage.credits_charged across all steps. */
  creditsCharged: number;
  /** The `fail` action's reason (or last reasoning) when status === "fail". */
  failReason: string | null;
  /** request_id of the last /predict response — quote it to support. */
  lastRequestId: string | null;
}

/**
 * The predict loop: screenshot -> /predict -> execute -> repeat while the
 * model says "continue". Stops on a `done`/`fail` response status, on a
 * terminal `done`/`fail` ACTION (defensive: some responses carry the marker
 * action while still saying "continue"), or at `maxSteps`.
 */
export async function runPredictLoop(options: PredictLoopOptions): Promise<PredictLoopResult> {
  const maxSteps = options.maxSteps ?? 8;
  const screenWidth = options.screenWidth ?? SCREEN_WIDTH;
  const screenHeight = options.screenHeight ?? SCREEN_HEIGHT;
  const print = options.print ?? stdoutPrint;
  const keyFor =
    options.idempotencyKey ?? ((step: number) => `ex01-step${String(step)}-${randomUUID()}`);

  let creditsCharged = 0;
  let lastRequestId: string | null = null;

  for (let step = 1; step <= maxSteps; step += 1) {
    const screenshot = await options.screenshot();
    // A fresh Idempotency-Key per step means a transport-level retry of THIS
    // step can never double-bill, while distinct steps stay distinct calls.
    const callOptions: CreateCallOptions = { idempotencyKey: keyFor(step) };
    const { data, meta } = await options.client.predict(
      {
        screenshot,
        instruction: options.instruction,
        cua_version: options.cuaVersion ?? 'v3',
        screen_width: screenWidth,
        screen_height: screenHeight,
      },
      callOptions,
    );

    lastRequestId = data.request_id !== '' ? data.request_id : meta.requestId;
    creditsCharged += data.usage.credits_charged;
    print(
      `[step ${String(step)}/${String(maxSteps)}] status=${data.status} ` +
        `actions=${String(data.actions.length)} credits=${String(data.usage.credits_charged)}` +
        (data.reasoning !== null ? ` reasoning=${data.reasoning}` : ''),
    );

    const results = await executeActions(data.actions, options.backend, {
      scaleX: options.scaleX ?? 1,
      scaleY: options.scaleY ?? 1,
      logger: print,
    });
    const terminal = results.find((result) => result.terminal !== null) ?? null;

    if (data.status === 'fail' || terminal?.terminal === 'fail') {
      const failReason = terminal?.detail ?? data.reasoning;
      print(`loop FAILED after ${String(step)} step(s): ${failReason ?? 'no reason given'}`);
      return { status: 'fail', stepsUsed: step, creditsCharged, failReason, lastRequestId };
    }
    if (data.status === 'done' || terminal?.terminal === 'done') {
      print(`loop DONE after ${String(step)} step(s).`);
      return { status: 'done', stepsUsed: step, creditsCharged, failReason: null, lastRequestId };
    }
  }

  print(`loop stopped: --max-steps=${String(maxSteps)} reached without done/fail.`);
  return {
    status: 'max_steps',
    stepsUsed: maxSteps,
    creditsCharged,
    failReason: null,
    lastRequestId,
  };
}

// ---------------------------------------------------------------------------
// Spend gate
// ---------------------------------------------------------------------------

export interface SpendGateOptions {
  apiKey: string;
  items: readonly EstimateLineItem[];
  /** True when the user passed --confirm. */
  confirmFlag: boolean;
  env?: NodeJS.ProcessEnv;
  print?: PrintFn;
}

/**
 * Print the itemized estimate, then decide whether billable calls may
 * proceed: sandbox keys always may ("$0 (sandbox)"); live keys require
 * `--confirm` or `COASTY_CONFIRM_SPEND=1`.
 */
export function ensureSpendApproved(options: SpendGateOptions): boolean {
  const print = options.print ?? stdoutPrint;
  const sandbox = isSandboxKey(options.apiKey);
  print(formatEstimate(options.items, { sandbox }));
  if (sandbox) {
    print('Spend gate: $0 (sandbox) — sandbox keys never bill; proceeding.');
    return true;
  }
  if (options.confirmFlag || spendConfirmed(options.env ?? process.env)) {
    print('Spend gate: confirmed — proceeding with billable calls.');
    return true;
  }
  print(
    'Spend gate: BLOCKED — this key bills real money. Re-run with --confirm ' +
      'or set COASTY_CONFIRM_SPEND=1 to proceed.',
  );
  return false;
}

// ---------------------------------------------------------------------------
// Optional Playwright wiring (dynamic import — never needed by tests/CI)
// ---------------------------------------------------------------------------

export interface PlaywrightPage extends PageLike {
  goto(url: string): Promise<unknown>;
}

interface PlaywrightBrowser {
  newPage(options?: { viewport: { width: number; height: number } }): Promise<PlaywrightPage>;
  close(): Promise<void>;
}

interface PlaywrightModule {
  chromium: { launch(options?: { headless?: boolean }): Promise<PlaywrightBrowser> };
}

export const PLAYWRIGHT_INSTALL_HINT =
  'Playwright is an OPTIONAL dependency of this example and is not installed.\n' +
  'Install it with:\n' +
  '  npm install -D playwright\n' +
  '  npx playwright install chromium\n' +
  'then re-run this example.';

/** Launch Chromium at a fixed viewport and navigate. Throws a clear hint when playwright is missing. */
export async function openPlaywrightPage(options: {
  url: string;
  headless: boolean;
  width?: number;
  height?: number;
}): Promise<{ page: PlaywrightPage; close: () => Promise<void> }> {
  // The module name is deliberately widened to `string` so tsc does not try
  // to resolve types for a package that may not be installed.
  const moduleName: string = 'playwright';
  let playwright: PlaywrightModule;
  try {
    playwright = (await import(moduleName)) as PlaywrightModule;
  } catch (cause) {
    throw new Error(PLAYWRIGHT_INSTALL_HINT, { cause });
  }
  const browser = await playwright.chromium.launch({ headless: options.headless });
  const page = await browser.newPage({
    viewport: { width: options.width ?? SCREEN_WIDTH, height: options.height ?? SCREEN_HEIGHT },
  });
  await page.goto(options.url);
  return { page, close: () => browser.close() };
}

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

export interface Ex01Config {
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
): Ex01Config {
  const config: Ex01Config = {
    url: env.EX01_URL ?? 'https://example.com',
    instruction: env.EX01_INSTRUCTION ?? 'Click the "More information..." link.',
    maxSteps: intOption('EX01_MAX_STEPS', env.EX01_MAX_STEPS, 8),
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

export function describeError(error: unknown): string {
  if (error instanceof CoastyError) {
    return `${error.code}: ${error.message} (request_id=${error.requestId ?? 'n/a'})`;
  }
  return error instanceof Error ? error.message : String(error);
}

export async function main(argv: readonly string[] = process.argv.slice(2)): Promise<number> {
  const config = parseArgs(argv);
  const apiKey = getApiKey();
  const perStep = estimatePredictCredits({
    screenWidth: SCREEN_WIDTH,
    screenHeight: SCREEN_HEIGHT,
  });
  const approved = ensureSpendApproved({
    apiKey,
    confirmFlag: config.confirm,
    items: [
      {
        label: `${String(config.maxSteps)} x POST /predict @ ${String(SCREEN_WIDTH)}x${String(SCREEN_HEIGHT)} (not HD)`,
        credits: config.maxSteps * perStep,
      },
    ],
  });
  if (!approved) return 1;

  const client = new CoastyClient({ apiKey });
  const { page, close } = await openPlaywrightPage({ url: config.url, headless: config.headless });
  try {
    const result = await runPredictLoop({
      client,
      instruction: config.instruction,
      screenshot: createPageScreenshotProvider(page),
      backend: createPageBackend(page),
      maxSteps: config.maxSteps,
    });
    stdoutPrint(
      `result: ${result.status} after ${String(result.stepsUsed)} step(s), ` +
        `${String(result.creditsCharged)} cr charged (${formatUsd(result.creditsCharged)}), ` +
        `last request_id=${result.lastRequestId ?? 'n/a'}`,
    );
    return result.status === 'fail' ? 1 : 0;
  } finally {
    await close();
  }
}

/** True when this module is the CLI entrypoint (vs being imported by tests). */
export function isCliEntry(moduleUrl: string): boolean {
  const entry = process.argv[1];
  if (entry === undefined) return false;
  return pathToFileURL(path.resolve(entry)).href.toLowerCase() === moduleUrl.toLowerCase();
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
