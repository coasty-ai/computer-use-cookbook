/**
 * client.ts — contract tests: every method hits the documented method + path,
 * sends `X-API-Key` (never `Bearer` inside it), serializes the documented body
 * shapes (incl. `cua_version` literals), honors `Idempotency-Key`, and
 * surfaces response metadata (request id + credits headers).
 */
import { describe, expect, it } from 'vitest';

import { CoastyError, NotFoundError } from '../src/coasty/errors.js';
import {
  FAKE_API_KEY,
  SCREENSHOT_B64,
  jsonResponse,
  makeClient,
  makePredictResponse,
  makeProvisionResponse,
  makeRun,
  makeWorkflow,
  makeWorkflowRun,
} from './helpers.js';

describe('auth + headers', () => {
  it('sends X-API-Key (without a Bearer prefix) and Accept: application/json', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(jsonResponse({ models: [], cua_versions: [], action_types: [] }));

    await client.models();

    const call = fetchMock.calls[0];
    expect(call).toBeDefined();
    expect(call?.headers.get('x-api-key')).toBe(FAKE_API_KEY);
    expect(call?.headers.get('x-api-key')).not.toContain('Bearer');
    expect(call?.headers.get('authorization')).toBeNull();
    expect(call?.headers.get('accept')).toBe('application/json');
  });

  it('sets Content-Type: application/json only when a body is sent', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(jsonResponse(makePredictResponse()));
    fetchMock.enqueue(jsonResponse({ sessions: [] }));

    await client.predict({ screenshot: SCREENSHOT_B64, instruction: 'go' });
    await client.sessions.list();

    expect(fetchMock.calls[0]?.headers.get('content-type')).toBe('application/json');
    expect(fetchMock.calls[1]?.headers.get('content-type')).toBeNull();
  });
});

describe('core inference', () => {
  it('predict POSTs /predict with the documented body (cua_version literal)', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(jsonResponse(makePredictResponse()));

    const { data } = await client.predict({
      screenshot: SCREENSHOT_B64,
      instruction: 'Open settings',
      cua_version: 'v3',
      screen_width: 1280,
      screen_height: 720,
      max_actions: 3,
      include_reasoning: true,
    });

    const call = fetchMock.calls[0];
    expect(call?.method).toBe('POST');
    expect(call?.path).toBe('/v1/predict');
    expect(call?.body).toEqual({
      screenshot: SCREENSHOT_B64,
      instruction: 'Open settings',
      cua_version: 'v3',
      screen_width: 1280,
      screen_height: 720,
      max_actions: 3,
      include_reasoning: true,
    });
    expect(data.status).toBe('continue');
    expect(data.actions[0]?.action_type).toBe('click');
  });

  it('ground POSTs /ground and returns coordinates', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(jsonResponse({ x: 312, y: 480, usage: makePredictResponse().usage }));

    const { data } = await client.ground({
      screenshot: SCREENSHOT_B64,
      element: 'the blue Submit button',
      screen_width: 1280,
      screen_height: 720,
    });

    expect(fetchMock.calls[0]?.method).toBe('POST');
    expect(fetchMock.calls[0]?.path).toBe('/v1/ground');
    expect(fetchMock.calls[0]?.body).toMatchObject({ element: 'the blue Submit button' });
    expect(data).toMatchObject({ x: 312, y: 480 });
  });

  it('parse POSTs /parse with {code}', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(jsonResponse({ actions: [] }));

    await client.parse({ code: 'pyautogui.click(1, 2)' });

    expect(fetchMock.calls[0]?.method).toBe('POST');
    expect(fetchMock.calls[0]?.path).toBe('/v1/parse');
    expect(fetchMock.calls[0]?.body).toEqual({ code: 'pyautogui.click(1, 2)' });
  });

  it('models GETs /models', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(jsonResponse({ models: [], cua_versions: [], action_types: [] }));

    await client.models();

    expect(fetchMock.calls[0]?.method).toBe('GET');
    expect(fetchMock.calls[0]?.path).toBe('/v1/models');
    expect(fetchMock.calls[0]?.rawBody).toBeNull();
  });

  it('usage GETs /usage with an optional period query', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(jsonResponse({ period: '2026-06' }));
    fetchMock.enqueue(jsonResponse({ period: '2026-05' }));

    await client.usage('2026-06');
    await client.usage();

    expect(fetchMock.calls[0]?.path).toBe('/v1/usage');
    expect(fetchMock.calls[0]?.url.searchParams.get('period')).toBe('2026-06');
    expect(fetchMock.calls[1]?.url.search).toBe('');
  });
});

describe('sessions', () => {
  it('covers create / predict / reset / get / list / delete', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(
      jsonResponse({
        session_id: 'sess_1',
        cua_version: 'v3',
        screen_size: '1280x720',
        created_at: 'now',
        expires_at: 'later',
      }),
      jsonResponse({ ...makePredictResponse(), session_id: 'sess_1', step: 1 }),
      jsonResponse({ status: 'ok', session_id: 'sess_1' }),
      jsonResponse({
        session_id: 'sess_1',
        cua_version: 'v3',
        screen_size: '1280x720',
        step_count: 1,
        created_at: 'now',
        expires_at: 'later',
        total_credits_used: 4,
      }),
      jsonResponse({ sessions: [] }),
      jsonResponse({ status: 'ok', session_id: 'sess_1' }),
    );

    await client.sessions.create({ cua_version: 'v3', max_trajectory_length: 5 });
    await client.sessions.predict('sess_1', { screenshot: SCREENSHOT_B64, instruction: 'next' });
    await client.sessions.reset('sess_1');
    await client.sessions.get('sess_1');
    await client.sessions.list();
    await client.sessions.delete('sess_1');

    expect(fetchMock.calls.map((c) => `${c.method} ${c.path}`)).toEqual([
      'POST /v1/sessions',
      'POST /v1/sessions/sess_1/predict',
      'POST /v1/sessions/sess_1/reset',
      'GET /v1/sessions/sess_1',
      'GET /v1/sessions',
      'DELETE /v1/sessions/sess_1',
    ]);
    expect(fetchMock.calls[0]?.body).toEqual({ cua_version: 'v3', max_trajectory_length: 5 });
    expect(fetchMock.calls[1]?.body).toEqual({
      screenshot: SCREENSHOT_B64,
      instruction: 'next',
    });
    // reset has no body
    expect(fetchMock.calls[2]?.rawBody).toBeNull();
  });

  it('sessions.predict forwards an Idempotency-Key when given', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(jsonResponse({ ...makePredictResponse(), session_id: 's', step: 2 }));

    await client.sessions.predict(
      's',
      { screenshot: SCREENSHOT_B64, instruction: 'next' },
      { idempotencyKey: 'step-s-2-abc' },
    );

    expect(fetchMock.calls[0]?.headers.get('idempotency-key')).toBe('step-s-2-abc');
  });
});

describe('runs', () => {
  it('create POSTs /runs with the documented body and Idempotency-Key header', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(jsonResponse(makeRun({ webhook_secret: 'whsec_once' })));

    const { data } = await client.runs.create(
      {
        machine_id: 'mch_test_1',
        task: 'Export the report',
        cua_version: 'v4',
        max_steps: 25,
        on_awaiting_human: 'pause',
        webhook_url: 'https://example.com/hook',
      },
      { idempotencyKey: 'order-4821' },
    );

    const call = fetchMock.calls[0];
    expect(call?.method).toBe('POST');
    expect(call?.path).toBe('/v1/runs');
    expect(call?.headers.get('idempotency-key')).toBe('order-4821');
    expect(call?.body).toEqual({
      machine_id: 'mch_test_1',
      task: 'Export the report',
      cua_version: 'v4',
      max_steps: 25,
      on_awaiting_human: 'pause',
      webhook_url: 'https://example.com/hook',
    });
    // webhook_secret is returned ONCE on create.
    expect(data.webhook_secret).toBe('whsec_once');
  });

  it('omits the Idempotency-Key header when not provided', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(jsonResponse(makeRun()));

    await client.runs.create({ machine_id: 'm', task: 't' });

    expect(fetchMock.calls[0]?.headers.get('idempotency-key')).toBeNull();
  });

  it('covers get / list / cancel / resume', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(
      jsonResponse(makeRun()),
      jsonResponse({ object: 'list', data: [makeRun()], has_more: false, request_id: 'r' }),
      jsonResponse(makeRun({ status: 'cancelled' })),
      jsonResponse(makeRun({ status: 'running' })),
    );

    await client.runs.get('run_1');
    await client.runs.list({ status: 'running', limit: 5 });
    await client.runs.cancel('run_1');
    await client.runs.resume('run_1', { note: 'captcha solved' });

    expect(fetchMock.calls.map((c) => `${c.method} ${c.path}`)).toEqual([
      'GET /v1/runs/run_1',
      'GET /v1/runs',
      'POST /v1/runs/run_1/cancel',
      'POST /v1/runs/run_1/resume',
    ]);
    expect(fetchMock.calls[1]?.url.searchParams.get('status')).toBe('running');
    expect(fetchMock.calls[1]?.url.searchParams.get('limit')).toBe('5');
    expect(fetchMock.calls[3]?.body).toEqual({ note: 'captcha solved' });
  });

  it('list without filters sends no query string', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(jsonResponse({ object: 'list', data: [], has_more: false, request_id: 'r' }));

    await client.runs.list();

    expect(fetchMock.calls[0]?.url.search).toBe('');
  });
});

describe('workflows', () => {
  it('covers the full CRUD + saved/ad-hoc runs + run ops', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(
      jsonResponse(makeWorkflow()),
      jsonResponse({ object: 'list', data: [], has_more: false, request_id: 'r' }),
      jsonResponse(makeWorkflow()),
      jsonResponse(makeWorkflow({ version: 2 })),
      jsonResponse(makeWorkflow({ status: 'archived' })),
      jsonResponse(makeWorkflowRun({ webhook_secret: 'whsec_wf_once' })),
      jsonResponse(makeWorkflowRun({ workflow_id: null })),
      jsonResponse(makeWorkflowRun()),
      jsonResponse({ object: 'list', data: [], has_more: false, request_id: 'r' }),
      jsonResponse(makeWorkflowRun({ status: 'cancelled' })),
      jsonResponse(makeWorkflowRun({ status: 'running' })),
    );

    const definition = { steps: [{ id: 's1', type: 'task' as const, task: 'do it' }] };
    await client.workflows.create({ name: 'Nightly', slug: 'nightly', definition });
    await client.workflows.list({ limit: 10 });
    await client.workflows.get('wf_1');
    await client.workflows.update('wf_1', { definition });
    await client.workflows.delete('wf_1');
    await client.workflows.run(
      'wf_1',
      { inputs: { city: 'Oslo' } },
      { idempotencyKey: 'wf-run-1' },
    );
    await client.workflows.runAdhoc({ definition, machine_id: 'mch_1' });
    await client.workflows.getRun('wfr_1');
    await client.workflows.listRuns({ workflowId: 'wf_1', limit: 3 });
    await client.workflows.cancelRun('wfr_1');
    await client.workflows.resumeRun('wfr_1', { approved: true, note: 'ship it' });

    expect(fetchMock.calls.map((c) => `${c.method} ${c.path}`)).toEqual([
      'POST /v1/workflows',
      'GET /v1/workflows',
      'GET /v1/workflows/wf_1',
      'PUT /v1/workflows/wf_1',
      'DELETE /v1/workflows/wf_1',
      'POST /v1/workflows/wf_1/runs',
      'POST /v1/workflows/runs',
      'GET /v1/workflows/runs/wfr_1',
      'GET /v1/workflows/runs',
      'POST /v1/workflows/runs/wfr_1/cancel',
      'POST /v1/workflows/runs/wfr_1/resume',
    ]);
    expect(fetchMock.calls[0]?.body).toEqual({ name: 'Nightly', slug: 'nightly', definition });
    expect(fetchMock.calls[1]?.url.searchParams.get('limit')).toBe('10');
    expect(fetchMock.calls[5]?.headers.get('idempotency-key')).toBe('wf-run-1');
    expect(fetchMock.calls[5]?.body).toEqual({ inputs: { city: 'Oslo' } });
    expect(fetchMock.calls[6]?.body).toEqual({ definition, machine_id: 'mch_1' });
    expect(fetchMock.calls[8]?.url.searchParams.get('workflow_id')).toBe('wf_1');
    expect(fetchMock.calls[8]?.url.searchParams.get('limit')).toBe('3');
    // resume body: approved is REQUIRED (false = reject the approval).
    expect(fetchMock.calls[10]?.body).toEqual({ approved: true, note: 'ship it' });
  });
});

describe('machines', () => {
  it('covers the full machine surface', async () => {
    const { client, fetchMock } = makeClient();
    const lifecycle = {
      machine_id: 'mch_1',
      status: 'ok',
      message: 'done',
      request_id: 'r',
    };
    fetchMock.enqueue(
      jsonResponse(makeProvisionResponse()),
      jsonResponse({ object: 'list', data: [], has_more: false, request_id: 'r' }),
      jsonResponse({ linux_running_per_hour: 5 }),
      jsonResponse(makeProvisionResponse().machine),
      jsonResponse(lifecycle), // terminate
      jsonResponse(lifecycle), // start
      jsonResponse(lifecycle), // stop
      jsonResponse(lifecycle), // restart
      jsonResponse(lifecycle), // patchTtl
      jsonResponse({
        machine_id: 'mch_1',
        snapshot_id: 'snap_1',
        name: 'snap',
        created_at: 'now',
        credits_charged: 1,
        request_id: 'r',
      }),
      jsonResponse({
        machine_id: 'mch_1',
        image_b64: SCREENSHOT_B64,
        mime_type: 'image/png',
        width: 1280,
        height: 720,
        captured_at: 'now',
        request_id: 'r',
      }),
      jsonResponse({
        machine_id: 'mch_1',
        command: 'click',
        success: true,
        result: null,
        error: null,
        duration_ms: 12,
        screenshot: null,
        request_id: 'r',
      }),
      jsonResponse({
        machine_id: 'mch_1',
        results: [],
        completed_count: 2,
        failed_count: 0,
        aborted: false,
        request_id: 'r',
      }),
      jsonResponse({ ok: true }), // browser
      jsonResponse({ stdout: 'hi' }), // terminal
      jsonResponse({ exists: true }), // files
      jsonResponse({
        ssh_private_key_pem: null,
        vnc_password: null,
        websocket_url: null,
        devtools_url: null,
      }),
    );

    await client.machines.provision(
      { display_name: 'vm', os_type: 'linux', desktop_enabled: true, ttl_minutes: 60 },
      { idempotencyKey: 'prov-1' },
    );
    await client.machines.list({ limit: 200 });
    await client.machines.pricing();
    await client.machines.get('mch_1');
    await client.machines.terminate('mch_1');
    await client.machines.start('mch_1');
    await client.machines.stop('mch_1');
    await client.machines.restart('mch_1');
    await client.machines.patchTtl('mch_1', 0);
    await client.machines.snapshot('mch_1', { idempotencyKey: 'snap-1' });
    await client.machines.screenshot('mch_1');
    await client.machines.action('mch_1', { command: 'click', parameters: { x: 1, y: 2 } });
    await client.machines.actionsBatch('mch_1', {
      steps: [{ command: 'click' }, { command: 'type' }],
      stop_on_error: true,
    });
    await client.machines.browser('mch_1', 'navigate', {
      parameters: { url: 'https://example.com' },
    });
    await client.machines.terminal('mch_1', { command: 'echo hi', timeout_ms: 5000 });
    await client.machines.files('mch_1', 'exists', { path: '/tmp/report.csv' });
    await client.machines.connection('mch_1');

    expect(fetchMock.calls.map((c) => `${c.method} ${c.path}`)).toEqual([
      'POST /v1/machines',
      'GET /v1/machines',
      'GET /v1/machines/pricing',
      'GET /v1/machines/mch_1',
      'DELETE /v1/machines/mch_1',
      'POST /v1/machines/mch_1/start',
      'POST /v1/machines/mch_1/stop',
      'POST /v1/machines/mch_1/restart',
      'PATCH /v1/machines/mch_1',
      'POST /v1/machines/mch_1/snapshot',
      'GET /v1/machines/mch_1/screenshot',
      'POST /v1/machines/mch_1/actions',
      'POST /v1/machines/mch_1/actions/batch',
      'POST /v1/machines/mch_1/browser/navigate',
      'POST /v1/machines/mch_1/terminal',
      'POST /v1/machines/mch_1/files/exists',
      'GET /v1/machines/mch_1/connection',
    ]);
    expect(fetchMock.calls[0]?.headers.get('idempotency-key')).toBe('prov-1');
    expect(fetchMock.calls[1]?.url.searchParams.get('limit')).toBe('200');
    expect(fetchMock.calls[8]?.body).toEqual({ ttl_minutes: 0 });
    expect(fetchMock.calls[9]?.headers.get('idempotency-key')).toBe('snap-1');
    expect(fetchMock.calls[12]?.body).toEqual({
      steps: [{ command: 'click' }, { command: 'type' }],
      stop_on_error: true,
    });
    expect(fetchMock.calls[13]?.body).toEqual({ parameters: { url: 'https://example.com' } });
    // files() wraps the parameters object.
    expect(fetchMock.calls[15]?.body).toEqual({ parameters: { path: '/tmp/report.csv' } });
  });
});

describe('URL handling', () => {
  it('percent-encodes path ids', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(jsonResponse(makeRun()));

    await client.runs.get('run/../etc?x=1');

    expect(fetchMock.calls[0]?.url.pathname).toBe('/v1/runs/run%2F..%2Fetc%3Fx%3D1');
  });

  it('strips trailing slashes from a custom base URL', async () => {
    const { client, fetchMock } = makeClient({ baseUrl: 'http://127.0.0.1:8787/v1///' });
    fetchMock.enqueue(jsonResponse({ models: [], cua_versions: [], action_types: [] }));

    await client.models();

    expect(fetchMock.calls[0]?.url.toString()).toBe('http://127.0.0.1:8787/v1/models');
  });
});

describe('response metadata + body handling', () => {
  it('surfaces request id, credits headers, test-mode, and idempotent replay', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(
      jsonResponse(makePredictResponse(), {
        requestId: 'req_meta_1',
        headers: {
          'x-credits-charged': '6',
          'x-credits-remaining': '994',
          'x-coasty-test-mode': 'true',
          'x-coasty-idempotent-replay': 'true',
        },
      }),
    );

    const { meta } = await client.predict({ screenshot: SCREENSHOT_B64, instruction: 'go' });

    expect(meta).toEqual({
      requestId: 'req_meta_1',
      creditsCharged: 6,
      creditsRemaining: 994,
      testMode: true,
      idempotentReplay: true,
      status: 200,
    });
  });

  it('defaults metadata fields when headers are absent', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(jsonResponse({ actions: [] }, { requestId: null }));

    const { meta } = await client.parse({ code: 'pyautogui.press("enter")' });

    expect(meta.requestId).toBeNull();
    expect(meta.creditsCharged).toBeNull();
    expect(meta.creditsRemaining).toBeNull();
    expect(meta.testMode).toBe(false);
    expect(meta.idempotentReplay).toBe(false);
  });

  it('returns undefined data for a 204 / empty body', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(new Response(null, { status: 204 }));

    const { data, meta } = await client.sessions.delete('sess_gone');

    expect(data).toBeUndefined();
    expect(meta.status).toBe(204);
  });

  it('throws INVALID_RESPONSE (with the request id) on a non-JSON 200', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(
      new Response('<html>login page</html>', {
        status: 200,
        headers: { 'x-coasty-request-id': 'req_html' },
      }),
    );

    const error = await client.models().catch((e: unknown) => e);

    expect(error).toBeInstanceOf(CoastyError);
    expect((error as CoastyError).code).toBe('INVALID_RESPONSE');
    expect((error as CoastyError).requestId).toBe('req_html');
  });

  it('propagates typed errors (with request id) from error envelopes', async () => {
    const { client, fetchMock } = makeClient();
    fetchMock.enqueue(
      jsonResponse(
        {
          error: {
            code: 'RUN_NOT_FOUND',
            message: 'No such run',
            type: 'not_found_error',
            request_id: 'req_404',
          },
        },
        { status: 404, requestId: 'req_404' },
      ),
    );

    const error = await client.runs.get('run_missing').catch((e: unknown) => e);

    expect(error).toBeInstanceOf(NotFoundError);
    expect((error as NotFoundError).code).toBe('RUN_NOT_FOUND');
    expect((error as NotFoundError).requestId).toBe('req_404');
    expect((error as NotFoundError).statusCode).toBe(404);
  });
});
