# API_NOTES — Distilled Coasty API contract

Distilled from `https://coasty.ai/docs/llms.txt` (snapshot: 2026-06-11, kept
locally as `.llms.txt` at repo root — consult it for exact field tables; it is
canonical where this file summarizes).

## Basics

- Base URL: `https://coasty.ai/v1` (override via `COASTY_BASE_URL` in this repo).
- Auth: `X-API-Key: <key>` **or** `Authorization: Bearer <key>`. Never put the
  text `Bearer ` inside `X-API-Key`.
- Key kinds: `sk-coasty-live-<48hex>` (bills), `sk-coasty-test-<48hex>`
  (sandbox, never bills, `X-Coasty-Test-Mode: true`), `cua_sk_<48hex>` (legacy,
  accepted through 2026-11-01).
- Every response has `X-Coasty-Request-Id`; errors repeat it as
  `error.request_id`. Billed responses add `X-Credits-Charged` and
  `X-Credits-Remaining` (cents). 1 credit = 1 cent = $0.01 exactly.
- `Idempotency-Key` header (≤128 chars, `[A-Za-z0-9_\-:]`) supported on:
  POST /runs, POST /workflows(/{id})/runs, POST /machines, snapshot. Reuse with
  a different body → `422 IDEMPOTENCY_KEY_REUSED`. Replay responses carry
  `X-Coasty-Idempotent-Replay: true`.
- `cua_version`: `v1` (slow, +3cr surcharge, pro+), `v3` (default), `v4`
  (autonomous + verifier, pro+ tier; `FEATURE_NOT_AVAILABLE` otherwise).
- `instructions` APPENDS to base prompt; `system_prompt` REPLACES it. Free tier
  has no custom-prompt budget.

## Core inference

### POST /v1/predict  (scope `predict`)
Req: `screenshot` (b64, >100 chars, no `data:` prefix), `instruction` (non-empty),
`cua_version?=v3`, `system_prompt?`, `instructions?`, `screen_width?=1920`
(320–3840), `screen_height?=1080` (240–2160), `trajectory?=[]`
(`[{screenshot, actions, reasoning}]`), `max_actions?=5` (1–10), `tools?`
(allowed action types), `include_reasoning?=true`, `include_raw_code?=true`.
Resp: `{request_id, status: continue|done|fail, reasoning, actions:[{action_type,
params, description, raw_code}], raw_code: [..], usage: {input_tokens,
output_tokens, credits_charged, cost_cents}}`.

### Sessions  (scope `session`)
- `POST /v1/sessions` → `{session_id, cua_version, screen_size, created_at,
  expires_at}`. Req fields: `cua_version?`, `screen_width?`, `screen_height?`,
  `max_trajectory_length?=3` (1–20), `system_prompt?`, `instructions?`,
  `tools?`, `metadata?`.
- `POST /v1/sessions/{id}/predict` req: `screenshot`, `instruction`,
  `include_reasoning?`, `include_raw_code?`. Resp adds `session_id`, `step`.
- `POST /v1/sessions/{id}/reset` → `{status:"ok", session_id}`.
- `DELETE /v1/sessions/{id}` → `{status:"ok", session_id}` (frees concurrency slot).
- `GET /v1/sessions` → `{sessions:[...]}`; `GET /v1/sessions/{id}` →
  `{session_id, cua_version, screen_size, step_count, created_at, expires_at,
  total_credits_used}`.

### POST /v1/ground  (scope `ground`)
Req: `screenshot`, `element`, `screen_width?`, `screen_height?`.
Resp: `{x, y, usage}`.

### POST /v1/parse  (scope `parse`, FREE)
Req: `{code}` (non-empty, <50k chars, pyautogui source).
Resp: `{actions: [{action_type, params, ...}]}`.

### GET /v1/models — `{models, cua_versions, action_types}`. Free.
### GET /v1/usage?period=YYYY-MM — `{period, total_requests, total_credits,
total_cost_cents, breakdown, balance, wallet_balance_cents, wallet_balance_usd}`. Free.

## Action types — IMPORTANT discrepancy

The docs give TWO param shapes. Reference §6 (canonical):

| type | params |
| --- | --- |
| click | `{x, y}` |
| type_text | `{text}` |
| key_press | `{key}` ("enter", "tab", …) |
| key_combo | `{keys: [..]}` |
| scroll | `{x, y, direction, amount}` |
| drag | `{from_x, from_y, to_x, to_y}` |
| move | `{x, y}` |
| wait | `{ms}` |
| done | `{}` |
| fail | `{reason?}` |

The "local automation" section instead shows: `click {x,y,button?,clicks?}`,
`key_press {keys}` (list), `scroll {clicks}` (+up/−down), `drag {x1,y1,x2,y2}`,
`wait {seconds}`, plus a `raw` type carrying pyautogui code.

**Executors in this repo MUST be defensive**: accept both shapes (`key` or
`keys`; `ms` or `seconds`; `amount`+`direction` or signed `clicks`;
`from_x/...` or `x1/...`), never execute `raw` code by default (log it), and
scale coords by (real/sent) factors. Note this discrepancy in SUMMARY.md.

Coordinates come back in the space of the screenshot you SENT — if you
downscale, pass the downscaled `screen_width/height` and multiply returned
x/y back up. ≤1280×720 avoids the HD surcharge (exactly 1280×720 is NOT HD).

## Task runs  (scopes `runs:read`, `runs:write`)

- `POST /v1/runs` req: `machine_id` (req, 1–128), `task` (req, 1–16000),
  `cua_version?=v3`, `instructions?` (≤16000), `system_prompt?` (≤32000),
  `max_steps?=50` (1–1000), `deadline_seconds?` (1–86400),
  `on_awaiting_human?=pause` (`pause|fail|cancel`),
  `awaiting_human_timeout_seconds?`, `webhook_url?` (https only),
  `metadata?` (≤50 keys). Unknown fields → 422.
- Run object: `{id, object:"agent.run", status, machine_id, task, cua_version,
  instructions, max_steps, on_awaiting_human, steps_completed, credits_charged,
  cost_cents, result: {passed, status, summary, verdict?}|null,
  error: {code,message}|null, awaiting_human_reason, metadata, webhook_url,
  webhook_secret (ONCE on create, null afterwards), created_at, started_at,
  awaiting_human_since, finished_at, request_id}`.
- States: `queued → running → (awaiting_human ↔ running) → succeeded|failed|
  cancelled|timed_out` (terminal immutable). `awaiting_human` only when
  `on_awaiting_human == "pause"`.
- `GET /v1/runs?status=&limit=` → `{object:"list", data, has_more, request_id}`
  (limit default 20).
- `GET /v1/runs/{id}`; `POST /v1/runs/{id}/cancel`;
  `POST /v1/runs/{id}/resume` body `{note?}` (≤2000) — only valid from
  `awaiting_human`, else `409 NOT_AWAITING_HUMAN`.

### SSE — GET /v1/runs/{id}/events
Reconnect with header `Last-Event-ID: <seq>` (or `?after=<seq>`); events are
durable, seq is the cursor (no loss/dup). Frames:

```
id: 42
event: status
data: {"status":"running"}
```

Event types: `status, text, reasoning, tool_call, tool_result, awaiting_human,
resumed, step, billing, error, done` (stream closes after `done`).

### Run webhooks
POSTed on terminal states + awaiting_human: events `run.awaiting_human`,
`run.succeeded`, `run.failed`, `run.cancelled`, `run.timed_out`.
Header: `Coasty-Signature: t=<unix>,v1=<hex>`. Signed payload =
`"<t>." + raw_body`; `v1 = HMACSHA256(webhook_secret, signed_payload)` hex.
Verify with constant-time compare AND timestamp tolerance (use 5 min — that is
the documented replay window for trigger webhooks; apply it to run webhooks too).

## Workflows  (scopes `workflows:read|write`; DSL version 2026-06-01)

Endpoints: `POST/GET /v1/workflows`, `GET/PUT/DELETE /v1/workflows/{id}`,
`POST /v1/workflows/{id}/runs` (saved), `POST /v1/workflows/runs` (ad-hoc
inline `definition`), `GET /v1/workflows/runs[/{id}]`,
`GET /v1/workflows/runs/{id}/events` (SSE, same framing),
`POST /v1/workflows/runs/{id}/cancel`, `POST /v1/workflows/runs/{id}/resume`
body `{approved: bool, note?}` (false rejects the pending human_approval).

Create req: `name` (1–128), `slug` (`^[a-z0-9][a-z0-9_-]{0,62}$`), `definition`,
`inputs_schema?`, `description?` (≤2000), `metadata?`. Update bumps `version`;
running snapshots the definition (version pinning).

DSL: `definition = {steps: [...], output?}`; step id `^[A-Za-z0-9_-]{1,64}$`.
Step types (9): `task {task, machine_id?, cua_version?, instructions?,
system_prompt?, max_steps?, save_as?, on_awaiting_human?}` (binds
`{status, passed, result, run_id, steps, error}` under save_as + step id),
`assert {condition, message?}`, `if {condition, then, else?}`,
`loop {count|while, body, max_iterations?}`, `parallel {branches: [[..],..]}`,
`human_approval {message?, timeout_seconds?}`, `retry {body, max_attempts 1–20}`,
`succeed {output?}`, `fail {message?}`.
Conditions (13 ops): `eq|ne|lt|gt|lte|gte|contains {op,left,right}`,
`truthy|falsy|exists {op,value}`, `and|or {op,conditions}`, `not {op,condition}`.
Templating: `{{inputs.x}}`, `{{vars.y}}`, `{{stepId.field}}`.
Limits: ≤200 steps total, ≤8 nesting, ≤16 parallel branches, no
human_approval/succeed/fail inside parallel, save_as ∉ {inputs, vars}.
Start-run req: `inputs?, machine_id?, budget_cents? (0–10000000),
max_iterations? (1–100000), deadline_seconds? (1–86400), webhook_url?,
metadata?, definition?, inputs_schema?`.
WorkflowRun object: `{id, object:"workflow.run", status (same states as runs),
workflow_id, workflow_version, machine_id, inputs, output, error,
awaiting_human_reason, awaiting_step_id, iterations_used, spent_cents,
budget_cents, webhook_url, webhook_secret (once), metadata, created_at,
started_at, finished_at, request_id}`. Guard breach → `GUARD_EXCEEDED`.

## Machines  (scopes `machines:read|write`, +)

- `POST /v1/machines` req: `display_name` (req, 1–64), `os_type?=linux`
  (`linux|windows`), `desktop_enabled?=false`, `provider?=auto`, `cpu_cores?`
  (1–16), `memory_gb?` (1–64), `storage_gb?` (8–500),
  `restore_from_snapshot?`, `ttl_minutes?` (5–10080, auto-terminate),
  `metadata?` (≤16). Resp: `{machine: {id, display_name, status, os_type,
  provider, desktop_enabled, cpu_cores, memory_gb, storage_gb, public_ip,
  is_test, created_at, metadata}, connection: {public_ip, ssh_port,
  ssh_username, vnc_port, websocket_port, has_ssh_key, has_vnc_password},
  request_id}`. Test keys get an INSTANT free sandbox VM with id
  `mch_test_<hex>`; live ids are UUIDs; ids are mode-isolated.
- `GET /v1/machines?limit=` (1–200, default 50), `GET /v1/machines/pricing`,
  `GET /v1/machines/{id}`, `DELETE /v1/machines/{id}` (terminate),
  `POST .../start|stop|restart` → `{machine_id, status, message, request_id}`,
  `PATCH /v1/machines/{id}` `{ttl_minutes}` (0 clears),
  `POST .../snapshot` → `{machine_id, snapshot_id, name, created_at,
  credits_charged, request_id}` ($0.01, scope `snapshots:write`),
  `GET .../screenshot` → `{machine_id, image_b64 (no data: prefix), mime_type,
  width, height, captured_at, request_id}`,
  `GET .../connection` (scope `connection:read`, HIGH-RISK, no-store; returns
  `ssh_private_key_pem`, `vnc_password`, `websocket_url`, `devtools_url`).
- `POST .../actions` `{command, parameters?, timeout_ms? (1000–120000)}` →
  `{machine_id, command, success, result, error, duration_ms, screenshot,
  request_id}`.
- `POST .../actions/batch` `{steps: [≤50 ActionRequest], stop_on_error: true}`
  → `{machine_id, results, completed_count, failed_count, aborted, request_id}`.
- `POST .../browser/{op}` — op ∈ open, navigate, click, type, dom, clickables,
  state, info, scroll, close, screenshot, wait, list-tabs, open-tab, close-tab,
  switch-tab. Body `{parameters, timeout_ms?}`. Raw JS only via /actions
  `browser_execute` (scope `browser:execute`).
- `POST .../terminal` `{command (1–8192), timeout_ms?=30000, session_id?, cwd?}`
  (scope `terminal:exec`; PowerShell on Windows, bash on Unix; output truncated
  at 5000 chars).
- `POST .../files/{op}` — read ops (`read, exists, list, list-directory,
  download, list-downloads` — scope `files:read`); write ops (`write, edit,
  append, delete, delete-directory` — scope `files:write`). Body `{parameters}`.

## Error contract

Envelope (always): `{"error": {code, message, type, request_id, suggestion?,
docs_url?, support?, ...context}}`. Types: auth_error, billing_error,
validation_error, not_found_error, state_error, rate_limit_error, server_error.
Branch on `code`, never `message`.

| HTTP | code | context extras |
| --- | --- | --- |
| 401 | INVALID_API_KEY (+ WWW-Authenticate) | |
| 401 | INVALID_SIGNATURE (trigger webhooks) | |
| 403 | INSUFFICIENT_SCOPE | required_scope, current_scopes |
| 402 | INSUFFICIENT_CREDITS | required, balance |
| 402 | WALLET_EXHAUSTED (mid-run) | |
| 422 | VALIDATION_ERROR | details (field loc) |
| 422 | INVALID_SCREENSHOT | |
| 422 | IDEMPOTENCY_KEY_REUSED (409 per catalog table — see note) | |
| 413 | PAYLOAD_TOO_LARGE (10 MB b64 cap) | |
| 400 | INVALID_LIMIT | actual, min, max |
| 400 | INVALID_STATUS_FILTER | valid_options |
| 400 | FEATURE_NOT_AVAILABLE | |
| 400 | EMPTY_UPDATE (PATCH schedules) | |
| 404 | NOT_FOUND / MACHINE_NOT_FOUND / RUN_NOT_FOUND / WORKFLOW_NOT_FOUND / SESSION_NOT_FOUND | |
| 409 | NOT_AWAITING_HUMAN / RESUME_CONFLICT / INVALID_STATE | current_state, allowed_from |
| 429 | RATE_LIMITED | retry_after + Retry-After header |
| 500 | INTERNAL_ERROR / PREDICTION_FAILED / GROUNDING_FAILED (auto-refunded) | |
| 503 | UPSTREAM_UNAVAILABLE (Retry-After) | retry_after |
| 504 | UPSTREAM_TIMEOUT | |

Note: the docs list IDEMPOTENCY_KEY_REUSED under both 422 (runs section) and
409 (error catalog). Clients should treat the CODE as canonical and not the
status. Record in SUMMARY.md.

**Retry policy (this repo's clients):** retry 429/500/503/504 + transport
errors with exponential backoff + full jitter (base 0.5s, cap 8s, max 4
attempts), honoring `Retry-After` when present. Never retry other 4xx.
GUARD: only retry POSTs when an `Idempotency-Key` was set (predict/ground/
parse are safe to retry; they're charged-then-refunded on failure).

## Pricing (1 credit = $0.01)

| Item | Credits |
| --- | --- |
| POST /predict | 5 |
| POST /sessions (create) | 10 (no surcharges) |
| POST /sessions/{id}/predict | 4 |
| POST /ground | 3 (+1 if HD) |
| POST /parse, session reset/get/list/delete, models, usage, keys | 0 |
| Surcharge: per trajectory screenshot | +2 |
| Surcharge: per HD image (w>1280 OR h>720, strict; applies to current + each trajectory shot) | +1 |
| Surcharge: v1 engine per request | +3 |
| Surcharge: system_prompt > 500 chars (exactly 500 = free) | +1 |
| Run step (v3/v4) — no surcharges on run steps | 5 |
| Run step (v1) | 8 |
| Workflow task step | same as run step; control-flow steps free |
| Machine running Linux (incl. starting/stopping/restarting) | 5/hr |
| Machine running Windows | 9/hr |
| Machine stopped/suspended (any OS) | 1/hr |
| Machine creating/error/terminated | 0 |
| Snapshot | 1 one-time (refunded on failure) |
| Machine per-call ops (actions/terminal/files/browser/screenshots/start/stop) | 0 |
| Provisioning gate (not a fee) | wallet ≥ 20 |
| Schedules: create/run-now/webhook-fire gate | wallet ≥ 20, no fee |
| Scheduled execution | 10 consumer-credits/min (different balance; 20 min start, 6h cap) |

Charges are debited up front and auto-refunded on failure. Runtime is metered
per minute, rounded down. Wallet-dry machine → stopped (never destroyed),
`suspended_for_billing`. Starting a run needs wallet ≥ 1 step.

## Scopes

`predict, session, ground, parse, keys, usage, runs:read, runs:write,
workflows:read, workflows:write, machines:read, machines:write, actions:exec,
terminal:exec, files:read, files:write, browser:execute, snapshots:write,
connection:read, schedules:read, schedules:write, triggers:write`.
Defaults on new keys: predict, session, ground, parse, machines:read,
actions:exec, files:read, runs:read, runs:write, workflows:read, workflows:write.

## Shared HMAC test vectors (use in ALL languages' webhook tests)

Scheme: `v1 = hex(HMAC_SHA256(secret, "<t>." + raw_body))`,
header `Coasty-Signature: t=<t>,v1=<v1>`.

Vector 1 (valid):
- secret: `whsec_test_secret_123`
- t: `1750000000`
- raw body (exact bytes, no trailing newline):
  `{"event":"run.succeeded","run_id":"run_123","status":"succeeded"}`
- v1: `5f70978eab52dbf5838da76e5eb6c6c465560ce8e746ed8e6113c159d8bbb2d4`

Vector 2 (valid, second key):
- secret: `whsec_other_secret_456`
- t: `1750000300`
- raw body: `{"event":"run.awaiting_human","run_id":"run_456","reason":"captcha"}`
- v1: `844504f42b7498094a83cedd7e050fc2f7fa32593b0814cc514c4be52a932e63`

Negative cases derived from vector 1: (a) flip any body byte → reject;
(b) same sig but `t` outside ±300s of "now" → reject (tests pin "now" to
1750000000); (c) malformed header (missing t= or v1=) → reject;
(d) sig computed with vector 2's secret → reject.

## SSE framing reference

UTF-8 lines; events separated by a blank line. Lines: `id: <seq>`,
`event: <type>`, `data: <json>` (data may span multiple `data:` lines —
join with `\n`). Comment lines start with `:` (keepalive) and must be ignored.
Client tracks last seen `id` and sends it as `Last-Event-ID` on reconnect.
