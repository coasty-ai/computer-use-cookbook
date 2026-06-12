/**
 * Example 02 — Grounding: locate a UI element, then click it.
 *
 * Purpose: `/ground` answers "WHERE is <element> on this screen?" with a
 * single (x, y) point — no agent loop, no reasoning. This example grounds a
 * natural-language element description on a screenshot and then clicks the
 * returned point through the same injected `ExecutorBackend` pattern as
 * example 01 (the Playwright wiring is optional and lives behind a dynamic
 * import in `main()`).
 *
 * Flow: screenshot -> POST /v1/ground { screenshot, element } -> { x, y } ->
 * backend.click(x * scaleX, y * scaleY).
 *
 * Endpoints: POST /v1/ground (3 credits; +1 if the image is HD — strictly
 * wider than 1280 OR taller than 720. We send exactly 1280x720, which is NOT
 * HD, so each call is 3 cr = $0.03).
 *
 * Estimated cost (computed at runtime via src/coasty/cost.ts and printed by
 * the spend gate): 1 x estimateGroundCredits({1280, 720}) = 3 cr = $0.03.
 * Sandbox keys never bill — the gate prints "$0 (sandbox)".
 *
 * Run it:
 *   npx tsx src/examples/ex02-grounding.ts \
 *     --url https://example.com --element "the More information link" --confirm
 *
 * Env config: COASTY_API_KEY (required), COASTY_BASE_URL, EX02_URL,
 * EX02_ELEMENT, COASTY_CONFIRM_SPEND=1 (instead of --confirm).
 */
import { CoastyClient } from '../coasty/client.js';
import { estimateGroundCredits } from '../coasty/cost.js';
import { getApiKey } from '../coasty/env.js';
import { executeAction, type ExecutorBackend } from '../coasty/executor.js';
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
  type PrintFn,
  type ScreenshotProvider,
} from './ex01-local-predict-loop.js';

// ---------------------------------------------------------------------------
// Core (pure: everything injected, fully testable offline)
// ---------------------------------------------------------------------------

export interface GroundAndClickOptions {
  client: CoastyClient;
  /** Natural-language description, e.g. "the blue Submit button". */
  element: string;
  screenshot: ScreenshotProvider;
  backend: ExecutorBackend;
  /** Declared screenshot dimensions (default 1280x720). */
  screenWidth?: number;
  screenHeight?: number;
  /**
   * real/sent coordinate factors. /ground answers in the pixel space of the
   * screenshot you SENT — if you downscaled it, multiply back up (see the
   * scaling-pitfall note in example 01). Default 1 (browser viewport, 1:1).
   */
  scaleX?: number;
  scaleY?: number;
  print?: PrintFn;
}

export interface GroundAndClickResult {
  /** Model coordinates, in the SENT screenshot's pixel space (unscaled). */
  x: number;
  y: number;
  creditsCharged: number;
  /** From the X-Coasty-Request-Id header — quote it to support. */
  requestId: string | null;
}

/** Ground `element` on the current screen, then click it via the backend. */
export async function groundAndClick(
  options: GroundAndClickOptions,
): Promise<GroundAndClickResult> {
  const print = options.print ?? stdoutPrint;
  const screenshot = await options.screenshot();
  const { data, meta } = await options.client.ground({
    screenshot,
    element: options.element,
    screen_width: options.screenWidth ?? SCREEN_WIDTH,
    screen_height: options.screenHeight ?? SCREEN_HEIGHT,
  });
  print(
    `/ground located "${options.element}" at (${String(data.x)}, ${String(data.y)}) — ` +
      `${String(data.usage.credits_charged)} cr, request_id=${meta.requestId ?? 'n/a'}`,
  );
  // Route the click through the defensive executor so scaling (and the
  // click param-shape quirks) are handled in exactly one place.
  await executeAction({ action_type: 'click', params: { x: data.x, y: data.y } }, options.backend, {
    scaleX: options.scaleX ?? 1,
    scaleY: options.scaleY ?? 1,
    logger: print,
  });
  print('clicked.');
  return {
    x: data.x,
    y: data.y,
    creditsCharged: data.usage.credits_charged,
    requestId: meta.requestId,
  };
}

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

export interface Ex02Config {
  url: string;
  element: string;
  headless: boolean;
  confirm: boolean;
}

export function parseArgs(
  argv: readonly string[],
  env: NodeJS.ProcessEnv = process.env,
): Ex02Config {
  const config: Ex02Config = {
    url: env.EX02_URL ?? 'https://example.com',
    element: env.EX02_ELEMENT ?? 'the "More information..." link',
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
      case '--element':
      case '-e':
        config.element = next();
        break;
      case '--headed':
        config.headless = false;
        break;
      case '--confirm':
        config.confirm = true;
        break;
      default:
        throw new Error(
          `unknown argument: ${String(arg)} (expected --url, --element, --headed, --confirm)`,
        );
    }
  }
  return config;
}

export async function main(argv: readonly string[] = process.argv.slice(2)): Promise<number> {
  const config = parseArgs(argv);
  const apiKey = getApiKey();
  const approved = ensureSpendApproved({
    apiKey,
    confirmFlag: config.confirm,
    items: [
      {
        label: `1 x POST /ground @ ${String(SCREEN_WIDTH)}x${String(SCREEN_HEIGHT)} (not HD)`,
        credits: estimateGroundCredits({
          screenWidth: SCREEN_WIDTH,
          screenHeight: SCREEN_HEIGHT,
        }),
      },
    ],
  });
  if (!approved) return 1;

  const client = new CoastyClient({ apiKey });
  const { page, close } = await openPlaywrightPage({ url: config.url, headless: config.headless });
  try {
    const result = await groundAndClick({
      client,
      element: config.element,
      screenshot: createPageScreenshotProvider(page),
      backend: createPageBackend(page),
    });
    stdoutPrint(
      `result: clicked (${String(result.x)}, ${String(result.y)}), ` +
        `${String(result.creditsCharged)} cr charged, request_id=${result.requestId ?? 'n/a'}`,
    );
    return 0;
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
