/**
 * Cost estimator implementing the full documented pricing table
 * (1 credit = 1 cent = $0.01 exactly). Sandbox keys (`sk-coasty-test-*`)
 * never bill — examples label those estimates "$0 (sandbox)".
 */
import { type CuaVersion, type MachineStatus, type OsType } from './types.js';

/** Full per-item pricing table, in credits. */
export const PRICING = {
  predict: 5,
  sessionCreate: 10, // one-time, no surcharges
  sessionPredict: 4,
  ground: 3,
  parse: 0,
  /** Surcharge: per trajectory screenshot attached. */
  trajectoryScreenshot: 2,
  /** Surcharge: per HD image (strictly width > 1280 OR height > 720). */
  hdImage: 1,
  /** Surcharge: per request on the v1 engine. */
  v1Engine: 3,
  /** Surcharge: system_prompt strictly longer than 500 chars (exactly 500 is free). */
  longSystemPrompt: 1,
  /** Run / workflow-task step on v3/v4 (no other surcharges apply to run steps). */
  runStepV3V4: 5,
  /** Run / workflow-task step on v1 (5 base + 3 engine surcharge). */
  runStepV1: 8,
  snapshot: 1,
  machineHourly: {
    linuxRunning: 5,
    windowsRunning: 9,
    /** Stopped / suspended (any OS): storage-only rate. */
    stopped: 1,
    /** Creating / error / terminated: free. */
    unbilled: 0,
  },
  /** Provisioning gate (NOT a fee): wallet must hold >= 20 credits. */
  provisioningGateCredits: 20,
} as const;

export const HD_WIDTH_THRESHOLD = 1280;
export const HD_HEIGHT_THRESHOLD = 720;
/** System prompts strictly longer than this add 1 credit. */
export const SYSTEM_PROMPT_FREE_CHARS = 500;

/** HD is STRICT: exactly 1280x720 is NOT HD. */
export function isHdImage(width: number, height: number): boolean {
  return width > HD_WIDTH_THRESHOLD || height > HD_HEIGHT_THRESHOLD;
}

export interface InferenceCostOptions {
  cuaVersion?: CuaVersion;
  /** Width of the screenshot you SEND (defaults mirror the API: 1920x1080 — note that IS HD). */
  screenWidth?: number;
  screenHeight?: number;
  /** Trajectory screenshots attached (assumed same dimensions as the current shot). */
  trajectoryScreenshots?: number;
  /** Length of `system_prompt` in characters (exactly 500 is still free). */
  systemPromptChars?: number;
}

function inferenceSurcharges(options: InferenceCostOptions): number {
  const cuaVersion = options.cuaVersion ?? 'v3';
  const width = options.screenWidth ?? 1920;
  const height = options.screenHeight ?? 1080;
  const trajectory = options.trajectoryScreenshots ?? 0;
  const promptChars = options.systemPromptChars ?? 0;

  let credits = trajectory * PRICING.trajectoryScreenshot;
  if (isHdImage(width, height)) {
    // The HD fee applies to the current screenshot AND each trajectory shot.
    credits += PRICING.hdImage * (1 + trajectory);
  }
  if (cuaVersion === 'v1') credits += PRICING.v1Engine;
  if (promptChars > SYSTEM_PROMPT_FREE_CHARS) credits += PRICING.longSystemPrompt;
  return credits;
}

/** POST /v1/predict: 5 credits + surcharges. */
export function estimatePredictCredits(options: InferenceCostOptions = {}): number {
  return PRICING.predict + inferenceSurcharges(options);
}

/** POST /v1/sessions: 10 credits one-time, NO surcharges. */
export function estimateSessionCreateCredits(): number {
  return PRICING.sessionCreate;
}

/** POST /v1/sessions/{id}/predict: 4 credits + the same surcharges as /predict. */
export function estimateSessionPredictCredits(options: InferenceCostOptions = {}): number {
  return PRICING.sessionPredict + inferenceSurcharges(options);
}

/** POST /v1/ground: 3 credits, +1 if the screenshot is HD. */
export function estimateGroundCredits(
  options: { screenWidth?: number; screenHeight?: number } = {},
): number {
  const width = options.screenWidth ?? 1920;
  const height = options.screenHeight ?? 1080;
  return PRICING.ground + (isHdImage(width, height) ? PRICING.hdImage : 0);
}

/** POST /v1/parse is free. */
export function estimateParseCredits(): number {
  return PRICING.parse;
}

/** Per-step run price: 5 credits on v3/v4, 8 on v1. No other surcharges on run steps. */
export function runStepCredits(cuaVersion: CuaVersion = 'v3'): number {
  return cuaVersion === 'v1' ? PRICING.runStepV1 : PRICING.runStepV3V4;
}

/** Estimate a whole run: steps × per-step rate. */
export function estimateRunCredits(options: { steps: number; cuaVersion?: CuaVersion }): number {
  return options.steps * runStepCredits(options.cuaVersion ?? 'v3');
}

/**
 * Estimate a workflow run. Only `task` steps bill (at the run-step rate ×
 * their expected agent steps); control-flow steps are free.
 */
export function estimateWorkflowRunCredits(options: {
  /** Total agent steps expected across all `task` steps. */
  taskSteps: number;
  cuaVersion?: CuaVersion;
}): number {
  return options.taskSteps * runStepCredits(options.cuaVersion ?? 'v3');
}

const RUNNING_STATES: ReadonlySet<MachineStatus> = new Set([
  'running',
  'starting',
  'stopping',
  'restarting',
]);
const STOPPED_STATES: ReadonlySet<MachineStatus> = new Set(['stopped', 'suspended_for_billing']);

/**
 * Hourly runtime rate by OS and state. Running (incl. starting/stopping/
 * restarting): Linux 5, Windows 9. Stopped/suspended (any OS): 1.
 * Creating/error/terminated: 0.
 */
export function machineHourlyCredits(osType: OsType, status: MachineStatus): number {
  if (RUNNING_STATES.has(status)) {
    return osType === 'windows'
      ? PRICING.machineHourly.windowsRunning
      : PRICING.machineHourly.linuxRunning;
  }
  if (STOPPED_STATES.has(status)) return PRICING.machineHourly.stopped;
  return PRICING.machineHourly.unbilled;
}

/**
 * Runtime is metered per minute and billed in whole credits, ROUNDED DOWN in
 * your favor (partial credits are never billed).
 */
export function estimateMachineRuntimeCredits(options: {
  osType: OsType;
  minutes: number;
  status?: MachineStatus;
}): number {
  const hourly = machineHourlyCredits(options.osType, options.status ?? 'running');
  return Math.floor((hourly * options.minutes) / 60);
}

/** POST /v1/machines/{id}/snapshot: 1 credit one-time (refunded on failure). */
export function snapshotCredits(): number {
  return PRICING.snapshot;
}

/** 1 credit = 1 cent = $0.01, exactly. */
export function creditsToUsd(credits: number): number {
  return credits / 100;
}

export function formatUsd(credits: number): string {
  return `$${creditsToUsd(credits).toFixed(2)}`;
}

export interface EstimateLineItem {
  label: string;
  credits: number;
}

/**
 * Render an itemized cost estimate for examples to print before spending.
 * When `sandbox` is true the total is labelled `$0.00 (sandbox key — never bills)`.
 */
export function formatEstimate(
  items: readonly EstimateLineItem[],
  options: { sandbox?: boolean } = {},
): string {
  const total = items.reduce((sum, item) => sum + item.credits, 0);
  const labelWidth = Math.max(5, ...items.map((item) => item.label.length));
  const lines = items.map(
    (item) =>
      `  ${item.label.padEnd(labelWidth)}  ${String(item.credits).padStart(6)} cr  (${formatUsd(item.credits)})`,
  );
  const totalText =
    options.sandbox === true
      ? `  ${'TOTAL'.padEnd(labelWidth)}  ${String(total).padStart(6)} cr  ($0.00 — sandbox key, never bills)`
      : `  ${'TOTAL'.padEnd(labelWidth)}  ${String(total).padStart(6)} cr  (${formatUsd(total)})`;
  return ['Estimated cost:', ...lines, totalText].join('\n');
}
