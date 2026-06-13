# SUMMARY — what was built, how it was verified, and where the docs disagree

Built against the live source of truth `https://coasty.ai/docs/llms.txt`
(snapshot fetched 2026-06-11, distilled into
[`docs/API_NOTES.md`](docs/API_NOTES.md)).

## What was built

| Track | Contents | Verification (all offline) |
| --- | --- | --- |
| `python/` | Shared typed client (`src/coasty/`: client, errors, types, sse, webhooks, cost, dsl, executor, env) + examples ex01–ex10 + 350 tests | pytest **350 passed**, mypy --strict clean, ruff clean, black clean — **87 % line coverage** over `src/coasty` + `examples` |
| `typescript/` | Shared typed client (`src/coasty/`, same module map) + examples ex01–ex10 + 331 tests | vitest **331 passed** (20 files), `tsc --strict` clean, eslint + prettier clean — **94 % stmt / 96 % line coverage** over `src/coasty` |
| `go/` | Stdlib-only client package + 4 examples (predict-loop, ground, runs-sse, webhook-server) | `go test ./...` **7/7 packages ok** (81.6 % coverage on the client pkg), `go vet` clean, `gofmt -l` clean |
| `mock/` | Offline FastAPI emulation of every `/v1` endpoint: error catalog, pricing math, run state machine, durable SSE + `Last-Event-ID` replay, signed webhooks, full workflow-DSL validation + generator-based interpreter, sandbox machines, `X-Mock-Force-Error` hook | pytest **161 passed**, mypy --strict clean, ruff + black clean |
| `curl/` | `quickstart.sh` (models, parse, predict, ground, sessions, runs + SSE, headers/error envelope; spend-gated) + offline stub-server smoke test | **SMOKE PASS** via Git Bash; shellcheck clean; all 7 sections also completed against the mock server |
| root | README, COOKBOOK (indexed table), ARCHITECTURE, Makefile (`test`/`lint`/`typecheck`/`fmt`/`mock`/`run-exNN`), GitHub Actions matrix (Python 3.11+3.12, Node 20+22, Go stable, mock, curl) | CI simulated locally: fresh `[dev]`-only venv → pytest + mypy green |

Beyond unit/contract tests, every track was driven **end-to-end against the
live mock server** (runs with SSE + `awaiting_human` → resume, workflows with
human approval, machines lifecycle, the full error matrix, the curl script's
seven sections). That e2e pass caught four real cross-track issues that unit
mocks could not, all fixed and committed
(`Harden examples against real servers…`).

The cookbook was **also validated against the real `https://coasty.ai/v1`
API** with a live key — see [`docs/LIVE_VALIDATION.md`](docs/LIVE_VALIDATION.md).
Models, usage, pricing, parse, predict, ground, and the full session lifecycle
all work live; the mouse-driving examples were exercised live through their
pure cores with a `NullBackend` (no real input) and a synthetic screenshot.
Machine provisioning on the test account returned a server-side `UPSTREAM_ERROR`
(so runs/workflows, which need a VM, were validated against the mock), and the
live API revealed a handful of harmless quirks the clients already handle —
all catalogued in that doc and folded into the deviations list below.

Shared HMAC webhook test vectors are pinned in `docs/API_NOTES.md` and
asserted byte-identical in **all three languages** plus the mock
(valid ×2, tampered body, stale/future timestamp with pinned now, ±300 s
boundary, malformed headers, wrong secret). Each language also has an SSE
reconnection test asserting `Last-Event-ID` is sent and nothing is lost or
duplicated.

## Spend safety (as shipped)

- Every billable example prints an itemized estimate (computed by the shared
  `cost` module) and refuses live keys without `--confirm` /
  `COASTY_CONFIRM_SPEND=1`; sandbox keys are detected and labeled `$0`.
- ex09 never fires deliberate failures at the production base URL (it prints
  the catalog instead); machine examples set `ttl_minutes` and stop +
  terminate in `finally`.
- No test in any track touches the network (loopback servers only); CI needs
  no API key. Live smoke tests (brief-optional) were **not** shipped — all
  verification is offline by design; the double gate
  (`COASTY_RUN_LIVE=1` + sandbox-prefix check) is documented in PLAN.md if
  wanted later.

## Deviations & doc discrepancies (live docs win; recorded as required)

1. **Action param shapes conflict inside the live docs.** Reference §6 says
   `key_press {key}`, `wait {ms}`, `drag {from_x…}`, `scroll {x,y,direction,amount}`;
   the local-automation section says `keys` (list), `seconds`, `x1/y1/x2/y2`,
   signed `clicks` — and adds a `raw` action absent from §6. Every executor
   in this repo accepts **both** shapes defensively, and `raw` is logged,
   never executed.
2. **`IDEMPOTENCY_KEY_REUSED` appears under 422** (runs section) **and 409**
   (error catalog). Clients treat the `code` as canonical regardless of
   status; the mock uses 422.
3. **Files-op response shape is not documented.** The docs give request
   bodies for `/machines/{id}/files/{op}` but no response schema; the repo
   accepts content top-level or nested under `result` (the documented
   `/actions` envelope style).
4. **Workflow-run event payloads are unpinned.** Run-event `billing` payloads
   are documented (`credits_charged`, `cost_cents`) but workflow-run events
   are only said to "stream the same way"; examples accept
   `cost_cents`/`spent_cents`/`credits_charged` and fetch the run object for
   the documented `awaiting_step_id` when a bare status event arrives.
5. **Terminal response field is unpinned** (`output` vs `result`); ex08 reads
   either.
6. **Predict has no documented `Idempotency-Key` support** (the header is
   documented for runs, workflow runs, machines, snapshots only), so Python
   ex01 deviates from the build brief's "Idempotency-Key per predict step" —
   predict is charged-then-refunded and inherently retry-safe; session
   predict steps and run creates do send keys. Documented in the example.
7. **Run-webhook timestamp tolerance is unspecified** ("reject if `t` is too
   old"); the 5-minute replay window documented for trigger webhooks is
   applied uniformly (±300 s, constant-time compare everywhere).
8. **Brief vs docs pricing**: the brief omitted the v1 run-step rate
   ($0.08/step) and all inference surcharges (trajectory +$0.02/shot, HD
   +$0.01 strictly above 1280×720, v1 +$0.03, >500-char system prompt
   +$0.01). The cost modules implement the full documented table, including
   the boundary semantics (exactly 1280×720 and exactly 500 chars are free).

## Known limitations

- The mock does not model per-scope authorization (only via
  `X-Mock-Force-Error`), subscription tiers, schedules/triggers, or real
  machine metering — documented in `mock/README.md` and ARCHITECTURE.md.
- Go covers the core subset by design (client + 4 examples); sessions,
  workflows, machines examples are Python/TypeScript only per PLAN.md.
- `make` is not present on stock Windows; every Makefile target's direct
  command is documented (README + per-track READMEs). The Go toolchain used
  for verification is a portable Go 1.26.4 at
  `%LOCALAPPDATA%\coasty-tools\go`.
- A 429-then-recover demonstration needs a scripted response sequence, so
  ex09 defers that one scenario to its test suite even against the mock.

## Commit log (logical units)

- scaffolding: plan, distilled API contract, secret-safe env handling
- python track · typescript track · go track · mock + curl (one commit each)
- e2e hardening fixes (found by driving examples against the live mock)
- root docs + Makefile + CI
- this SUMMARY
- live-API validation notes + multi-language READMEs
