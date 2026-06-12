# coasty-mock — fully offline mock of the Coasty Computer Use API

A FastAPI re-implementation of `https://coasty.ai/v1` (per `../.llms.txt` and
`../docs/API_NOTES.md`) so every example and e2e test in this cookbook runs
with **zero network and zero spend**. Deterministic, in-memory, seedable.

## Quick start

```bash
# from mock/ (Windows: use .venv\Scripts\... paths; PowerShell: chain with ';')
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"

.venv/Scripts/python.exe -m coasty_mock --port 8787
# -> http://127.0.0.1:8787/v1
```

```bash
curl -s http://127.0.0.1:8787/v1/models \
  -H "X-API-Key: sk-coasty-test-000000000000000000000000000000000000000000000000"
```

Flags: `--host` (default `127.0.0.1`), `--port` (default `8787`), `--seed`
(default `0`), `--frozen-clock` (freeze time at epoch `1750000000`; default for
the standalone server is the wall clock — tests always use the frozen clock).

Or embed it (what the test suites do):

```python
from coasty_mock import create_app, TestState
app = create_app(TestState(seed=1234))   # frozen clock, deterministic ids
```

## What is implemented

| Surface | Routes |
| --- | --- |
| Core inference | `POST /v1/predict`, `POST /v1/ground`, `POST /v1/parse`, `GET /v1/models`, `GET /v1/usage` |
| Sessions | `POST/GET /v1/sessions`, `GET/DELETE /v1/sessions/{id}`, `POST .../predict`, `POST .../reset` |
| Runs | `POST/GET /v1/runs`, `GET /v1/runs/{id}`, `POST .../cancel`, `POST .../resume`, `GET .../events` (SSE) |
| Workflows | full CRUD, saved + ad-hoc runs, `GET .../events` (SSE), cancel/resume (approvals) |
| Machines | provision/list/get/pricing/start/stop/restart/terminate/PATCH ttl, screenshot, snapshot, connection, actions (+batch), browser/{op}, terminal, files/{op} |
| Control | `POST /__mock__/reset`, `GET/POST /__mock__/config`, `GET /__mock__/webhooks` |

Not modeled: schedules & triggers, key management, per-scope enforcement
(see Conventions), tiers/feature gating.

## Auth

- `X-API-Key: <key>` or `Authorization: Bearer <key>`.
- Accepted key shapes: `sk-coasty-test-*` (sandbox, never billed,
  `X-Coasty-Test-Mode: true`), `sk-coasty-live-*`, legacy `cua_sk_*`
  (bills like live). Anything else — including the classic mistake of pasting
  `Bearer ...` into `X-API-Key` — is `401 INVALID_API_KEY` + `WWW-Authenticate`.
- Every response (success, error, `/__mock__`) carries `X-Coasty-Request-Id`;
  error envelopes repeat it as `error.request_id`.
- Billed routes set `X-Credits-Charged` / `X-Credits-Remaining` (`0` charged
  for test keys). Idempotent replays set `X-Coasty-Idempotent-Replay: true`.

## Determinism

- **Ids**: derived from `sha256(f"{seed}:{kind}:{counter}")` — a given seed
  always yields the same `req_*/run_*/sess_*/wf_*/wfr_*/mch_*/snap_*` sequence.
  `POST /__mock__/reset {"seed": N}` restores a pristine state.
- **Clock**: nothing reads the wall clock; all timestamps come from the
  injected clock (frozen at epoch `1750000000` = `2025-06-15T15:06:40Z` in
  tests). Advance it with `POST /__mock__/config {"advance_clock_seconds": N}`.
  Run ticks advance it by `run_step_seconds` (default 1); workflow task steps
  by `workflow_task_step_seconds` (default 30) — that is how
  `deadline_seconds` guards are exercisable without sleeping.
- **No background execution**: state only changes when you call the API
  (see Runs below). Webhook POSTs are the one async piece and they are
  loopback-only.

## Forced errors (`X-Mock-Force-Error`)

Send `X-Mock-Force-Error: <CODE>` on any `/v1` request to get that documented
error envelope + status, with realistic context extras
(`INSUFFICIENT_SCOPE` → `required_scope`/`current_scopes` for the route,
`INSUFFICIENT_CREDITS` → `required`/`balance`, `RATE_LIMITED` and
`UPSTREAM_UNAVAILABLE` → `retry_after` + `Retry-After` header,
`INVALID_STATE` → `current_state`/`allowed_from`, ...). Every code in the
catalog of `docs/API_NOTES.md` §Error contract is supported; an unknown code
returns 422 `VALIDATION_ERROR`. `IDEMPOTENCY_KEY_REUSED` uses **422** (the
runs-section status; the docs also list it under 409 — the CODE is canonical).

## Billing model

- Prices come straight from the documented table (`pricing.py`):
  predict 5, session create 10, session step 4, ground 3, parse free,
  +2/trajectory shot, +1/HD image (strictly >1280 or >720; exactly 1280x720 is
  NOT HD), +3 v1 engine, +1 system_prompt > 500 chars, run step 5 (v3/v4) / 8
  (v1), snapshot 1, machine provision gate wallet ≥ 20.
- **Convention**: `usage.credits_charged` / run `credits_charged` /
  workflow `spent_cents` always report the **nominal** price — even for test
  keys — so cost estimators and budget guards are exercisable offline. The
  **wallet** (and the `X-Credits-Charged` header) is only debited for
  live/legacy keys; test keys always charge 0.
- Wallet starts at 10000 cents; set it with
  `POST /__mock__/config {"wallet_balance_cents": N}`. A live charge that the
  wallet cannot cover → `402 INSUFFICIENT_CREDITS {required, balance}`; a run
  that goes dry mid-flight fails with `error.code == "WALLET_EXHAUSTED"`
  keeping only completed steps billed.

## Predict / sessions conventions

- `screenshot` must be raw base64, > 100 chars, no `data:` prefix — otherwise
  `422 INVALID_SCREENSHOT` (missing field → `422 VALIDATION_ERROR`).
- Actions are synthesized deterministically from the instruction:
  contains `type` → click + `type_text` (quoted text is extracted),
  contains `scroll` → `scroll` (up/down from the words after it), anything
  else → click at screen center. `[done]` → status `done`; `[fail]` → `fail`.
  The same (caller, instruction) pair returns `done` after
  `predict_done_after` calls (default 3) so loops terminate.
- Sessions: server-kept trajectory surcharge grows with `step_count` capped at
  `max_trajectory_length`; sessions expire after `session_ttl_seconds`
  (default 1800) → `404 SESSION_NOT_FOUND`; concurrency is capped at
  `max_concurrent_sessions` (default 25) → `429 RATE_LIMITED`.

## Runs conventions

- State machine: `queued → running → (awaiting_human ↔ running) → succeeded |
  failed | cancelled | timed_out`; terminal states are immutable.
- **Polling drives progress**: each `GET /v1/runs/{id}` advances exactly one
  tick (first tick `queued→running`, then one agent step per poll). The list
  endpoint does NOT advance.
- A run **succeeds after `run_success_steps` steps** (default 3) with
  `result: {passed: true, status, summary}`.
- Task markers: `[pause]` → after step 1 honors `on_awaiting_human`
  (`pause` → `awaiting_human` + webhook; `fail`/`cancel` → straight to that
  terminal state). `[fail]` → final verdict failed (`error.code TASK_FAILED`,
  `result.passed false`).
- `deadline_seconds` + the per-tick clock advance → `timed_out`
  (`error.code DEADLINE_EXCEEDED`).
- Each step appends `tool_call`, `tool_result`, `step`, `billing` events and
  debits one step price (5 cr v3/v4, 8 cr v1).
- `Idempotency-Key` honored on create: same key + same body replays the
  original response (including `webhook_secret`) with
  `X-Coasty-Idempotent-Replay: true`; same key + different body →
  `422 IDEMPOTENCY_KEY_REUSED`.
- 409s: `resume` on a non-paused active run → `NOT_AWAITING_HUMAN`; on a
  terminal run → `RESUME_CONFLICT`; `cancel` on a terminal run →
  `INVALID_STATE`. All carry `current_state` + `allowed_from`.
- `webhook_url` must be `https://...` — or a loopback `http://127.0.0.1|localhost|[::1]`
  URL (mock extension so offline tests can receive real POSTs).

## SSE events (`GET /v1/runs/{id}/events`, workflows alike)

- Reading the stream advances the run as far as it can go (terminal or
  `awaiting_human`) and returns the **durable** event log as proper SSE frames
  (`id: <seq>`, `event: <type>`, `data: <json>`, blank line). The stream
  closes after `done`; a paused run's stream ends *without* `done` — resume,
  then reconnect with `Last-Event-ID` to pick up exactly where you left off.
- Replay: `Last-Event-ID: <seq>` header or `?after=<seq>` (the query param
  wins) — no loss, no duplication; `seq` is 1-based and strictly increasing.
- Test hook: `?drop_after=<n>` closes the connection after at most `n` events
  so clients can rehearse reconnect logic deterministically.

## Webhooks

- Runs and workflow runs with a `webhook_url` get a deterministic
  `webhook_secret` (`whsec_` + 32 hex, derived from seed + run id), returned
  **once** on create (null on every later read).
- A signed POST is recorded on `awaiting_human` and on every terminal
  transition. Run events: `run.succeeded|failed|cancelled|timed_out|awaiting_human`;
  workflow events use the `workflow_run.` prefix.
- Signature: `Coasty-Signature: t=<unix>,v1=<hex>` where
  `v1 = HMAC-SHA256(webhook_secret, f"{t}." + raw_body)` — identical to the
  shared vectors in `docs/API_NOTES.md`.
- Every emission is recorded in `GET /__mock__/webhooks` (headers + exact raw
  body, so signatures can be re-verified). Actual HTTP delivery happens **only
  for loopback URLs** and only while `config.deliver_webhooks` is true — the
  mock never touches the network.

## Workflows conventions

- Full structural DSL validation on create/update/ad-hoc start (9 step types,
  13 condition ops, ≤200 steps, ≤8 nesting levels, ≤16 parallel branches,
  `retry.max_attempts` required 1-20, no `human_approval`/`succeed`/`fail`
  inside `parallel`, `save_as` ∉ {`inputs`,`vars`}) → 422 with `details`
  naming the exact loc.
- Runs execute **eagerly on create**: the create response already shows the
  settled status (`succeeded`/`failed`/`awaiting_human`). The event log is
  written along the way, so SSE replay behaves like the real API.
- Task steps never abort the run; they bind
  `{status, passed, result, run_id, steps, error}` under the step id and
  `save_as`, with `result` = `"Completed: <resolved task text>"` (so
  `contains` checks against words in your task text work). Markers in the
  resolved text: `[fail]` → `passed=false`; `[flaky:N]` → passes from the Nth
  execution of that step per run (use inside `retry`).
- `assert` failures and rejected approvals fail the run with
  `error.code STEP_FAILED` (caught by `retry`); a `fail` step →
  `WORKFLOW_FAILED`; guard breaches (`budget_cents`, `max_iterations`,
  `deadline_seconds`) → `GUARD_EXCEEDED`. All fail the run as `failed`.
- Templating `{{inputs.x}} / {{vars.y}} / {{stepId.field}}`: a string that is
  exactly one template resolves to the raw value (types preserved); mixed
  strings substitute (`None` → empty). Unknown paths resolve to `None` (so
  `exists` is false). The engine maintains `vars.iteration` (innermost loop,
  1-based) and `vars.attempt` (innermost retry).
- `loop` needs exactly one of `count`/`while`; its own `max_iterations`
  (default cap 100) just ends the loop — only the run-level `max_iterations`
  guard raises `GUARD_EXCEEDED`. `parallel` branches run deterministically in
  order. Re-using a slug on `POST /v1/workflows` bumps that workflow's version;
  `PUT` always bumps.
- `human_approval` → `awaiting_human` + `awaiting_step_id`; resume with
  `{"approved": bool, "note"?}` — `false` fails the step (a surrounding
  `retry` re-asks, pausing again).

## Machines conventions

- Provisioning is **instant** for every key kind. Test keys → `mch_test_<hex>`
  ids; live keys → UUID-shaped ids and the wallet ≥ 20 credits gate (402).
  Ids are mode-isolated (cross-mode access → `404 MACHINE_NOT_FOUND`).
- Lifecycle 409s (`INVALID_STATE` + `current_state`/`allowed_from`):
  `start` from `stopped`; `stop`/`restart` from `running`; terminate from
  `running|stopped`; actions/browser/terminal/files/screenshot require
  `running`; snapshot allows `running|stopped`.
- `GET .../screenshot` returns a real 64x36 PNG (decodable, base64 > 100 chars
  → directly reusable as a `/v1/predict` screenshot).
- Actions: batch caps at 50 steps, `stop_on_error: true` aborts shell-`&&`
  style; the `fail` command and unknown commands fail deterministically;
  `duration_ms` is a stable per-command hash. The terminal echoes the command
  (`bash` on linux, `powershell` on windows, output truncated at 5000 chars).
  Files are an in-memory per-machine dict supporting all 11 documented ops.
- `GET .../connection` returns obviously-fake deterministic secrets with
  `Cache-Control: no-store`. Snapshot costs 1 credit and honors
  `Idempotency-Key`. `PATCH {ttl_minutes}`: 5-10080 sets, 0 clears, else 422.
- `ttl_minutes` auto-termination is **not** simulated (TTL is stored and
  echoed only); runtime hourly billing is summarized by
  `GET /v1/machines/pricing` but not metered.

## `/__mock__` control endpoints (no auth)

```text
POST /__mock__/reset     {"seed"?: int}            -> pristine state
GET  /__mock__/config                              -> knobs + wallet + clock
POST /__mock__/config    {"wallet_balance_cents"?, "deliver_webhooks"?,
                          "run_success_steps"?, "predict_done_after"?,
                          "retry_after_seconds"?, "session_ttl_seconds"?,
                          "max_concurrent_sessions"?, "run_step_seconds"?,
                          "workflow_task_step_seconds"?, "latency_ms"?,
                          "advance_clock_seconds"?, "set_clock_epoch"?}
GET  /__mock__/webhooks                            -> recorded deliveries
```

## Developing

```bash
make test        # .venv/Scripts/python.exe -m pytest
make lint        # ruff check + black --check
make typecheck   # mypy (strict)
make serve       # python -m coasty_mock --port 8787
```

(On Windows without make, run the underlying commands directly — they are
printed in the Makefile.) Tests are fully offline: the only sockets ever
opened are loopback listeners inside the webhook tests.
