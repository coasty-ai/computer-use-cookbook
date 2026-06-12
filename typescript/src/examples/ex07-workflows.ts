/**
 * Example 07 — Workflows DSL end-to-end (dsl_version 2026-06-01).
 *
 * Authors an "invoice triage" workflow with the typed builders in `dsl.ts`
 * using ALL NINE step types — task, assert, if, loop, parallel,
 * human_approval, retry, succeed, fail — validates it locally against the
 * documented limits, creates it (name + slug), starts a run with inputs +
 * `budget_cents` + `max_iterations`, streams the SSE event log (with
 * `Last-Event-ID` reconnect), resumes the pending `human_approval` step with
 * `{approved: true}` (the rejection path sits behind `--reject`), and finally
 * prints `spent_cents` against the budget.
 *
 * Estimated cost (computed): only `task` steps bill, at the run-step rate of
 * 5 credits each on v3/v4 — control-flow steps are FREE. The typical path
 * here executes ~34 agent steps (export 8 + 2 loop iterations x validate 6 +
 * two parallel notifications x 4 + ledger post 6):
 *   34 steps x 5 cr = 170 credits = $1.70 worst-typical; $0.00 on a sandbox
 * key. Charges are debited per completed step and the run stops at
 * GUARD_EXCEEDED if it would breach `budget_cents`.
 *
 * Run it (sandbox keys never bill; live keys require --confirm):
 *   npx tsx src/examples/ex07-workflows.ts --machine mch_test_demo [--reject] \
 *     [--budget-cents 300] [--confirm]
 */
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { CoastyClient } from '../coasty/client.js';
import { creditsToUsd, estimateWorkflowRunCredits, formatEstimate } from '../coasty/cost.js';
import {
  and,
  assertStep,
  assertValidDefinition,
  definition,
  fail,
  gt,
  humanApproval,
  ifStep,
  loopCount,
  parallel,
  retryStep,
  succeed,
  task,
  truthy,
  type WorkflowDefinition,
} from '../coasty/dsl.js';
import { isSandboxKey, spendConfirmed } from '../coasty/env.js';
import { CoastyError } from '../coasty/errors.js';
import { type RunEvent, type Workflow, type WorkflowRun } from '../coasty/types.js';

// ---------------------------------------------------------------------------
// The workflow definition (all 9 step types)
// ---------------------------------------------------------------------------

export const WORKFLOW_NAME = 'Invoice triage (cookbook ex07)';
export const WORKFLOW_SLUG = 'cookbook-ex07-invoice-triage';

/** Agent steps the typical path executes — drives the printed cost estimate. */
export const ESTIMATED_TASK_STEPS = 34;

export function estimateExampleCredits(): number {
  return estimateWorkflowRunCredits({ taskSteps: ESTIMATED_TASK_STEPS });
}

/**
 * Build the definition with the typed DSL builders. Uses every step type and
 * a spread of condition ops (`truthy`, `gt`, `and`); templating pulls from
 * `{{inputs.*}}` and the `save_as` bindings of earlier task steps.
 */
export function buildInvoiceTriageDefinition(): WorkflowDefinition {
  return definition(
    [
      // 1. task — binds {status, passed, result, run_id, steps, error} as "export".
      task(
        'export-invoices',
        'Open the invoices dashboard and export invoices newer than {{inputs.since}}',
        {
          save_as: 'export',
          max_steps: 8,
        },
      ),
      // 2. assert — hard-stop the workflow when the export task failed.
      assertStep('export-ok', truthy('{{export.passed}}'), 'invoice export must pass'),
      // 3. if / loop / retry — validate each invoice, retrying flaky checks.
      ifStep(
        'any-invoices',
        gt('{{inputs.expected_invoices}}', 0),
        [
          loopCount(
            'validate-each',
            2,
            [
              retryStep(
                'validate-with-retry',
                [
                  task(
                    'validate-invoice',
                    'Open the next exported invoice and verify its totals match the order',
                    { save_as: 'validate', max_steps: 6 },
                  ),
                ],
                3,
              ),
            ],
            10,
          ),
        ],
        [fail('no-invoices', 'expected at least one invoice to triage')],
      ),
      // 4. parallel — fan out the notifications (no human_approval/succeed/fail inside).
      parallel('notify', [
        [
          task('email-summary', 'Email the reconciliation summary to {{inputs.notify_email}}', {
            max_steps: 4,
          }),
        ],
        [
          task('post-slack', 'Post the reconciliation summary to the #finance channel', {
            max_steps: 4,
          }),
        ],
      ]),
      // 5. human_approval — pauses the run (status awaiting_human) until resumed.
      humanApproval('sign-off', {
        message: 'Approve posting the reconciled invoices to the ledger?',
        timeoutSeconds: 3600,
      }),
      // 6. The post-approval task.
      task('post-ledger', 'Post the approved invoices to the ledger', {
        save_as: 'post',
        max_steps: 6,
      }),
      // 7. succeed / fail — explicit terminal steps.
      ifStep(
        'final-check',
        and(truthy('{{validate.passed}}'), truthy('{{post.passed}}')),
        [succeed('done-ok', { posted: '{{post.result}}' })],
        [fail('done-bad', 'ledger post failed after approval')],
      ),
    ],
    { summary: '{{post.result}}' },
  );
}

// ---------------------------------------------------------------------------
// Testable core
// ---------------------------------------------------------------------------

/** Thrown when a live (billable) key is used without explicit confirmation. */
export class SpendNotConfirmedError extends Error {
  constructor(estimatedCredits: number) {
    super(
      `Refusing to spend ~${String(estimatedCredits)} credits ` +
        `($${creditsToUsd(estimatedCredits).toFixed(2)}) on a live key without confirmation. ` +
        'Pass --confirm (or set COASTY_CONFIRM_SPEND=1), or use an sk-coasty-test-* sandbox key.',
    );
    this.name = 'SpendNotConfirmedError';
  }
}

export interface WorkflowExampleOptions {
  /** Default machine for task steps (sandbox machines look like mch_test_*). */
  machineId: string;
  /** True when the API key is a sandbox key (never bills). */
  sandbox: boolean;
  /** True when the user explicitly confirmed live spend. */
  confirmedSpend: boolean;
  /** Approve (true, default) or reject (false) the human_approval step. */
  approve?: boolean;
  inputs?: Record<string, unknown>;
  /** Spend guard for the run; breach -> GUARD_EXCEEDED. Default 300 ($3.00). */
  budgetCents?: number;
  /** Iteration guard for the run. Default 200. */
  maxIterations?: number;
  /** Makes the start-run POST safe to retry. */
  idempotencyKey?: string;
  logger?: (line: string) => void;
}

export interface WorkflowExampleResult {
  workflow: Workflow;
  /** Final state, fetched after the SSE stream closed. */
  run: WorkflowRun;
  events: RunEvent[];
  /** True when an awaiting_human event was answered via resume. */
  resumed: boolean;
  approved: boolean;
}

export const DEFAULT_INPUTS: Record<string, unknown> = {
  since: '2026-06-01',
  expected_invoices: 2,
  notify_email: 'finance@example.com',
};

/**
 * Author -> validate -> create -> start -> stream -> resume -> settle.
 * Every HTTP interaction goes through the injected client, so tests drive
 * this against a mocked transport.
 */
export async function runWorkflowExample(
  client: CoastyClient,
  options: WorkflowExampleOptions,
): Promise<WorkflowExampleResult> {
  const log = options.logger ?? ((line: string): void => void process.stdout.write(`${line}\n`));
  const approve = options.approve ?? true;
  const budgetCents = options.budgetCents ?? 300;
  const maxIterations = options.maxIterations ?? 200;

  if (!options.sandbox && !options.confirmedSpend) {
    throw new SpendNotConfirmedError(estimateExampleCredits());
  }

  // 1. Author + validate locally (free — catches DSL violations before any call).
  const def = buildInvoiceTriageDefinition();
  assertValidDefinition(def);
  log(`definition valid: ${String(def.steps.length)} top-level steps, all 9 step types`);

  // 2. Create the saved workflow (name + slug; slug must match ^[a-z0-9][a-z0-9_-]{0,62}$).
  const { data: workflow } = await client.workflows.create({
    name: WORKFLOW_NAME,
    slug: WORKFLOW_SLUG,
    definition: def,
    description: 'Cookbook example 07 — exercises every DSL step type.',
  });
  log(`created workflow ${workflow.id} (version ${String(workflow.version)})`);

  // 3. Start a run with inputs + spend/iteration guards.
  const { data: started } = await client.workflows.run(
    workflow.id,
    {
      inputs: options.inputs ?? DEFAULT_INPUTS,
      machine_id: options.machineId,
      budget_cents: budgetCents,
      max_iterations: maxIterations,
    },
    options.idempotencyKey === undefined ? {} : { idempotencyKey: options.idempotencyKey },
  );
  log(`started workflow run ${started.id} (budget ${String(budgetCents)} cents)`);

  // 4. Stream the durable SSE event log. The client reconnects automatically
  //    with Last-Event-ID, so a dropped stream loses nothing.
  const events: RunEvent[] = [];
  let resumed = false;
  for await (const event of client.workflows.runEvents(started.id)) {
    events.push(event);
    log(`  [seq ${String(event.seq)}] ${event.type}: ${JSON.stringify(event.data)}`);
    if (event.type === 'awaiting_human' && !resumed) {
      // 5. Answer the human_approval step. approved=false REJECTS it (the
      //    step fails and the workflow takes its failure path).
      log(approve ? '  -> approving sign-off' : '  -> REJECTING sign-off');
      await client.workflows.resumeRun(started.id, {
        approved: approve,
        note: approve ? 'approved via cookbook ex07' : 'rejected via cookbook ex07 --reject',
      });
      resumed = true;
    }
  }

  // 6. Settle: fetch the final run and report spend against the budget.
  const { data: run } = await client.workflows.getRun(started.id);
  log(
    `final status=${run.status} spent ${String(run.spent_cents)} of ${String(run.budget_cents)} budget cents ` +
      `($${creditsToUsd(run.spent_cents).toFixed(2)} of $${creditsToUsd(run.budget_cents).toFixed(2)})`,
  );
  return { workflow, run, events, resumed, approved: approve };
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
  const machineId = flagValue(argv, '--machine') ?? (sandbox ? 'mch_test_demo' : undefined);
  if (machineId === undefined) {
    console.error('A live key needs an explicit --machine <id> (provision one with ex08 first).');
    process.exitCode = 1;
    return;
  }
  const budgetCents = Number(flagValue(argv, '--budget-cents') ?? '300');
  const confirmedSpend = argv.includes('--confirm') || spendConfirmed();

  print(
    formatEstimate(
      [
        {
          label: `~${String(ESTIMATED_TASK_STEPS)} workflow task steps @ 5 cr`,
          credits: estimateExampleCredits(),
        },
      ],
      { sandbox },
    ),
  );

  const client = new CoastyClient();
  const result = await runWorkflowExample(client, {
    machineId,
    sandbox,
    confirmedSpend,
    approve: !argv.includes('--reject'),
    budgetCents,
  });
  print(`workflow run ${result.run.id} finished: ${result.run.status}`);
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
