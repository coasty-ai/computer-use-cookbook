/**
 * Example 08 — Machines lifecycle, cost-aware.
 *
 * provision -> poll until running -> screenshot (saved to disk) -> one
 * low-level action -> a batched action sequence -> terminal command -> file
 * write/read -> browser navigate -> snapshot -> STOP + TERMINATE in finally.
 *
 * Cost model (computed for the defaults: Linux, 30-minute TTL):
 *   - provisioning gate: wallet must hold >= 20 credits ($0.20) — a GATE, not
 *     a fee; sandbox keys skip it entirely (instant free mch_test_* VM);
 *   - runtime: Linux 5 cr/hr running (Windows 9 cr/hr), 1 cr/hr stopped,
 *     metered per minute and ROUNDED DOWN -> a full 30-minute session costs
 *     floor(5 x 30 / 60) = 2 credits ($0.02);
 *   - snapshot: 1 credit ($0.01) one-time, refunded on failure;
 *   - actions / batch / terminal / files / browser / screenshots / stop:
 *     0 credits per call.
 *   TOTAL (typical run, well under TTL): <= 3 credits ($0.03); $0.00 on a
 *   sandbox key. A wallet-dry machine is STOPPED, never destroyed.
 *
 * The `ttl_minutes` guard is mandatory in this example: every provision sets
 * an auto-terminate TTL (5-10080 min) so a crashed script can never leak a
 * billing machine.
 *
 * Run it:
 *   npx tsx src/examples/ex08-machines.ts [--os linux|windows] [--ttl 30] \
 *     [--out ./ex08-screenshot.png] [--confirm]
 */
import { writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { CoastyClient } from '../coasty/client.js';
import {
  PRICING,
  creditsToUsd,
  estimateMachineRuntimeCredits,
  formatEstimate,
  machineHourlyCredits,
  snapshotCredits,
} from '../coasty/cost.js';
import { isSandboxKey, spendConfirmed } from '../coasty/env.js';
import { CoastyError } from '../coasty/errors.js';
import { defaultSleep, type SleepFn } from '../coasty/sse.js';
import { type Machine, type OsType } from '../coasty/types.js';

// ---------------------------------------------------------------------------
// Guards
// ---------------------------------------------------------------------------

export const TTL_MIN_MINUTES = 5;
export const TTL_MAX_MINUTES = 10080;
export const DEFAULT_TTL_MINUTES = 30;

/** Thrown when a live (billable) key is used without explicit confirmation. */
export class SpendNotConfirmedError extends Error {
  constructor(osType: OsType) {
    const hourly = machineHourlyCredits(osType, 'running');
    super(
      `Refusing to provision a live ${osType} machine (${String(hourly)} cr/hr running, ` +
        `gate: wallet >= ${String(PRICING.provisioningGateCredits)} cr) without confirmation. ` +
        'Pass --confirm (or set COASTY_CONFIRM_SPEND=1), or use an sk-coasty-test-* sandbox key.',
    );
    this.name = 'SpendNotConfirmedError';
  }
}

/** Thrown when the mandatory auto-terminate TTL is missing or out of range. */
export class TtlGuardError extends Error {
  constructor(ttlMinutes: number) {
    super(
      `ttl_minutes must be an integer between ${String(TTL_MIN_MINUTES)} and ` +
        `${String(TTL_MAX_MINUTES)} (got ${String(ttlMinutes)}). This example refuses to ` +
        'provision a machine without an auto-terminate TTL — a crashed script must never ' +
        'leak a billing VM.',
    );
    this.name = 'TtlGuardError';
  }
}

/** Thrown when a per-call machine op reports success=false (never silent). */
export class MachineOpFailedError extends Error {
  readonly requestId: string | null;

  constructor(op: string, detail: string | null, requestId: string | null) {
    super(
      `machine op "${op}" failed: ${detail ?? 'no error detail'} (request_id: ${requestId ?? 'n/a'})`,
    );
    this.name = 'MachineOpFailedError';
    this.requestId = requestId;
  }
}

export function validateTtlMinutes(ttlMinutes: number): void {
  if (
    !Number.isInteger(ttlMinutes) ||
    ttlMinutes < TTL_MIN_MINUTES ||
    ttlMinutes > TTL_MAX_MINUTES
  ) {
    throw new TtlGuardError(ttlMinutes);
  }
}

// ---------------------------------------------------------------------------
// Testable core
// ---------------------------------------------------------------------------

export interface MachineExampleOptions {
  /** True when the API key is a sandbox key (instant free mch_test_* VM). */
  sandbox: boolean;
  /** True when the user explicitly confirmed live spend. */
  confirmedSpend: boolean;
  /** Where to save the screenshot PNG. */
  screenshotPath: string;
  displayName?: string;
  osType?: OsType;
  /** Auto-terminate TTL (5-10080); ALWAYS set. Default 30. */
  ttlMinutes?: number;
  /** Provision/snapshot retries become safe with this. */
  idempotencyKey?: string;
  pollIntervalMs?: number;
  maxPolls?: number;
  sleep?: SleepFn;
  /** Millisecond clock for the runtime cost readout (injectable for tests). */
  now?: () => number;
  logger?: (line: string) => void;
}

export interface MachineExampleReport {
  machine: Machine;
  /** Ordered op log — tests assert the lifecycle order against this. */
  operations: string[];
  screenshotPath: string;
  screenshotBytes: number;
  terminalResult: Record<string, unknown>;
  fileContent: Record<string, unknown>;
  browserResult: Record<string, unknown>;
  snapshotId: string;
  /** Sum of X-Credits-Charged across all calls (0 on sandbox keys). */
  creditsObserved: number;
  /** cost.ts estimate for the elapsed runtime + the snapshot. */
  estimatedCredits: number;
  elapsedMinutes: number;
}

export const REMOTE_FILE_PATH = '/tmp/coasty-cookbook-ex08.txt';
export const REMOTE_FILE_CONTENT = 'hello from the coasty cookbook (ex08)';

async function pollUntilRunning(
  client: CoastyClient,
  machineId: string,
  options: { sleep: SleepFn; intervalMs: number; maxPolls: number },
): Promise<Machine> {
  for (let poll = 0; poll < options.maxPolls; poll += 1) {
    const { data: machine } = await client.machines.get(machineId);
    if (machine.status === 'running') return machine;
    if (machine.status === 'error' || machine.status === 'terminated') {
      throw new MachineOpFailedError('poll', `machine entered status "${machine.status}"`, null);
    }
    await options.sleep(options.intervalMs);
  }
  throw new MachineOpFailedError(
    'poll',
    `machine not running after ${String(options.maxPolls)} polls`,
    null,
  );
}

/**
 * The full lifecycle. The machine is ALWAYS stopped + terminated in `finally`
 * once provisioned — even when a mid-lifecycle call throws.
 */
export async function runMachineLifecycle(
  client: CoastyClient,
  options: MachineExampleOptions,
): Promise<MachineExampleReport> {
  const log = options.logger ?? ((line: string): void => void process.stdout.write(`${line}\n`));
  const osType = options.osType ?? 'linux';
  const ttlMinutes = options.ttlMinutes ?? DEFAULT_TTL_MINUTES;
  const sleep = options.sleep ?? defaultSleep;
  const now = options.now ?? ((): number => Date.now());

  // Guards FIRST — nothing is provisioned unless both pass.
  validateTtlMinutes(ttlMinutes);
  if (!options.sandbox && !options.confirmedSpend) throw new SpendNotConfirmedError(osType);

  const hourly = machineHourlyCredits(osType, 'running');
  log(
    options.sandbox
      ? 'sandbox key: instant free mch_test_* VM, no wallet gate, $0.00'
      : `live key: gate wallet >= ${String(PRICING.provisioningGateCredits)} cr ($0.20, not a fee); ` +
          `${osType} runs at ${String(hourly)} cr/hr (${String(PRICING.machineHourly.stopped)} cr/hr stopped)`,
  );

  const operations: string[] = [];
  let creditsObserved = 0;
  const track = <T extends { meta: { creditsCharged: number | null } }>(result: T): T => {
    creditsObserved += result.meta.creditsCharged ?? 0;
    return result;
  };

  const startedAtMs = now();

  // 1. Provision with the auto-terminate TTL.
  const provisioned = track(
    await client.machines.provision(
      {
        display_name: options.displayName ?? 'cookbook-ex08',
        os_type: osType,
        desktop_enabled: true,
        ttl_minutes: ttlMinutes,
      },
      options.idempotencyKey === undefined ? {} : { idempotencyKey: options.idempotencyKey },
    ),
  );
  operations.push('provision');
  let machine = provisioned.data.machine;
  log(`provisioned ${machine.id} (status ${machine.status}, ttl ${String(ttlMinutes)} min)`);

  try {
    // 2. Poll until running (sandbox machines are usually running instantly).
    if (machine.status !== 'running') {
      machine = await pollUntilRunning(client, machine.id, {
        sleep,
        intervalMs: options.pollIntervalMs ?? 2000,
        maxPolls: options.maxPolls ?? 60,
      });
    } else {
      // Still record one poll so the lifecycle order is deterministic.
      machine = track(await client.machines.get(machine.id)).data;
    }
    operations.push('poll');
    log(`machine ${machine.id} is running`);

    // 3. Screenshot -> save to disk (free per call).
    const shot = track(await client.machines.screenshot(machine.id));
    const imageBytes = Buffer.from(shot.data.image_b64, 'base64');
    await writeFile(options.screenshotPath, imageBytes);
    operations.push('screenshot');
    log(
      `saved screenshot (${String(shot.data.width)}x${String(shot.data.height)}, ` +
        `${String(imageBytes.length)} bytes) -> ${options.screenshotPath}`,
    );

    // 4. One low-level action (free).
    const action = track(
      await client.machines.action(machine.id, {
        command: 'click',
        parameters: { x: 640, y: 360 },
      }),
    );
    operations.push('action');
    if (!action.data.success) {
      throw new MachineOpFailedError('actions:click', action.data.error, action.meta.requestId);
    }

    // 5. A batched action sequence (<= 50 steps, stop_on_error).
    const batch = track(
      await client.machines.actionsBatch(machine.id, {
        steps: [
          { command: 'move', parameters: { x: 320, y: 240 } },
          { command: 'type_text', parameters: { text: 'coasty cookbook' } },
          { command: 'key_press', parameters: { key: 'enter' } },
        ],
        stop_on_error: true,
      }),
    );
    operations.push('actions_batch');
    if (batch.data.failed_count > 0 || batch.data.aborted) {
      throw new MachineOpFailedError(
        'actions/batch',
        `${String(batch.data.failed_count)} step(s) failed (aborted=${String(batch.data.aborted)})`,
        batch.meta.requestId,
      );
    }

    // 6. Terminal (bash on Linux, PowerShell on Windows; free).
    const terminal = track(
      await client.machines.terminal(machine.id, {
        command: "echo 'hello from the coasty cookbook'",
        timeout_ms: 30000,
      }),
    );
    operations.push('terminal');

    // 7. Files: write then read it back (free).
    track(
      await client.machines.files(machine.id, 'write', {
        path: REMOTE_FILE_PATH,
        content: REMOTE_FILE_CONTENT,
      }),
    );
    operations.push('file_write');
    const fileRead = track(
      await client.machines.files(machine.id, 'read', { path: REMOTE_FILE_PATH }),
    );
    operations.push('file_read');

    // 8. Browser navigate (free).
    const browser = track(
      await client.machines.browser(machine.id, 'navigate', {
        parameters: { url: 'https://example.com' },
      }),
    );
    operations.push('browser_navigate');

    // 9. Snapshot — 1 credit ($0.01) one-time, refunded on failure.
    const snapshot = track(
      await client.machines.snapshot(
        machine.id,
        options.idempotencyKey === undefined
          ? {}
          : { idempotencyKey: `${options.idempotencyKey}-snap` },
      ),
    );
    operations.push('snapshot');
    log(
      `snapshot ${snapshot.data.snapshot_id} (${String(snapshot.data.credits_charged)} cr charged)`,
    );

    const elapsedMinutes = (now() - startedAtMs) / 60_000;
    const estimatedCredits =
      estimateMachineRuntimeCredits({ osType, minutes: elapsedMinutes }) + snapshotCredits();
    log(
      formatEstimate(
        [
          {
            label: `runtime ${elapsedMinutes.toFixed(1)} min @ ${String(hourly)} cr/hr (floored)`,
            credits: estimateMachineRuntimeCredits({ osType, minutes: elapsedMinutes }),
          },
          { label: 'snapshot (one-time)', credits: snapshotCredits() },
          { label: 'per-call ops (actions/terminal/files/browser/screenshot)', credits: 0 },
        ],
        { sandbox: options.sandbox },
      ),
    );
    log(`credits actually observed via X-Credits-Charged: ${String(creditsObserved)}`);

    return {
      machine,
      operations,
      screenshotPath: options.screenshotPath,
      screenshotBytes: imageBytes.length,
      terminalResult: terminal.data,
      fileContent: fileRead.data,
      browserResult: browser.data,
      snapshotId: snapshot.data.snapshot_id,
      creditsObserved,
      estimatedCredits,
      elapsedMinutes,
    };
  } finally {
    // 10. ALWAYS stop (drops to the 1 cr/hr storage rate) then terminate
    //     (ends all billing) — even when the body threw. Cleanup failures are
    //     reported loudly but never mask the original error.
    for (const [op, call] of [
      ['stop', (): Promise<unknown> => client.machines.stop(machine.id)],
      ['terminate', (): Promise<unknown> => client.machines.terminate(machine.id)],
    ] as const) {
      try {
        await call();
        operations.push(op);
        log(`${op}: ok (${machine.id})`);
      } catch (cleanupError) {
        const requestId = cleanupError instanceof CoastyError ? cleanupError.requestId : null;
        console.error(
          `cleanup "${op}" failed for ${machine.id}: ${String(cleanupError)} ` +
            `(request_id: ${requestId ?? 'n/a'}) — the ${String(ttlMinutes)}-min TTL is the backstop`,
        );
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Thin CLI
// ---------------------------------------------------------------------------

function flagValue(argv: string[], flag: string): string | undefined {
  const index = argv.indexOf(flag);
  return index === -1 ? undefined : argv[index + 1];
}

function isMain(): boolean {
  const entry = process.argv[1];
  return entry !== undefined && path.resolve(entry) === fileURLToPath(import.meta.url);
}

export async function main(argv: string[] = process.argv.slice(2)): Promise<void> {
  const print = (line: string): void => void process.stdout.write(`${line}\n`);
  const sandbox = isSandboxKey();
  const osFlag = flagValue(argv, '--os') ?? 'linux';
  if (osFlag !== 'linux' && osFlag !== 'windows') {
    console.error(`--os must be linux or windows (got ${osFlag})`);
    process.exitCode = 1;
    return;
  }
  const ttlMinutes = Number(flagValue(argv, '--ttl') ?? String(DEFAULT_TTL_MINUTES));
  const screenshotPath = flagValue(argv, '--out') ?? path.resolve('ex08-screenshot.png');

  print(
    formatEstimate(
      [
        {
          label: `runtime <= ${String(ttlMinutes)} min @ ${String(machineHourlyCredits(osFlag, 'running'))} cr/hr`,
          credits: estimateMachineRuntimeCredits({ osType: osFlag, minutes: ttlMinutes }),
        },
        { label: 'snapshot (one-time, refunded on failure)', credits: snapshotCredits() },
      ],
      { sandbox },
    ),
  );
  if (!sandbox) {
    print(
      `note: live provisioning also requires wallet >= ${String(PRICING.provisioningGateCredits)} cr ` +
        `($${creditsToUsd(PRICING.provisioningGateCredits).toFixed(2)}) — a gate, not a fee.`,
    );
  }

  const client = new CoastyClient();
  const report = await runMachineLifecycle(client, {
    sandbox,
    confirmedSpend: argv.includes('--confirm') || spendConfirmed(),
    screenshotPath,
    osType: osFlag,
    ttlMinutes,
  });
  print(`done: ${report.operations.join(' -> ')}`);
}

if (isMain()) {
  main().catch((error: unknown) => {
    if (error instanceof CoastyError) {
      console.error(`[${error.code}] ${error.message} (request_id: ${error.requestId ?? 'n/a'})`);
    } else {
      console.error(String(error));
    }
    process.exitCode = 1;
  });
}
