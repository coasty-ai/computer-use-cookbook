# PLAN — Coasty Computer Use API Cookbook

Production-quality, multi-language examples repository for the Coasty Computer
Use API (https://coasty.ai/v1). Source of truth: `https://coasty.ai/docs/llms.txt`
(snapshot kept locally as `.llms.txt`, distilled into `docs/API_NOTES.md`).

## Repository layout

```
computer-use-cookbook/
├── README.md               # Install, env setup, how to run, cost warnings
├── COOKBOOK.md             # Index: use case → file → run cmd → endpoints → est. cost
├── ARCHITECTURE.md         # Shared client + mock server design
├── SUMMARY.md              # What was built, coverage, doc deviations
├── PLAN.md                 # This file
├── Makefile                # make test / lint / typecheck / run-<example>
├── .env.example            # Template; .env is gitignored
├── .github/workflows/ci.yml
├── docs/
│   └── API_NOTES.md        # Distilled API contract + shared HMAC test vectors
├── mock/                   # Offline mock Coasty server (FastAPI)
│   ├── pyproject.toml
│   ├── src/coasty_mock/    # All /v1 endpoints, SSE streams, signed webhooks
│   └── tests/
├── python/                 # Primary track
│   ├── pyproject.toml      # ruff + black + mypy(strict) + pytest + respx
│   ├── src/coasty/         # Shared typed client wrapper
│   ├── examples/           # ex01..ex10 (see list below)
│   └── tests/
├── typescript/             # Primary track
│   ├── package.json        # eslint + prettier + tsc --strict + vitest + msw
│   ├── src/coasty/         # Shared typed client wrapper
│   ├── src/examples/       # ex01..ex10
│   └── tests/
├── go/                     # Secondary track
│   ├── go.mod
│   ├── coasty/             # Shared client package + table-driven tests
│   └── examples/           # predict-loop, ground, runs-sse, webhook-server
└── curl/
    └── quickstart.sh       # Pure curl/bash quickstart (predict, ground, parse,
                            # sessions, runs + SSE)
```

## Shared client wrapper (per language)

One thin, typed client per language; every example imports it. Capabilities:

- Auth from `COASTY_API_KEY` env (loads repo-root `.env` if present; never logs
  the key). Base URL from `COASTY_BASE_URL` (default `https://coasty.ai/v1`).
- Timeouts on every request; retries with exponential backoff + full jitter on
  429/500/503/504 and transport errors, honoring `Retry-After`; never retries
  other 4xx. `Idempotency-Key` support on creates.
- Error envelope parsing → typed exception/error carrying `code`, `message`,
  `type`, `request_id`, and context extras (`required`, `balance`,
  `required_scope`, `retry_after`, …).
- Methods: predict, ground, parse, sessions (create/predict/reset/get/list/
  delete), runs (create/get/list/cancel/resume/events SSE), workflows (CRUD +
  runs + events + resume), machines (provision/get/list/start/stop/terminate/
  patch TTL/screenshot/actions/batch/terminal/files/browser/snapshot), usage,
  models.
- SSE reader with `Last-Event-ID` reconnection (seq cursor).
- Webhook signature verification (`Coasty-Signature: t=...,v1=...`,
  HMAC-SHA256 over `"<t>." + raw_body`, constant-time compare, ±5 min
  timestamp tolerance).
- Cost estimator module implementing the full pricing table incl. surcharges.

## Examples (Python and TypeScript each; Go gets the starred subset)

| #  | Example | Endpoints |
| -- | ------- | --------- |
| 01 | Local screen predict-loop* (screenshot → predict → execute → repeat) | `/predict` |
| 02 | Grounding* (locate element → click) | `/ground` |
| 03 | Stateful sessions (create → multi-step predict → delete) | `/sessions`, `/sessions/{id}/predict` |
| 04 | Parse (pyautogui code → actions; free) | `/parse` |
| 05 | Task runs* (v3 + v4, poll, SSE w/ Last-Event-ID reconnect, awaiting_human → resume) | `/runs`, `/runs/{id}/events` |
| 06 | Webhook server* (HMAC verify: constant-time + timestamp tolerance) | (receiver) |
| 07 | Workflows DSL (task/assert/if/loop/parallel/human_approval/retry; run, stream, resume approval) | `/workflows*` |
| 08 | Machines lifecycle (create → act → snapshot → stop/terminate; cost-aware) | `/machines*` |
| 09 | Error-handling matrix (401/402/403, retry behavior, INSUFFICIENT_CREDITS) | all |
| 10 | Cost/billing helper (estimate $ per op from the pricing table) | (local) |

Examples are split into a pure, testable core function + a thin CLI `main`.
Local-automation deps (pyautogui/mss/pillow; @nut-tree or playwright) are
optional extras so CI never needs a display; the executor is injected and
mocked in tests.

## Spend safety

- Every example that can bill prints an itemized cost estimate first and
  refuses to proceed unless `--confirm` is passed or `COASTY_CONFIRM_SPEND=1`.
- Sandbox keys (`sk-coasty-test-*`) are detected and labeled "$0 (sandbox)".
- All automated tests run against mocks or the local mock server — no network,
  no spend. Live smoke tests are opt-in via `COASTY_RUN_LIVE=1` AND require a
  sandbox key (hard check on the `sk-coasty-test-` prefix; otherwise skip).

## Mock server (`mock/`)

FastAPI app emulating `/v1`: predict/ground/parse/sessions/runs (with state
machine + durable SSE event log + `Last-Event-ID` replay), workflows (DSL
validation + execution stub), machines (sandbox semantics), usage/models.
Signs outbound webhooks with `Coasty-Signature`. Deterministic, in-memory,
seedable. Used by e2e tests and runnable standalone:
`make mock` → `http://127.0.0.1:8787/v1`.

## Test strategy

1. **Unit** (no network): every client method + example core. Python: pytest +
   respx. TS: vitest + msw. Go: httptest. HMAC known-answer vectors shared
   across languages (see `docs/API_NOTES.md` §Test vectors).
2. **Contract**: assert outbound request shapes (headers incl. auth +
   Idempotency-Key, body fields, `cua_version` values, condition `op` set,
   error envelope parsing) against the documented schemas.
3. **HMAC**: valid vector, tampered body, stale timestamp, malformed header.
4. **SSE**: parse id/event/data framing; reconnect resumes via `Last-Event-ID`
   without loss or duplication.
5. **E2E (offline)**: examples driven against the local mock server.
6. **Live smoke (opt-in)**: `COASTY_RUN_LIVE=1` + sandbox key only.

## Toolchain & CI

- Python 3.11+: ruff (lint) + black (format) + mypy --strict + pytest(-cov).
- TypeScript / Node 20+: eslint + prettier + `tsc --strict` + vitest.
- Go 1.22+: gofmt + go vet + go test.
- Root `Makefile`: `test`, `lint`, `typecheck`, `fmt`, `mock`,
  `run-<example>` per language (`make -C python` style dispatch).
  Windows note: use Git Bash/WSL for make, or the documented direct commands.
- GitHub Actions: matrix {python 3.11/3.12, node 20/22, go stable} running
  lint + typecheck + unit tests on push/PR. No network needed.

## Build order

1. Scaffolding: .gitignore, .env.example, PLAN.md, docs/API_NOTES.md. ✅
2. In parallel: Python client, TS client, Go client, mock server, curl track —
   each lands with passing unit tests, lint, typecheck.
3. Per language (pipelined after its client): the examples + their tests.
4. Root Makefile, CI workflow, README/COOKBOOK/ARCHITECTURE docs.
5. Full verification sweep (all suites + adversarial audits: docs-conformance,
   spend-safety, secret hygiene) and fixes.
6. SUMMARY.md, final commits.
