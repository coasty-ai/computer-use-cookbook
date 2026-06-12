/**
 * Example 04 — Parse pyautogui code into structured actions (FREE).
 *
 * Purpose: `/parse` converts raw pyautogui source into the same structured
 * `{action_type, params}` actions that /predict returns — useful for
 * migrating legacy automation scripts, linting generated code, or replaying
 * recorded macros through a safe executor. This example parses a snippet,
 * pretty-prints the actions, and DRY-RUNS them through the defensive
 * executor's `NullBackend` (records every call, touches no real screen — and
 * never executes `raw` actions, it logs and skips them).
 *
 * Flow: POST /v1/parse { code } -> actions[] -> pretty-print ->
 * executeActions(actions, NullBackend) -> print the recorded calls.
 *
 * Endpoints: POST /v1/parse — scope `parse`, FREE (0 credits), so there is
 * NO spend gate here. Estimated cost (computed via src/coasty/cost.ts):
 * estimateParseCredits() = 0 cr = $0.00 on every key, sandbox or live.
 *
 * Run it:
 *   npx tsx src/examples/ex04-parse.ts                      # built-in sample
 *   npx tsx src/examples/ex04-parse.ts --file my_macro.py   # parse a file
 *   npx tsx src/examples/ex04-parse.ts --code "pyautogui.click(10, 20)"
 *
 * Env config: COASTY_API_KEY (required), COASTY_BASE_URL.
 */
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { CoastyClient } from '../coasty/client.js';
import { estimateParseCredits, formatEstimate } from '../coasty/cost.js';
import { getApiKey } from '../coasty/env.js';
import { CoastyError } from '../coasty/errors.js';
import {
  NullBackend,
  executeActions,
  type ExecuteResult,
  type RecordedCall,
} from '../coasty/executor.js';
import { type Action } from '../coasty/types.js';

export type PrintFn = (line: string) => void;

export const stdoutPrint: PrintFn = (line) => {
  process.stdout.write(`${line}\n`);
};

/** A representative pyautogui snippet covering several action shapes. */
export const SAMPLE_PYAUTOGUI_CODE = `import pyautogui
pyautogui.click(640, 360)
pyautogui.typewrite("hello@example.com")
pyautogui.press("tab")
pyautogui.hotkey("ctrl", "s")
pyautogui.scroll(-3)
`;

// ---------------------------------------------------------------------------
// Core (pure: everything injected, fully testable offline)
// ---------------------------------------------------------------------------

/** Numbered, aligned rendering of parsed actions. */
export function formatActionTable(actions: readonly Action[]): string {
  if (actions.length === 0) return '(no actions parsed)';
  const typeWidth = Math.max(...actions.map((action) => action.action_type.length));
  return actions
    .map((action, index) => {
      const description =
        typeof action.description === 'string' && action.description !== ''
          ? `  — ${action.description}`
          : '';
      return (
        `  ${String(index + 1).padStart(2)}. ${action.action_type.padEnd(typeWidth)} ` +
        `${JSON.stringify(action.params)}${description}`
      );
    })
    .join('\n');
}

export interface ParseAndDryRunOptions {
  client: CoastyClient;
  /** pyautogui source, non-empty, < 50,000 chars. */
  code: string;
  print?: PrintFn;
}

export interface ParseAndDryRunResult {
  actions: Action[];
  /** Per-action executor outcomes (raw actions appear with executed=false). */
  results: ExecuteResult[];
  /** Backend calls the dry-run recorded (what WOULD hit a real screen). */
  calls: RecordedCall[];
  /** From the X-Coasty-Request-Id header — quote it to support. */
  requestId: string | null;
}

/** Parse pyautogui source, pretty-print the actions, then dry-run them. */
export async function parseAndDryRun(
  options: ParseAndDryRunOptions,
): Promise<ParseAndDryRunResult> {
  const print = options.print ?? stdoutPrint;
  const { data, meta } = await options.client.parse({ code: options.code });

  print(
    `/parse returned ${String(data.actions.length)} action(s) — FREE (0 credits), ` +
      `request_id=${meta.requestId ?? 'n/a'}`,
  );
  print(formatActionTable(data.actions));

  // Dry-run: NullBackend records calls without touching any real screen.
  // The executor also refuses to execute `raw` actions (logged + skipped).
  const backend = new NullBackend();
  const results = await executeActions(data.actions, backend, { logger: print });

  print(`dry-run recorded ${String(backend.calls.length)} backend call(s):`);
  for (const call of backend.calls) {
    print(`  ${call.method}(${call.args.map((arg) => JSON.stringify(arg)).join(', ')})`);
  }

  return { actions: data.actions, results, calls: backend.calls, requestId: meta.requestId };
}

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

export interface Ex04Config {
  code: string | null;
  file: string | null;
}

export function parseArgs(argv: readonly string[]): Ex04Config {
  const config: Ex04Config = { code: null, file: null };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    const next = (): string => {
      i += 1;
      const value = argv[i];
      if (value === undefined) throw new Error(`missing value for ${String(arg)}`);
      return value;
    };
    switch (arg) {
      case '--code':
        config.code = next();
        break;
      case '--file':
        config.file = next();
        break;
      default:
        throw new Error(`unknown argument: ${String(arg)} (expected --code or --file)`);
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
  let code = config.code ?? SAMPLE_PYAUTOGUI_CODE;
  if (config.file !== null) code = await readFile(config.file, 'utf-8');

  // No spend gate: /parse is documented as free. Still show the estimate so
  // every example prints its cost up front.
  stdoutPrint(formatEstimate([{ label: '1 x POST /parse', credits: estimateParseCredits() }]));

  const client = new CoastyClient({ apiKey: getApiKey() });
  await parseAndDryRun({ client, code });
  return 0;
}

/** True when this module is the CLI entrypoint (vs being imported by tests). */
function isCliEntry(moduleUrl: string): boolean {
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
