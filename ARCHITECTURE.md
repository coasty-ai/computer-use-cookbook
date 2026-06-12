# ARCHITECTURE

How the cookbook is put together: one thin shared client per language, ten
examples that only ever import that client, and an offline mock server that
makes the whole API testable with zero network and zero spend.

```
examples (ex01..ex10)          curl/quickstart.sh
        │ import                        │ plain HTTP
        ▼                               ▼
shared client (python/src/coasty, typescript/src/coasty, go/coasty)
        │ HTTPS                         │
        ▼                               ▼
https://coasty.ai/v1   ◄─ or ─►   mock server (mock/, 127.0.0.1:8787/v1)
```

## The shared client (per language)

One thin, fully typed wrapper per language — `python/src/coasty/`,
`typescript/src/coasty/`, `go/coasty/` — with the same module map:

| Module | Responsibility |
| --- | --- |
| `env` | Loads repo-root `.env` (never logs values); `COASTY_API_KEY`, `COASTY_BASE_URL` (default `https://coasty.ai/v1`), `COASTY_CONFIRM_SPEND`, sandbox-key detection |
| `errors` | Parses the documented envelope `{error: {code, message, type, request_id, …extras}}` into a typed hierarchy: Authentication(401) / InsufficientScope(403) / InsufficientCredits(402, exposes `required`+`balance`) / Validation(422/400) / NotFound(404) / Conflict(409) / RateLimit(429, `retry_after`) / Server(5xx). Class is chosen by HTTP status; `code` is preserved verbatim and is the stable branching key. Tolerates non-JSON bodies; always carries `request_id` (body, falling back to the `X-Coasty-Request-Id` header) |
| `client` | Every `/v1` endpoint as a typed method. Auth via `X-API-Key`. Default 60 s timeout. Surfaces `X-Coasty-Request-Id`, `X-Credits-Charged`, `X-Credits-Remaining` on results |
| `sse` | SSE parser (`id:`/`event:`/`data:` framing, multi-line `data`, `:` comments ignored) + a reconnecting iterator that resumes with `Last-Event-ID` and terminates after the `done` event |
| `webhooks` | `verify_signature(raw_body, header, secret)`: HMAC-SHA256 over `"<t>." + raw_body`, constant-time compare, ±300 s timestamp tolerance, malformed input → `false` (never raises) |
| `cost` | The full pricing table as pure functions — base prices, every surcharge (trajectory +2 cr/shot, HD +1 cr strictly above 1280×720, v1 +3 cr, >500-char system prompt +1 cr), run steps 5/8 cr, machine hourly rates, snapshots |
| `dsl` | Typed builders for the 9 workflow step types + 13 condition ops, with client-side `validate()` enforcing the documented limits (≤200 nested steps, ≤8 depth, ≤16 parallel branches, retry 1–20, parallel content bans, reserved `save_as`, id regex) |
| `executor` | Local action dispatch behind a backend interface (pyautogui / Playwright-page / logging backends ship; `NullBackend` for tests), with coordinate scaling |

### Retry policy (identical across languages)

- Retries **only** 429/500/503/504 and transport errors, with exponential
  backoff + **full jitter** (base 0.5 s, cap 8 s, max 4 attempts), honoring
  `Retry-After` (header or body `retry_after`) exactly when present.
- Never retries other 4xx.
- POSTs are retried **only** when inherently safe (`/predict`, `/ground`,
  `/parse` are charged-then-refunded on failure) **or** when the caller set
  an `Idempotency-Key` — a retried create must never start a second run/VM.
- Sleep and RNG are injectable, so tests assert exact retry counts and
  recorded delays without ever sleeping.

### The action-param defense

The live docs describe action params in two different shapes (Reference §6:
`key`, `wait {ms}`, `drag {from_x…}` vs the local-automation section:
`keys`, `wait {seconds}`, `drag {x1…}`). Every executor in this repo accepts
**both** (`key|keys`, `ms|seconds`, `direction+amount` | signed `clicks`,
`from_x…|x1…`), and `raw` (arbitrary pyautogui source) is **logged, never
executed**. See `docs/API_NOTES.md` and SUMMARY.md.

## Examples = pure core + thin CLI

Every example is structured as pure, injectable core functions (client,
screenshot provider, executor backend, clock/sleep, printer all injected) plus
a thin argv-parsing `main`. That is what makes the test suites possible: the
cores run headless against mocked HTTP with deterministic time.

Spend safety is part of the design, not a docstring: billable cores are
guarded by a spend gate that prints an itemized estimate (computed by the
`cost` module, never hand-written) and refuses on a live key without
`--confirm` / `COASTY_CONFIRM_SPEND=1`. Machine examples take a `ttl_minutes`
backstop and clean up in `finally`.

## The mock server (`mock/`)

A FastAPI app emulating every `/v1` endpoint so examples and e2e tests run
fully offline. Design choices:

- **Deterministic by construction**: in-memory `TestState` with a frozen,
  advanceable clock (`POST /__mock__/config {advance_clock_seconds}`),
  seeded ids, no wall-clock or randomness in responses. `POST /__mock__/reset`
  restores a pristine state.
- **Documented envelopes everywhere**: the same error catalog, headers
  (`X-Coasty-Request-Id` on every response, credits headers on billed
  routes), idempotency replay (`X-Coasty-Idempotent-Replay: true`), and
  pricing math (full surcharge model) as `docs/API_NOTES.md`.
- **Observable progression**: runs advance one step per `GET` poll, or to
  completion on an events read; they succeed after 3 steps by default
  (configurable), `[pause]` in the task pauses after step 1 honoring
  `on_awaiting_human`, `[fail]` fails. Workflow runs execute eagerly on
  create via a generator-based interpreter, so `human_approval` genuinely
  suspends and `resume {approved}` continues or fails the step. Guards
  (`budget_cents`, `max_iterations`, deadline) stop with `GUARD_EXCEEDED`.
- **Durable SSE**: per-run event logs with `seq` ids; `Last-Event-ID` and
  `?after=` replay without loss or duplication; `?drop_after=N` deliberately
  cuts the connection to exercise client reconnection.
- **Signed webhooks**: with a `webhook_url` (https, or loopback http as a
  mock extension), lifecycle transitions POST the documented payload signed
  `Coasty-Signature: t=…,v1=HMAC-SHA256(webhook_secret, "<t>."+body)`; the
  secret is returned exactly once at create.
- **Error forcing**: `X-Mock-Force-Error: <CODE>` returns any documented
  error with realistic context extras — this powers the error-matrix
  examples (ex09 feature-probes for it with one free request).
- **Sandbox machines**: instant `mch_test_*` VMs with an in-memory
  filesystem, terminal echo, browser ops, snapshots, and the documented
  `INVALID_STATE` transition matrix.

What it deliberately does **not** model: per-scope authorization (only via
the force header), tier gating, schedules/triggers, and real machine
metering. Conventions are documented in [`mock/README.md`](mock/README.md).

## Test strategy (all offline)

1. **Unit + contract** — every client method asserts the outbound request
   (auth header, `Idempotency-Key`, exact body fields, `cua_version`
   literals) and the parsed response. Python: pytest + respx; TypeScript:
   vitest + an injected recording fetch mock; Go: httptest.
2. **Shared HMAC vectors** — `docs/API_NOTES.md` pins two known-answer
   vectors; all three languages assert the *same* hex digests, plus
   tampered-body, stale/future timestamp (pinned "now"), boundary ±300 s,
   malformed-header, and wrong-secret rejections.
3. **SSE reconnection** — a stream that dies mid-event must resume with
   `Last-Event-ID`, replay nothing twice, lose nothing, and stop at `done`.
4. **Cost known-answers** — the boundary cases that cost real money when
   wrong: exactly 1280×720 is not HD; exactly 500 prompt chars is free;
   HD applies per trajectory image; v1 surcharges.
5. **Mock-server suite** — 161 tests asserting the mock itself honors the
   contract (envelopes, pricing, lifecycle, replay, signing, DSL limits).
6. **E2E** — examples were driven against the live mock server end-to-end
   (this caught real cross-track contract gaps; see SUMMARY.md).
7. **Live smoke (opt-in)** — only with `COASTY_RUN_LIVE=1` *and* a
   `sk-coasty-test-*` key; skipped cleanly otherwise.

## CI

GitHub Actions (`.github/workflows/ci.yml`) runs lint + typecheck + tests for
Python 3.11/3.12, Node 20/22, Go (stable), the mock suite, and the curl smoke
test on every push/PR. No job needs network access beyond dependency installs,
no job needs an API key, and nothing can spend money.
