# Coasty Computer Use API — TypeScript track

The shared, fully-typed TypeScript/Node client library for the [Coasty
Computer Use API](https://coasty.ai/v1) cookbook. Every example in
`src/examples/` (ex01–ex10) imports from `src/coasty/`. The canonical API
contract lives in `../docs/API_NOTES.md` (distilled) and `../.llms.txt`
(full reference).

Requires **Node 20+** (uses global `fetch`, `AbortSignal.any`, web streams).
Pure ESM (`"type": "module"`).

## Setup

```powershell
cd typescript
npm install
```

Configuration comes from environment variables (the repo-root `.env` is loaded
automatically and quietly — values are never logged):

| Variable               | Meaning                                                                                                      |
| ---------------------- | ------------------------------------------------------------------------------------------------------------ |
| `COASTY_API_KEY`       | Your API key. Use a sandbox `sk-coasty-test-*` key for free development.                                     |
| `COASTY_BASE_URL`      | Override the base URL (default `https://coasty.ai/v1`; point at the local mock: `http://127.0.0.1:8787/v1`). |
| `COASTY_CONFIRM_SPEND` | `1`/`true` pre-confirms spend prompts in billable examples.                                                  |

## Verify

```powershell
npm run typecheck   # tsc --strict --noEmit (noUncheckedIndexedAccess on)
npm run lint        # eslint (type-checked) + prettier --check
npm test            # vitest run — fully OFFLINE, all HTTP is mocked
npm run test:coverage
npm run fmt         # prettier --write + eslint --fix
```

## Module map (`src/coasty/`)

| Module        | What it provides                                                                                                                                                                                                                                                                                                                                                                              |
| ------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `env.ts`      | `getApiKey()` / `getBaseUrl()` / `spendConfirmed()` / `isSandboxKey()`; loads the repo-root `.env` once via dotenv (quiet).                                                                                                                                                                                                                                                                   |
| `errors.ts`   | `CoastyError` base + 8 typed subclasses mapped from the documented error envelope. Branch on `error.code`, never on `message`. Every error carries `requestId`. Tolerates non-JSON bodies (synthesizes `HTTP_<status>`).                                                                                                                                                                      |
| `client.ts`   | `CoastyClient` — every `/v1` endpoint: `predict`, `ground`, `parse`, `models`, `usage`, `sessions.*`, `runs.*` (incl. SSE `events`), `workflows.*` (CRUD + saved/ad-hoc runs + `runEvents`), `machines.*` (provision → terminate, actions, browser, terminal, files, connection). 60s default timeout, retries, `Idempotency-Key` support, response `meta` with request id + credits headers. |
| `types.ts`    | Precise request/response interfaces and literal unions (`CuaVersion`, `RunStatus`, `ActionType`, …).                                                                                                                                                                                                                                                                                          |
| `sse.ts`      | `parseSseStream` (id/event/data framing, multi-line data, comments ignored) + `reconnectingSse` (resumes via `Last-Event-ID`, filters replays — no loss, no duplicates — terminates after `done`).                                                                                                                                                                                            |
| `webhooks.ts` | `verifySignature(rawBody, header, secret, {toleranceSeconds, now}?)` for `Coasty-Signature: t=...,v1=...` — HMAC-SHA256 over `"<t>." + rawBody`, constant-time compare, ±300s tolerance, never throws. `signPayload` for tests/mock servers.                                                                                                                                                  |
| `cost.ts`     | The full pricing table (1 credit = $0.01) incl. surcharges: strict HD (`>1280` OR `>720`; exactly 1280×720 is NOT HD), trajectory shots, v1 engine, the 500-char system-prompt boundary (exactly 500 is free), run steps 5 (v3/v4) / 8 (v1), machine hourly rates, snapshots, and `formatEstimate()` for spend prompts.                                                                       |
| `dsl.ts`      | Workflow DSL builders for all 9 step types and all 13 condition ops (`CONDITION_OPS` is a readonly tuple), plus `validateDefinition()` / `assertValidDefinition()` enforcing the documented limits (≤200 steps, ≤8 nesting, ≤16 parallel branches, retry 1–20, reserved `save_as`, parallel-forbidden steps).                                                                                 |
| `executor.ts` | `executeAction(s)` — defensive dispatch over BOTH documented action-param shapes (`key`/`keys`, `ms`/`seconds`, `direction+amount`/signed `clicks`, `from_x…`/`x1…`), coordinate scaling by (real/sent), and `raw` actions logged-and-skipped (never executed). `NullBackend` records calls for tests/dry runs.                                                                               |

## Quickstart

```ts
import { CoastyClient } from './src/coasty/client.js';
import { executeActions, NullBackend } from './src/coasty/executor.js';

const client = new CoastyClient(); // key + base URL from env / repo-root .env

const { data, meta } = await client.predict({
  screenshot: screenshotB64, // pure base64, no "data:" prefix
  instruction: 'Open the settings page',
  screen_width: 1280, // <=1280x720 avoids the HD surcharge
  screen_height: 720,
});
console.log(meta.requestId, meta.creditsCharged);

// Coordinates come back in the space of the screenshot you SENT.
const backend = new NullBackend(); // swap in a real Playwright/nut-js backend
await executeActions(data.actions, backend, { scaleX: realW / 1280, scaleY: realH / 720 });
```

### Task runs with SSE (auto-reconnect via Last-Event-ID)

```ts
const { data: run } = await client.runs.create(
  { machine_id: 'mch_test_1', task: 'Export the report', max_steps: 25 },
  { idempotencyKey: 'export-2026-06-11' }, // makes the create retry-safe
);

for await (const event of client.runs.events(run.id)) {
  console.log(event.seq, event.type, event.data); // ends after the 'done' event
}
```

### Webhook verification

```ts
import { verifySignature } from './src/coasty/webhooks.js';

// IMPORTANT: verify the RAW body bytes, before any JSON parsing.
const ok = verifySignature(rawBody, req.headers['coasty-signature'], webhookSecret);
```

### Cost estimate before spending

```ts
import { estimatePredictCredits, formatEstimate } from './src/coasty/cost.js';
import { isSandboxKey } from './src/coasty/env.js';

const credits = estimatePredictCredits({ screenWidth: 1280, screenHeight: 720 });
console.log(formatEstimate([{ label: 'predict x1', credits }], { sandbox: isSandboxKey() }));
```

## Retry policy (built into `CoastyClient`)

- Retries **429 / 500 / 503 / 504** and transport errors with exponential
  backoff + **full jitter** (base 500 ms, cap 8 s, max 4 attempts), honoring
  `Retry-After` (header or body `retry_after`).
- **Never** retries other 4xx (402 surfaces immediately as
  `InsufficientCreditsError` with `required` / `balance`).
- POSTs are only retried when inherently safe (`predict` / `ground` / `parse` —
  charged-then-refunded on failure) **or** when you passed an
  `idempotencyKey`. PATCH is never retried.

## Action-param discrepancy (why the executor is defensive)

The docs describe two param shapes for several action types (Reference §6 vs
the local-automation section): `key` vs `keys`, `ms` vs `seconds`,
`direction`+`amount` vs signed `clicks`, `from_x/…` vs `x1/…`. The executor
accepts **both**, and never executes the `raw` (pyautogui source) action type —
it logs and skips it. See `../docs/API_NOTES.md` §Action types.

## Tests

`tests/` runs fully offline and deterministically: HTTP is mocked by injecting
a recording `fetch` (no sockets), sleeps are recorded no-ops, and webhook
"now" is pinned. The HMAC vectors in `tests/webhooks.test.ts` are the shared
cross-language vectors from `../docs/API_NOTES.md`.

Example tests should reuse `tests/helpers.ts`:

- `FAKE_API_KEY` (sandbox-prefixed, obviously fake) and `makeClient()` — a
  `CoastyClient` wired to a queue-based `FetchMock` with recorded sleeps;
- `jsonResponse` / `errorResponse` / `sseResponse` / `sseFrame` builders;
- payload factories: `makePredictResponse`, `makeRun`, `makeWorkflow`,
  `makeWorkflowRun`, `makeMachine`, `makeProvisionResponse`, …;
- `HMAC_VECTOR_1` / `HMAC_VECTOR_2` — the shared webhook test vectors.

## Spend safety

- Sandbox keys (`sk-coasty-test-*`) never bill; `isSandboxKey()` detects them.
- Billable examples print an itemized `formatEstimate()` first and refuse to
  run without `--confirm` or `COASTY_CONFIRM_SPEND=1`.
- Tests never touch the network and never read real keys.
