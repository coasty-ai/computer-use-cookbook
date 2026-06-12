/**
 * Example 10 — Cost/billing helper CLI.
 *
 * Local-only arithmetic over the documented pricing table (1 credit = 1 cent
 * = $0.01 exactly) via the shared `cost.ts` estimator. Subcommands:
 *
 *   predict   --width --height --trajectory --cua --prompt-chars
 *   session   --steps + the predict flags (create is 10 cr, surcharge-free)
 *   ground    --width --height                       (3 cr, +1 if HD)
 *   run       --steps --cua                          (5 cr/step, 8 on v1)
 *   workflow  --task-steps --cua                     (control-flow steps FREE)
 *   machine   --os|--windows --hours --stopped-hours --snapshots
 *   plan      --file batch.json   (totals a JSON batch of the above)
 *
 * Surcharges itemized: +2 cr per trajectory screenshot, +1 cr per HD image
 * (STRICTLY width > 1280 OR height > 720 — exactly 1280x720 is NOT HD,
 * applied to the current shot AND each trajectory shot), +3 cr per request on
 * the v1 engine, +1 cr when system_prompt exceeds 500 chars (exactly 500 is
 * free). Machine runtime is metered per minute and ROUNDED DOWN.
 *
 * Estimated cost of running this example: 0 credits ($0.00) — it never calls
 * the API. Every report also reminds you that sandbox keys never bill and
 * that charges are debited up front and auto-refunded on failure.
 *
 * Run it:
 *   npx tsx src/examples/ex10-cost-helper.ts predict --width 1280 --height 720
 *   npx tsx src/examples/ex10-cost-helper.ts machine --windows --hours 2 --snapshots 1
 *   npx tsx src/examples/ex10-cost-helper.ts plan --file ./batch.json
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';

import {
  PRICING,
  SYSTEM_PROMPT_FREE_CHARS,
  creditsToUsd,
  estimateGroundCredits,
  estimateMachineRuntimeCredits,
  estimatePredictCredits,
  estimateRunCredits,
  estimateSessionCreateCredits,
  estimateSessionPredictCredits,
  estimateWorkflowRunCredits,
  formatEstimate,
  isHdImage,
  snapshotCredits,
  type EstimateLineItem,
} from '../coasty/cost.js';
import { isSandboxKey } from '../coasty/env.js';
import { type CuaVersion, type OsType } from '../coasty/types.js';

// ---------------------------------------------------------------------------
// Report shape
// ---------------------------------------------------------------------------

export interface CostReport {
  title: string;
  items: EstimateLineItem[];
  totalCredits: number;
  /** Dollars, exact: credits / 100. */
  totalUsd: number;
  notes: string[];
}

export const STANDARD_NOTES: readonly string[] = [
  'sandbox keys (sk-coasty-test-*) never bill: every total is $0.00 in test mode',
  'charges are debited up front and auto-refunded on failure (incl. snapshots)',
];

export class UsageError extends Error {
  constructor(message: string) {
    super(`${message}\n\n${USAGE}`);
    this.name = 'UsageError';
  }
}

function finalize(title: string, items: EstimateLineItem[], notes: string[] = []): CostReport {
  const totalCredits = items.reduce((sum, item) => sum + item.credits, 0);
  return {
    title,
    items,
    totalCredits,
    totalUsd: creditsToUsd(totalCredits),
    notes: [...notes, ...STANDARD_NOTES],
  };
}

// ---------------------------------------------------------------------------
// Per-operation estimators (each returns an ITEMIZED report)
// ---------------------------------------------------------------------------

export interface InferenceArgs {
  width: number;
  height: number;
  trajectory: number;
  cuaVersion: CuaVersion;
  promptChars: number;
}

export const INFERENCE_DEFAULTS: InferenceArgs = {
  width: 1920,
  height: 1080,
  trajectory: 0,
  cuaVersion: 'v3',
  promptChars: 0,
};

function inferenceSurchargeItems(args: InferenceArgs, perRequest: number): EstimateLineItem[] {
  const items: EstimateLineItem[] = [];
  if (args.trajectory > 0) {
    items.push({
      label: `trajectory screenshots (${String(args.trajectory)} x ${String(PRICING.trajectoryScreenshot)} cr)`,
      credits: perRequest * args.trajectory * PRICING.trajectoryScreenshot,
    });
  }
  if (isHdImage(args.width, args.height)) {
    items.push({
      label: `HD surcharge (${String(args.width)}x${String(args.height)} > 1280x720 strict; current + trajectory shots)`,
      credits: perRequest * (1 + args.trajectory) * PRICING.hdImage,
    });
  }
  if (args.cuaVersion === 'v1') {
    items.push({ label: 'v1 engine surcharge', credits: perRequest * PRICING.v1Engine });
  }
  if (args.promptChars > SYSTEM_PROMPT_FREE_CHARS) {
    items.push({
      label: `system_prompt > ${String(SYSTEM_PROMPT_FREE_CHARS)} chars (exactly ${String(SYSTEM_PROMPT_FREE_CHARS)} is free)`,
      credits: perRequest * PRICING.longSystemPrompt,
    });
  }
  return items;
}

export function predictReport(partial: Partial<InferenceArgs> = {}): CostReport {
  const args = { ...INFERENCE_DEFAULTS, ...partial };
  const items: EstimateLineItem[] = [
    { label: 'POST /predict base', credits: PRICING.predict },
    ...inferenceSurchargeItems(args, 1),
  ];
  const report = finalize('predict (one request)', items);
  // Known-answer guard: the itemization must equal the canonical estimator.
  assertTotal(report, estimatePredictCredits(toCostOptions(args)));
  return report;
}

export interface SessionArgs extends InferenceArgs {
  steps: number;
}

export function sessionReport(partial: Partial<SessionArgs> = {}): CostReport {
  const args = { ...INFERENCE_DEFAULTS, steps: 1, ...partial };
  const items: EstimateLineItem[] = [
    {
      label: 'POST /sessions create (one-time, NO surcharges)',
      credits: estimateSessionCreateCredits(),
    },
    {
      label: `session predicts (${String(args.steps)} x ${String(PRICING.sessionPredict)} cr base)`,
      credits: args.steps * PRICING.sessionPredict,
    },
    ...inferenceSurchargeItems(args, args.steps),
  ];
  const report = finalize(`session (create + ${String(args.steps)} steps)`, items, [
    'session reset/get/list/delete are free',
  ]);
  assertTotal(
    report,
    estimateSessionCreateCredits() +
      args.steps * estimateSessionPredictCredits(toCostOptions(args)),
  );
  return report;
}

export function groundReport(
  partial: Partial<Pick<InferenceArgs, 'width' | 'height'>> = {},
): CostReport {
  const args = { ...INFERENCE_DEFAULTS, ...partial };
  const items: EstimateLineItem[] = [{ label: 'POST /ground base', credits: PRICING.ground }];
  if (isHdImage(args.width, args.height)) {
    items.push({
      label: `HD surcharge (${String(args.width)}x${String(args.height)})`,
      credits: PRICING.hdImage,
    });
  }
  const report = finalize('ground (one request)', items);
  assertTotal(
    report,
    estimateGroundCredits({ screenWidth: args.width, screenHeight: args.height }),
  );
  return report;
}

export interface RunArgs {
  steps: number;
  cuaVersion: CuaVersion;
}

export function runReport(partial: Partial<RunArgs> = {}): CostReport {
  const args: RunArgs = { steps: 10, cuaVersion: 'v3', ...partial };
  const perStep = args.cuaVersion === 'v1' ? PRICING.runStepV1 : PRICING.runStepV3V4;
  const report = finalize(
    `run (${String(args.steps)} agent steps, ${args.cuaVersion})`,
    [
      {
        label: `run steps (${String(args.steps)} x ${String(perStep)} cr on ${args.cuaVersion})`,
        credits: args.steps * perStep,
      },
    ],
    [
      'run steps have NO other surcharges (HD/trajectory/prompt do not apply)',
      'starting a run requires the wallet to cover at least one step',
    ],
  );
  assertTotal(report, estimateRunCredits({ steps: args.steps, cuaVersion: args.cuaVersion }));
  return report;
}

export function workflowReport(
  partial: Partial<{ taskSteps: number; cuaVersion: CuaVersion }> = {},
): CostReport {
  const args = { taskSteps: 10, cuaVersion: 'v3' as CuaVersion, ...partial };
  const perStep = args.cuaVersion === 'v1' ? PRICING.runStepV1 : PRICING.runStepV3V4;
  const report = finalize(
    `workflow run (${String(args.taskSteps)} task-step executions, ${args.cuaVersion})`,
    [
      {
        label: `task steps (${String(args.taskSteps)} x ${String(perStep)} cr)`,
        credits: args.taskSteps * perStep,
      },
      { label: 'control-flow steps (assert/if/loop/parallel/retry/...)', credits: 0 },
    ],
    ['budget_cents caps spend: a breach stops the run with GUARD_EXCEEDED'],
  );
  assertTotal(
    report,
    estimateWorkflowRunCredits({ taskSteps: args.taskSteps, cuaVersion: args.cuaVersion }),
  );
  return report;
}

export interface MachineArgs {
  osType: OsType;
  hours: number;
  stoppedHours: number;
  snapshots: number;
}

export function machineReport(partial: Partial<MachineArgs> = {}): CostReport {
  const args: MachineArgs = {
    osType: 'linux',
    hours: 1,
    stoppedHours: 0,
    snapshots: 0,
    ...partial,
  };
  const runningRate =
    args.osType === 'windows'
      ? PRICING.machineHourly.windowsRunning
      : PRICING.machineHourly.linuxRunning;
  const items: EstimateLineItem[] = [
    {
      label: `running ${args.osType} ${String(args.hours)}h @ ${String(runningRate)} cr/hr (floored per minute)`,
      credits: estimateMachineRuntimeCredits({ osType: args.osType, minutes: args.hours * 60 }),
    },
  ];
  if (args.stoppedHours > 0) {
    items.push({
      label: `stopped ${String(args.stoppedHours)}h @ ${String(PRICING.machineHourly.stopped)} cr/hr (any OS)`,
      credits: estimateMachineRuntimeCredits({
        osType: args.osType,
        minutes: args.stoppedHours * 60,
        status: 'stopped',
      }),
    });
  }
  if (args.snapshots > 0) {
    items.push({
      label: `snapshots (${String(args.snapshots)} x ${String(snapshotCredits())} cr, refunded on failure)`,
      credits: args.snapshots * snapshotCredits(),
    });
  }
  return finalize(`machine (${args.osType})`, items, [
    `provisioning gate: wallet >= ${String(PRICING.provisioningGateCredits)} cr ($0.20) — a gate, not a fee`,
    'runtime is metered per minute, rounded DOWN; per-call ops (actions/terminal/files/browser/screenshots) are free',
    'a wallet-dry machine is STOPPED (suspended_for_billing), never destroyed',
  ]);
}

function assertTotal(report: CostReport, expected: number): void {
  if (report.totalCredits !== expected) {
    throw new Error(
      `internal pricing mismatch for "${report.title}": itemized ${String(report.totalCredits)} ` +
        `!= canonical ${String(expected)} — pricing table drift, fix cost.ts/ex10 together`,
    );
  }
}

// ---------------------------------------------------------------------------
// Plan mode — total a JSON batch
// ---------------------------------------------------------------------------

export type PlanEntry =
  | ({ kind: 'predict' } & Partial<InferenceArgs>)
  | ({ kind: 'session' } & Partial<SessionArgs>)
  | ({ kind: 'ground' } & Partial<Pick<InferenceArgs, 'width' | 'height'>>)
  | ({ kind: 'run' } & Partial<RunArgs>)
  | ({ kind: 'workflow' } & Partial<{ taskSteps: number; cuaVersion: CuaVersion }>)
  | ({ kind: 'machine' } & Partial<MachineArgs>);

export function reportForEntry(entry: PlanEntry): CostReport {
  switch (entry.kind) {
    case 'predict':
      return predictReport(entry);
    case 'session':
      return sessionReport(entry);
    case 'ground':
      return groundReport(entry);
    case 'run':
      return runReport(entry);
    case 'workflow':
      return workflowReport(entry);
    case 'machine':
      return machineReport(entry);
  }
}

const PLAN_KINDS = ['predict', 'session', 'ground', 'run', 'workflow', 'machine'] as const;

/** Parse + total a JSON batch: `[{"kind":"predict","width":1280,...}, ...]`. */
export function planReportFromJson(jsonText: string): CostReport {
  let parsed: unknown;
  try {
    parsed = JSON.parse(jsonText);
  } catch (cause) {
    throw new UsageError(`plan file is not valid JSON: ${String(cause)}`);
  }
  if (!Array.isArray(parsed)) throw new UsageError('plan must be a JSON ARRAY of entries');
  const items: EstimateLineItem[] = parsed.map((entry: unknown, index) => {
    if (typeof entry !== 'object' || entry === null || Array.isArray(entry)) {
      throw new UsageError(`plan[${String(index)}] must be an object with a "kind"`);
    }
    const kind = (entry as Record<string, unknown>).kind;
    if (typeof kind !== 'string' || !(PLAN_KINDS as readonly string[]).includes(kind)) {
      throw new UsageError(
        `plan[${String(index)}].kind ${JSON.stringify(kind)} must be one of: ${PLAN_KINDS.join(', ')}`,
      );
    }
    const report = reportForEntry(entry as PlanEntry);
    return { label: `[${String(index)}] ${report.title}`, credits: report.totalCredits };
  });
  return finalize(`plan (${String(items.length)} entries)`, items);
}

// ---------------------------------------------------------------------------
// CLI parsing (testable)
// ---------------------------------------------------------------------------

export const USAGE = `usage: npx tsx src/examples/ex10-cost-helper.ts <subcommand> [flags]

subcommands and flags:
  predict   [--width N] [--height N] [--trajectory N] [--cua v1|v3|v4] [--prompt-chars N]
  session   [--steps N] + the predict flags
  ground    [--width N] [--height N]
  run       [--steps N] [--cua v1|v3|v4]
  workflow  [--task-steps N] [--cua v1|v3|v4]
  machine   [--os linux|windows | --windows] [--hours H] [--stopped-hours H] [--snapshots N]
  plan      --file <batch.json>   (JSON array of {kind, ...flags-as-fields})`;

function toNumber(name: string, raw: string, opts: { int?: boolean; min?: number }): number {
  const value = Number(raw);
  if (!Number.isFinite(value)) throw new UsageError(`${name} must be a number (got ${raw})`);
  if (opts.int === true && !Number.isInteger(value)) {
    throw new UsageError(`${name} must be an integer (got ${raw})`);
  }
  if (opts.min !== undefined && value < opts.min) {
    throw new UsageError(`${name} must be >= ${String(opts.min)} (got ${raw})`);
  }
  return value;
}

function toCua(raw: string): CuaVersion {
  if (raw !== 'v1' && raw !== 'v3' && raw !== 'v4') {
    throw new UsageError(`--cua must be v1, v3 or v4 (got ${raw})`);
  }
  return raw;
}

function toCostOptions(args: InferenceArgs): {
  cuaVersion: CuaVersion;
  screenWidth: number;
  screenHeight: number;
  trajectoryScreenshots: number;
  systemPromptChars: number;
} {
  return {
    cuaVersion: args.cuaVersion,
    screenWidth: args.width,
    screenHeight: args.height,
    trajectoryScreenshots: args.trajectory,
    systemPromptChars: args.promptChars,
  };
}

/** Parse argv (subcommand first) into a finished report. Throws UsageError. */
export function buildReportFromArgs(argv: readonly string[]): CostReport {
  const [subcommand, ...rest] = argv;
  if (subcommand === undefined) throw new UsageError('missing subcommand');

  const { values } = parseArgs({
    args: [...rest],
    strict: true,
    allowPositionals: false,
    options: {
      width: { type: 'string' },
      height: { type: 'string' },
      trajectory: { type: 'string' },
      cua: { type: 'string' },
      'prompt-chars': { type: 'string' },
      steps: { type: 'string' },
      'task-steps': { type: 'string' },
      hours: { type: 'string' },
      'stopped-hours': { type: 'string' },
      snapshots: { type: 'string' },
      os: { type: 'string' },
      windows: { type: 'boolean' },
      file: { type: 'string' },
    },
  });

  const inference = (): Partial<InferenceArgs> => ({
    ...(values.width === undefined
      ? {}
      : { width: toNumber('--width', values.width, { int: true, min: 1 }) }),
    ...(values.height === undefined
      ? {}
      : { height: toNumber('--height', values.height, { int: true, min: 1 }) }),
    ...(values.trajectory === undefined
      ? {}
      : { trajectory: toNumber('--trajectory', values.trajectory, { int: true, min: 0 }) }),
    ...(values.cua === undefined ? {} : { cuaVersion: toCua(values.cua) }),
    ...(values['prompt-chars'] === undefined
      ? {}
      : { promptChars: toNumber('--prompt-chars', values['prompt-chars'], { int: true, min: 0 }) }),
  });

  switch (subcommand) {
    case 'predict':
      return predictReport(inference());
    case 'session':
      return sessionReport({
        ...inference(),
        ...(values.steps === undefined
          ? {}
          : { steps: toNumber('--steps', values.steps, { int: true, min: 0 }) }),
      });
    case 'ground':
      return groundReport(inference());
    case 'run':
      return runReport({
        ...(values.steps === undefined
          ? {}
          : { steps: toNumber('--steps', values.steps, { int: true, min: 1 }) }),
        ...(values.cua === undefined ? {} : { cuaVersion: toCua(values.cua) }),
      });
    case 'workflow':
      return workflowReport({
        ...(values['task-steps'] === undefined
          ? {}
          : { taskSteps: toNumber('--task-steps', values['task-steps'], { int: true, min: 0 }) }),
        ...(values.cua === undefined ? {} : { cuaVersion: toCua(values.cua) }),
      });
    case 'machine': {
      const osType: OsType | undefined =
        values.windows === true
          ? 'windows'
          : values.os === undefined
            ? undefined
            : parseOs(values.os);
      return machineReport({
        ...(osType === undefined ? {} : { osType }),
        ...(values.hours === undefined
          ? {}
          : { hours: toNumber('--hours', values.hours, { min: 0 }) }),
        ...(values['stopped-hours'] === undefined
          ? {}
          : { stoppedHours: toNumber('--stopped-hours', values['stopped-hours'], { min: 0 }) }),
        ...(values.snapshots === undefined
          ? {}
          : { snapshots: toNumber('--snapshots', values.snapshots, { int: true, min: 0 }) }),
      });
    }
    case 'plan': {
      if (values.file === undefined) throw new UsageError('plan requires --file <batch.json>');
      return planReportFromJson(readFileSync(values.file, 'utf8'));
    }
    default:
      throw new UsageError(`unknown subcommand "${subcommand}"`);
  }
}

function parseOs(raw: string): OsType {
  if (raw !== 'linux' && raw !== 'windows') {
    throw new UsageError(`--os must be linux or windows (got ${raw})`);
  }
  return raw;
}

export function renderReport(report: CostReport, sandbox: boolean): string {
  const lines = [
    report.title,
    formatEstimate(report.items, { sandbox }),
    ...report.notes.map((note) => `  note: ${note}`),
  ];
  return lines.join('\n');
}

// ---------------------------------------------------------------------------
// Thin CLI
// ---------------------------------------------------------------------------

function isMain(): boolean {
  const entry = process.argv[1];
  return entry !== undefined && path.resolve(entry) === fileURLToPath(import.meta.url);
}

export function main(argv: string[] = process.argv.slice(2)): void {
  const print = (line: string): void => void process.stdout.write(`${line}\n`);
  try {
    const report = buildReportFromArgs(argv);
    print(renderReport(report, isSandboxKey()));
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  }
}

if (isMain()) main();
