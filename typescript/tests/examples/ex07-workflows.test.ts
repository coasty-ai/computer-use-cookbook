/**
 * ex07 — workflows DSL end-to-end: the authored definition is valid and uses
 * every step type; create/start/resume request bodies match the documented
 * contract (condition op set, approved flag); awaiting_human is answered via
 * resume; the SSE stream reconnects with Last-Event-ID; the spend gate blocks
 * unconfirmed live keys.
 */
import { describe, expect, it } from 'vitest';

import {
  CONDITION_OPS,
  STEP_TYPES,
  validateDefinition,
  type WorkflowStep,
} from '../../src/coasty/dsl.js';
import {
  DEFAULT_INPUTS,
  ESTIMATED_TASK_STEPS,
  SpendNotConfirmedError,
  WORKFLOW_NAME,
  WORKFLOW_SLUG,
  buildInvoiceTriageDefinition,
  estimateExampleCredits,
  runWorkflowExample,
} from '../../src/examples/ex07-workflows.js';
import {
  jsonResponse,
  makeClient,
  makeWorkflow,
  makeWorkflowRun,
  sseFrame,
  sseResponse,
} from '../helpers.js';

// ---------------------------------------------------------------------------
// Definition contract
// ---------------------------------------------------------------------------

function collectStepTypes(steps: WorkflowStep[], into: Set<string>): void {
  for (const step of steps) {
    into.add(step.type);
    if (step.type === 'if') {
      collectStepTypes(step.then, into);
      if (step.else !== undefined) collectStepTypes(step.else, into);
    } else if (step.type === 'loop' || step.type === 'retry') {
      collectStepTypes(step.body, into);
    } else if (step.type === 'parallel') {
      for (const branch of step.branches) collectStepTypes(branch, into);
    }
  }
}

describe('buildInvoiceTriageDefinition', () => {
  it('passes local validation (no issues)', () => {
    expect(validateDefinition(buildInvoiceTriageDefinition())).toEqual([]);
  });

  it('uses ALL nine documented step types', () => {
    const used = new Set<string>();
    collectStepTypes(buildInvoiceTriageDefinition().steps, used);
    expect([...used].sort()).toEqual([...STEP_TYPES].sort());
  });

  it('only uses documented condition ops', () => {
    const json = JSON.stringify(buildInvoiceTriageDefinition());
    const ops = [...json.matchAll(/"op":"([a-z_]+)"/g)].map((match) => match[1]);
    expect(ops.length).toBeGreaterThan(0);
    for (const op of ops) {
      expect(CONDITION_OPS as readonly (string | undefined)[]).toContain(op);
    }
    // A spread of op families: comparison, value, composite.
    expect(ops).toContain('gt');
    expect(ops).toContain('truthy');
    expect(ops).toContain('and');
  });

  it('slug matches the documented pattern and the estimate is priced from the table', () => {
    expect(WORKFLOW_SLUG).toMatch(/^[a-z0-9][a-z0-9_-]{0,62}$/);
    expect(estimateExampleCredits()).toBe(ESTIMATED_TASK_STEPS * 5);
  });
});

// ---------------------------------------------------------------------------
// End-to-end flow against the mocked transport
// ---------------------------------------------------------------------------

const RUN_FRAMES = [
  sseFrame({ id: 1, event: 'status', data: '{"status":"running"}' }),
  sseFrame({ id: 2, event: 'awaiting_human', data: '{"step_id":"sign-off","message":"Approve?"}' }),
  sseFrame({ id: 3, event: 'resumed', data: '{"approved":true}' }),
  sseFrame({ id: 4, event: 'done', data: '{"status":"succeeded"}' }),
];

describe('runWorkflowExample', () => {
  it('creates, starts, streams, resumes the approval, and reports spend', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(
      jsonResponse(makeWorkflow({ id: 'wf_1' })),
      jsonResponse(makeWorkflowRun({ id: 'wfr_1', status: 'queued', budget_cents: 300 })),
      sseResponse(RUN_FRAMES),
      jsonResponse(makeWorkflowRun({ id: 'wfr_1', status: 'running' })), // resume ack
      jsonResponse(
        makeWorkflowRun({ id: 'wfr_1', status: 'succeeded', spent_cents: 40, budget_cents: 300 }),
      ),
    );
    const logs: string[] = [];

    const result = await runWorkflowExample(client, {
      machineId: 'mch_test_demo',
      sandbox: true,
      confirmedSpend: false,
      idempotencyKey: 'cookbook-ex07-demo',
      logger: (line) => logs.push(line),
    });

    // Request sequence + shapes (the documented contract).
    expect(fetchMock.calls.map((call) => `${call.method} ${call.path}`)).toEqual([
      'POST /v1/workflows',
      'POST /v1/workflows/wf_1/runs',
      'GET /v1/workflows/runs/wfr_1/events',
      'POST /v1/workflows/runs/wfr_1/resume',
      'GET /v1/workflows/runs/wfr_1',
    ]);

    const createBody = fetchMock.calls[0]?.body as Record<string, unknown>;
    expect(createBody.name).toBe(WORKFLOW_NAME);
    expect(createBody.slug).toBe(WORKFLOW_SLUG);
    expect(createBody.definition).toEqual(buildInvoiceTriageDefinition());

    const startBody = fetchMock.calls[1]?.body as Record<string, unknown>;
    expect(startBody).toEqual({
      inputs: DEFAULT_INPUTS,
      machine_id: 'mch_test_demo',
      budget_cents: 300,
      max_iterations: 200,
    });
    expect(fetchMock.calls[1]?.headers.get('idempotency-key')).toBe('cookbook-ex07-demo');

    const resumeBody = fetchMock.calls[3]?.body as Record<string, unknown>;
    expect(resumeBody).toEqual({ approved: true, note: 'approved via cookbook ex07' });

    // Result + spend report.
    expect(result.resumed).toBe(true);
    expect(result.approved).toBe(true);
    expect(result.run.status).toBe('succeeded');
    expect(result.events.map((event) => event.type)).toEqual([
      'status',
      'awaiting_human',
      'resumed',
      'done',
    ]);
    expect(logs.some((line) => line.includes('spent 40 of 300 budget cents'))).toBe(true);
  });

  it('rejection path: --reject sends approved=false to the resume endpoint', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(
      jsonResponse(makeWorkflow({ id: 'wf_1' })),
      jsonResponse(makeWorkflowRun({ id: 'wfr_1' })),
      sseResponse([
        sseFrame({ id: 1, event: 'awaiting_human', data: '{"step_id":"sign-off"}' }),
        sseFrame({ id: 2, event: 'done', data: '{"status":"failed"}' }),
      ]),
      jsonResponse(makeWorkflowRun({ id: 'wfr_1', status: 'running' })),
      jsonResponse(makeWorkflowRun({ id: 'wfr_1', status: 'failed' })),
    );

    const result = await runWorkflowExample(client, {
      machineId: 'mch_test_demo',
      sandbox: true,
      confirmedSpend: false,
      approve: false,
      logger: () => undefined,
    });

    const resumeBody = fetchMock.calls[3]?.body as Record<string, unknown>;
    expect(resumeBody.approved).toBe(false);
    expect(result.approved).toBe(false);
    expect(result.run.status).toBe('failed');
  });

  it('reconnects a dropped SSE stream with Last-Event-ID and loses nothing', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(
      jsonResponse(makeWorkflow({ id: 'wf_1' })),
      jsonResponse(makeWorkflowRun({ id: 'wfr_1' })),
      // First stream delivers seq 1 then drops mid-flight.
      sseResponse([sseFrame({ id: 1, event: 'status', data: '{"status":"running"}' })], {
        drop: true,
      }),
      // Reconnect MUST carry Last-Event-ID: 1.
      (request) => {
        expect(request.headers.get('last-event-id')).toBe('1');
        return sseResponse([
          sseFrame({ id: 2, event: 'awaiting_human', data: '{"step_id":"sign-off"}' }),
          sseFrame({ id: 3, event: 'resumed', data: '{}' }),
          sseFrame({ id: 4, event: 'done', data: '{"status":"succeeded"}' }),
        ]);
      },
      jsonResponse(makeWorkflowRun({ id: 'wfr_1', status: 'running' })),
      jsonResponse(makeWorkflowRun({ id: 'wfr_1', status: 'succeeded', spent_cents: 25 })),
    );

    const result = await runWorkflowExample(client, {
      machineId: 'mch_test_demo',
      sandbox: true,
      confirmedSpend: false,
      logger: () => undefined,
    });

    expect(result.events.map((event) => event.seq)).toEqual([1, 2, 3, 4]); // no loss, no dup
    expect(result.resumed).toBe(true);
    expect(result.run.spent_cents).toBe(25);
  });

  it('spend gate: refuses an unconfirmed live key BEFORE any request', async () => {
    const { client, fetchMock } = makeClient();

    await expect(
      runWorkflowExample(client, {
        machineId: 'mch_live_1',
        sandbox: false,
        confirmedSpend: false,
        logger: () => undefined,
      }),
    ).rejects.toBeInstanceOf(SpendNotConfirmedError);
    expect(fetchMock.calls).toHaveLength(0);
  });

  it('confirmed live spend proceeds', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(
      jsonResponse(makeWorkflow({ id: 'wf_1' })),
      jsonResponse(makeWorkflowRun({ id: 'wfr_1' })),
      sseResponse([sseFrame({ id: 1, event: 'done', data: '{"status":"succeeded"}' })]),
      jsonResponse(makeWorkflowRun({ id: 'wfr_1', status: 'succeeded' })),
    );

    const result = await runWorkflowExample(client, {
      machineId: 'mch_live_1',
      sandbox: false,
      confirmedSpend: true,
      logger: () => undefined,
    });

    expect(result.resumed).toBe(false); // no awaiting_human event in this stream
    expect(fetchMock.calls).toHaveLength(4); // create, start, events, final get
  });
});
