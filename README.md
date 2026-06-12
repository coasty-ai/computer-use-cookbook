# Coasty Computer Use API Cookbook

Production-quality, multi-language examples for the
[Coasty Computer Use API](https://coasty.ai/docs) — see a screen, act on it
(click/type/scroll), run autonomous agent tasks, and orchestrate multi-step
workflows.

Every use case ships as a runnable example **with offline tests**, in
**Python** and **TypeScript** (primary), **Go** (core subset), and a pure
**cURL/bash** quickstart — all built on one thin, typed, shared client per
language. A bundled **offline mock server** emulates the whole API so you can
run everything with zero network and zero spend.

| Directory | What's inside |
| --- | --- |
| [`python/`](python/) | Shared client (`src/coasty/`) + examples 01–10 + 350 tests |
| [`typescript/`](typescript/) | Shared client (`src/coasty/`) + examples 01–10 + 331 tests |
| [`go/`](go/) | Stdlib-only client package + 4 examples + table-driven tests |
| [`curl/`](curl/) | `quickstart.sh` — the whole core API in commented bash |
| [`mock/`](mock/) | Offline FastAPI mock of `https://coasty.ai/v1` + 161 tests |
| [`docs/API_NOTES.md`](docs/API_NOTES.md) | Distilled API contract this repo is built against |
| [`COOKBOOK.md`](COOKBOOK.md) | Index: use case → file → run command → endpoints → cost |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Client + mock-server design |
| [`SUMMARY.md`](SUMMARY.md) | What was built, coverage, deviations from the live docs |

## Prerequisites

- **Python 3.11+** and/or **Node 20+** (pick your track); **Go 1.22+** for the
  Go track.
- `make` (Git Bash/WSL on Windows) is convenient but optional — every Makefile
  target's underlying command is listed below and in each track's README.
- `curl` (+ optionally `jq`) for the bash quickstart.

## Setup (under 5 minutes)

```bash
git clone https://github.com/coasty-ai/computer-use-cookbook
cd computer-use-cookbook
cp .env.example .env        # then edit .env
```

Put your API key in `.env` (never commit it — `.gitignore` already excludes it):

```dotenv
COASTY_API_KEY=sk-coasty-test-...   # sandbox key: NEVER bills. Start here.
```

> **Use a sandbox key** (`sk-coasty-test-*`) while exploring: it runs the same
> validation and logic as a live key but never debits your wallet, and every
> example prints `$0 (sandbox)`. Create keys at
> <https://coasty.ai/developers/keys>.

Then install one (or every) track:

```bash
# Python
cd python && python -m venv .venv && .venv/Scripts/python.exe -m pip install -e ".[dev,local]"
# (.venv/bin/python on macOS/Linux; [local] adds pyautogui/mss for example 01)

# TypeScript
cd typescript && npm ci

# Go — nothing to install beyond the toolchain
cd go && go build ./...

# Mock server (optional but recommended)
cd mock && python -m venv .venv && .venv/Scripts/python.exe -m pip install -e ".[dev]"
```

## Run your first example

Free, no key risk, works everywhere:

```bash
cd python && .venv/Scripts/python.exe examples/ex04_parse.py        # /v1/parse is free
cd typescript && npx tsx src/examples/ex04-parse.ts
```

Or run the entire API **offline** against the bundled mock server:

```bash
# terminal 1 — start the mock (http://127.0.0.1:8787/v1)
cd mock && .venv/Scripts/python.exe -m coasty_mock --port 8787

# terminal 2 — point any example at it (any well-formed key works offline)
export COASTY_API_KEY=sk-coasty-test-000000000000000000000000000000000000000000000000
export COASTY_BASE_URL=http://127.0.0.1:8787/v1
cd python && .venv/Scripts/python.exe examples/ex05_runs.py \
  --machine-id mch_test_demo --task "Download the latest invoice" --events
```

See [COOKBOOK.md](COOKBOOK.md) for all ten use cases with run commands,
endpoints, and per-example cost estimates.

## Cost warnings & spend safety

Coasty bills a prepaid USD wallet (1 credit = $0.01). This repo is built to
make accidental spend hard:

- **Every billable example prints an itemized cost estimate first** and
  refuses to run against a live key unless you pass `--confirm` (or set
  `COASTY_CONFIRM_SPEND=1`). Sandbox keys proceed with a `$0 (sandbox)` label.
- Machine examples set `ttl_minutes` so a forgotten VM terminates itself, and
  stop/terminate in `finally`.
- The test suites **never touch the network** — all HTTP is mocked, and the
  optional e2e path uses the local mock server.
- Live smoke tests are double-gated: they run only when `COASTY_RUN_LIVE=1`
  **and** the configured key is a sandbox key.
- Reference prices live in `docs/API_NOTES.md`; estimate anything with
  example 10 (`ex10_cost_helper.py` / `ex10-cost-helper.ts`).

## Verifying the repo (no network needed)

```bash
make test lint typecheck          # all tracks, from the repo root
```

Without `make`, per track:

```bash
cd python      && .venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m mypy && .venv/Scripts/python.exe -m ruff check src tests examples && .venv/Scripts/python.exe -m black --check src tests examples
cd typescript  && npm test && npm run typecheck && npm run lint
cd go          && go test ./... && go vet ./... && gofmt -l .
cd mock        && .venv/Scripts/python.exe -m pytest && .venv/Scripts/python.exe -m mypy
cd curl        && bash tests/smoke.sh
```

CI (GitHub Actions) runs the same matrix on every push: Python 3.11/3.12,
Node 20/22, Go stable, the mock suite, and the curl smoke test.

## Troubleshooting

**401 INVALID_API_KEY** — the key is missing, malformed, or revoked. Send a
raw `sk-coasty-...` key in `X-API-Key` (do **not** paste the literal word
"Bearer" into `X-API-Key`; use the `Authorization: Bearer <key>` header if
you prefer bearer auth). Check that `.env` is in the repo root and that your
shell isn't overriding `COASTY_API_KEY`.

**402 INSUFFICIENT_CREDITS** — your prepaid wallet can't cover the request;
the error body tells you `required` vs `balance`. Top up at
<https://coasty.ai/credits> or switch to a sandbox key (free). Note the
**wallet minimums**: provisioning a machine and creating/firing schedules
require a balance of at least **$0.20 (20 credits)** — that's a runway gate,
not a fee — and starting a run requires the wallet to cover at least one step.

**403 INSUFFICIENT_SCOPE** — the key is valid but lacks a scope (the body
names `required_scope` and your `current_scopes`). Elevated scopes like
`terminal:exec`, `files:write`, and `browser:execute` must be requested at
key creation — re-mint the key.

**Clicks land in the wrong place** — the #1 pitfall: coordinates come back in
the space of the screenshot you *sent*. If you downscale before uploading,
pass the downscaled `screen_width`/`screen_height` and multiply the returned
x/y back up. Examples 01/02 show the pattern.

**`make` not found (Windows)** — use Git Bash or WSL, or run the direct
commands above; every Makefile is a thin wrapper over them.

**Wallet ran dry mid-run** — the run fails with `WALLET_EXHAUSTED` (completed
steps stay billed); a machine is **stopped, never destroyed**, and flagged
`suspended_for_billing` — top up and start it again.
