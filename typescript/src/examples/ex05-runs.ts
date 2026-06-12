/**
 * Example 05 — Task runs: create (v3 / v4), poll OR stream, resume, billing.
 *
 * Purpose: hand a whole task to a Coasty-managed agent on one of YOUR
 * machines and supervise it. Demonstrates:
 *
 *   - POST /v1/runs with an Idempotency-Key (retried creates can never
 *     double-start a run; replays carry X-Coasty-Idempotent-Replay: true);
 *   - cua_version v3 (default) and v4 behind `--v4` — NOTE: v4 (autonomous
 *     mode + verifier) requires the pro+ tier; other tiers get
 *     400 FEATURE_NOT_AVAILABLE;
 *   - default mode: GET /v1/runs/{id} polling until a terminal status
 *     (succeeded | failed | cancelled | timed_out);
 *   - `--events` mode: GET /v1/runs/{id}/events SSE consumption with
 *     AUTOMATIC Last-Event-ID reconnect (src/coasty/sse.ts replays after the
 *     last seen seq — no loss, no duplicates);
 *   - the awaiting_human -> POST /v1/runs/{id}/resume flow in both modes;
 *   - billing events from the stream + the final result with cost_cents.
 *
 * Endpoints: POST /v1/runs, GET /v1/runs/{id}, GET /v1/runs/{id}/events,
 * POST /v1/runs/{id}/resume.
 *
 * Estimated cost (computed at runtime via src/coasty/cost.ts and printed by
 * the spend gate): estimateRunCredits({steps: maxSteps, cuaVersion}) —
 * 5 cr/step on v3/v4, 8 cr/step on v1; run steps have NO other surcharges.
 * Defaults: 10 x 5 = 50 cr = $0.50 ceiling (only completed steps bill).
 * Sandbox keys never bill — the gate prints "$0 (sandbox)".
 *
 * Run it:
 *   npx tsx src/examples/ex05-runs.ts --machine mch_test_123 \
 *     --task "Open the dashboard and export the report" --confirm
 *   npx tsx src/examples/ex05-runs.ts --machine mch_test_123 --events --v4 --confirm
 *
 * Env config: COASTY_API_KEY (required), COASTY_BASE_URL, COASTY_MACHINE_ID,
 * EX05_TASK, EX05_MAX_STEPS, COASTY_CONFIRM_SPEND=1 (instead of --confirm).
 */
import { randomUUID } from 'node:crypto';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { CoastyClient } from '../coasty/client.js';
import {
  estimateRunCredits,
  formatEstimate,
  formatUsd,
  runStepCredits,
  type EstimateLineItem,
} from '../coasty/cost.js';
import { getApiKey, isSandboxKey, spendConfirmed } from '../coasty/env.js';
import { CoastyError } from '../coasty/errors.js';
import { defaultSleep, type SleepFn } from '../coasty/sse.js';
import {
  isTerminalRunStatus,
  type CreateRunRequest,
  type CuaVersion,
  type ResumeRunRequest,
  type Run,
  type RunEvent,
} from '../coasty/types.js';

export type PrintFn = (line: string) => void;

export const stdoutPrint: PrintFn = (line) => {
  process.stdout.write(`${line}\n`);
};

export const V4_TIER_NOTE =
  'Note: cua_version v4 (autonomous mode + verifier) requires the pro+ tier — ' +
  'other tiers get 400 FEATURE_NOT_AVAILABLE. v3 is the default for all tiers.';

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
// Create
// ---------------------------------------------------------------------------

export interface BuildRunRequestOptions {
  machineId: string;
  task: string;
  /** 'v3' (default, all tiers) or 'v4' (pro+ only — see V4_TIER_NOTE). */
  cuaVersion?: CuaVersion;
  maxSteps?: number;
}

export function buildCreateRunRequest(options: BuildRunRequestOptions): CreateRunRequest {
  return {
    machine_id: options.machineId,
    task: options.task,
    cua_version: options.cuaVersion ?? 'v3',
    max_steps: options.maxSteps ?? 10,
    // pause (default) is what makes the awaiting_human -> resume flow below
    // possible; 'fail' / 'cancel' end the run instead.
    on_awaiting_human: 'pause',
  };
}

export interface CreateRunOptions {
  client: CoastyClient;
  request: CreateRunRequest;
  /** Default: a fresh `ex05-run-<uuid>` key. */
  idempotencyKey?: string;
  print?: PrintFn;
}

/** POST /v1/runs with an Idempotency-Key; surfaces idempotent replays. */
export async function createRun(options: CreateRunOptions): Promise<Run> {
  const print = options.print ?? stdoutPrint;
  const idempotencyKey = options.idempotencyKey ?? `ex05-run-${randomUUID()}`;
  const { data, meta } = await options.client.runs.create(options.request, { idempotencyKey });
  print(
    `created run ${data.id} (status=${data.status}, cua_version=${data.cua_version}, ` +
      `max_steps=${String(data.max_steps)}, request_id=${data.request_id ?? meta.requestId ?? 'n/a'})`,
  );
  if (meta.idempotentReplay) {
    print('note: served from the idempotency cache (X-Coasty-Idempotent-Replay: true).');
  }
  return data;
}

// ---------------------------------------------------------------------------
// Poll mode
// ---------------------------------------------------------------------------

/**
 * Decide what to do when a run pauses for a human. Return a resume body to
 * POST /runs/{id}/resume, or null to keep waiting (e.g. a real operator is
 * handling it out-of-band).
 */
export type AwaitingHumanHandler = (run: Run) => Promise<ResumeRunRequest | null>;

export interface PollRunOptions {
  client: CoastyClient;
  runId: string;
  /** Delay between GETs (default 2000 ms). */
  intervalMs?: number;
  /** Injectable for tests (default: real setTimeout sleep). */
  sleep?: SleepFn;
  onAwaitingHuman?: AwaitingHumanHandler;
  /** Called on every status TRANSITION (not every poll). */
  onUpdate?: (run: Run) => void;
  /** Safety valve so a stuck run cannot poll forever (default 10000). */
  maxPolls?: number;
  print?: PrintFn;
}

/** GET /v1/runs/{id} until a terminal status; resumes awaiting_human runs. */
export async function pollRunUntilTerminal(options: PollRunOptions): Promise<Run> {
  const intervalMs = options.intervalMs ?? 2000;
  const sleep = options.sleep ?? defaultSleep;
  const maxPolls = options.maxPolls ?? 10_000;
  const print = options.print ?? stdoutPrint;

  let lastStatus: string | null = null;
  for (let poll = 1; poll <= maxPolls; poll += 1) {
    const { data: run } = await options.client.runs.get(options.runId);
    if (run.status !== lastStatus) {
      lastStatus = run.status;
      print(`run ${run.id}: ${run.status} (steps_completed=${String(run.steps_completed)})`);
      options.onUpdate?.(run);
    }
    if (isTerminalRunStatus(run.status)) return run;
    if (run.status === 'awaiting_human' && options.onAwaitingHuman !== undefined) {
      print(`awaiting_human: ${run.awaiting_human_reason ?? 'no reason given'}`);
      const resume = await options.onAwaitingHuman(run);
      if (resume !== null) {
        const { data: resumed } = await options.client.runs.resume(options.runId, resume);
        print(`resumed run ${resumed.id} (status=${resumed.status}).`);
        lastStatus = resumed.status;
      }
    }
    await sleep(intervalMs);
  }
  throw new Error(
    `run ${options.runId} did not reach a terminal status after ${String(maxPolls)} poll(s)`,
  );
}

// ---------------------------------------------------------------------------
// --events mode (SSE with automatic Last-Event-ID reconnect)
// ---------------------------------------------------------------------------

function extractReason(data: unknown): string | null {
  if (typeof data === 'object' && data !== null && 'reason' in data) {
    const reason = (data as Record<string, unknown>).reason;
    return typeof reason === 'string' ? reason : null;
  }
  return null;
}

export interface WatchRunOptions {
  client: CoastyClient;
  runId: string;
  /** Resume cursor for a previous partial watch (sent as Last-Event-ID). */
  lastEventId?: string | number;
  /** Max automatic reconnects after stream drops (default 5). */
  maxReconnects?: number;
  onAwaitingHuman?: AwaitingHumanHandler;
  /** Observe every event (tests use this; printing already happens). */
  onEvent?: (event: RunEvent) => void;
  print?: PrintFn;
}

export interface WatchRunResult {
  /** Final run object (GET after the stream's `done`). */
  run: Run;
  /** Every seq observed, in order — no loss, no duplicates across reconnects. */
  seqs: number[];
  /** Payloads of `billing` events, in order. */
  billingEvents: unknown[];
}

/**
 * Consume GET /v1/runs/{id}/events until the `done` event. The underlying
 * client stream (src/coasty/sse.ts) reconnects automatically on drops,
 * carrying `Last-Event-ID: <last seen seq>` so the durable event log is
 * replayed exactly-once. awaiting_human events trigger the resume flow
 * inline; billing events are collected and printed.
 */
export async function watchRunEvents(options: WatchRunOptions): Promise<WatchRunResult> {
  const print = options.print ?? stdoutPrint;
  const seqs: number[] = [];
  const billingEvents: unknown[] = [];

  const events = options.client.runs.events(options.runId, {
    lastEventId: options.lastEventId,
    maxReconnects: options.maxReconnects,
  });
  for await (const event of events) {
    if (event.seq >= 0) seqs.push(event.seq);
    options.onEvent?.(event);
    switch (event.type) {
      case 'billing':
        billingEvents.push(event.data);
        print(`[seq ${String(event.seq)}] billing ${JSON.stringify(event.data)}`);
        break;
      case 'awaiting_human': {
        const reason = extractReason(event.data);
        print(`[seq ${String(event.seq)}] awaiting_human: ${reason ?? 'no reason given'}`);
        if (options.onAwaitingHuman !== undefined) {
          // The stream stays open while we resume; subsequent events
          // (`resumed`, more steps, `done`) arrive on the same stream.
          const { data: current } = await options.client.runs.get(options.runId);
          const resume = await options.onAwaitingHuman(current);
          if (resume !== null) {
            const { data: resumed } = await options.client.runs.resume(options.runId, resume);
            print(`resumed run ${resumed.id} (status=${resumed.status}).`);
          }
        }
        break;
      }
      case 'error':
        print(`[seq ${String(event.seq)}] error ${JSON.stringify(event.data)}`);
        break;
      case 'done':
        print(`[seq ${String(event.seq)}] done — stream complete.`);
        break;
      default:
        print(`[seq ${String(event.seq)}] ${event.type} ${JSON.stringify(event.data)}`);
        break;
    }
  }

  const { data: run } = await options.client.runs.get(options.runId);
  return { run, seqs, billingEvents };
}

// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------

export function formatRunSummary(run: Run): string {
  const lines = [
    `run ${run.id}: ${run.status}`,
    `  steps_completed: ${String(run.steps_completed)} / ${String(run.max_steps)}`,
    `  credits_charged: ${String(run.credits_charged)} cr`,
    `  cost_cents:      ${String(run.cost_cents)} (${formatUsd(run.cost_cents)})`,
  ];
  if (run.result !== null) {
    lines.push(`  result: passed=${String(run.result.passed)} — ${run.result.summary}`);
    if (run.result.verdict !== undefined && run.result.verdict !== null) {
      lines.push(`  verdict: ${run.result.verdict}`);
    }
  }
  if (run.error !== null) lines.push(`  error: ${run.error.code}: ${run.error.message}`);
  if (run.awaiting_human_reason !== null) {
    lines.push(`  awaiting_human_reason: ${run.awaiting_human_reason}`);
  }
  lines.push(`  request_id: ${run.request_id ?? 'n/a'}`);
  return lines.join('\n');
}

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

export interface Ex05Config {
  machineId: string;
  task: string;
  cuaVersion: CuaVersion;
  maxSteps: number;
  events: boolean;
  confirm: boolean;
  resumeNote: string;
  pollIntervalMs: number;
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
): Ex05Config {
  const config: Ex05Config = {
    machineId: env.COASTY_MACHINE_ID ?? '',
    task: env.EX05_TASK ?? 'Open the dashboard and export this month’s report as CSV.',
    cuaVersion: 'v3',
    maxSteps: intOption('EX05_MAX_STEPS', env.EX05_MAX_STEPS, 10),
    events: false,
    confirm: false,
    resumeNote: 'Resolved by the cookbook operator — please continue.',
    pollIntervalMs: 2000,
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
      case '--machine':
      case '-m':
        config.machineId = next();
        break;
      case '--task':
      case '-t':
        config.task = next();
        break;
      case '--v4':
        config.cuaVersion = 'v4';
        break;
      case '--max-steps':
        config.maxSteps = intOption('--max-steps', next(), config.maxSteps);
        break;
      case '--events':
        config.events = true;
        break;
      case '--confirm':
        config.confirm = true;
        break;
      case '--resume-note':
        config.resumeNote = next();
        break;
      case '--poll-interval-ms':
        config.pollIntervalMs = intOption('--poll-interval-ms', next(), config.pollIntervalMs);
        break;
      default:
        throw new Error(
          `unknown argument: ${String(arg)} (expected --machine, --task, --v4, ` +
            '--max-steps, --events, --confirm, --resume-note, --poll-interval-ms)',
        );
    }
  }
  if (config.machineId === '') {
    throw new Error(
      'a machine id is required: pass --machine <id> or set COASTY_MACHINE_ID. ' +
        'Sandbox keys get an instant free VM from POST /v1/machines (ids look like mch_test_*).',
    );
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
  if (config.cuaVersion === 'v4') stdoutPrint(V4_TIER_NOTE);

  const approved = ensureSpendApproved({
    apiKey,
    confirmFlag: config.confirm,
    items: [
      {
        label:
          `run: <= ${String(config.maxSteps)} steps @ ${config.cuaVersion} ` +
          `(${String(runStepCredits(config.cuaVersion))} cr/step, no other surcharges)`,
        credits: estimateRunCredits({ steps: config.maxSteps, cuaVersion: config.cuaVersion }),
      },
    ],
  });
  if (!approved) return 1;

  const client = new CoastyClient({ apiKey });
  const run = await createRun({
    client,
    request: buildCreateRunRequest({
      machineId: config.machineId,
      task: config.task,
      cuaVersion: config.cuaVersion,
      maxSteps: config.maxSteps,
    }),
  });

  // Demo policy: auto-resume paused runs once the "human" (this CLI) leaves a
  // note. A real operator UI would inspect run.awaiting_human_reason first.
  const onAwaitingHuman: AwaitingHumanHandler = (paused) => {
    stdoutPrint(`human needed on run ${paused.id}: ${paused.awaiting_human_reason ?? 'unknown'}`);
    return Promise.resolve({ note: config.resumeNote });
  };

  let finalRun: Run;
  if (config.events) {
    const watched = await watchRunEvents({ client, runId: run.id, onAwaitingHuman });
    stdoutPrint(`observed ${String(watched.seqs.length)} event(s); billing events:`);
    for (const billing of watched.billingEvents) stdoutPrint(`  ${JSON.stringify(billing)}`);
    finalRun = watched.run;
  } else {
    finalRun = await pollRunUntilTerminal({
      client,
      runId: run.id,
      intervalMs: config.pollIntervalMs,
      onAwaitingHuman,
    });
  }

  stdoutPrint(formatRunSummary(finalRun));
  return finalRun.status === 'succeeded' ? 0 : 1;
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
