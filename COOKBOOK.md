# COOKBOOK — every use case, indexed

All commands below assume the repo root, a configured `.env` (see
[README.md](README.md)), and the track installed. Costs are the defaults'
worst case at live prices (1 credit = $0.01); **sandbox keys
(`sk-coasty-test-*`) always bill $0**, and every billable example prints its
own itemized estimate and requires `--confirm` / `COASTY_CONFIRM_SPEND=1`
on a live key.

Run anything offline against the bundled mock first:
`COASTY_BASE_URL=http://127.0.0.1:8787/v1` (start it with `make mock`).

| # | Use case | Python | TypeScript | Go | Endpoints | Est. cost (live) |
| - | -------- | ------ | ---------- | -- | --------- | ---------------- |
| 01 | Local screen predict-loop | [ex01](python/examples/ex01_local_predict_loop.py) | [ex01](typescript/src/examples/ex01-local-predict-loop.ts) | [predict-loop](go/examples/predict-loop/main.go) | `POST /predict` | ≤ $0.40 (8 steps × 5 cr) |
| 02 | Grounding → click | [ex02](python/examples/ex02_grounding.py) | [ex02](typescript/src/examples/ex02-grounding.ts) | [ground](go/examples/ground/main.go) | `POST /ground` | $0.03 |
| 03 | Stateful sessions | [ex03](python/examples/ex03_sessions.py) | [ex03](typescript/src/examples/ex03-sessions.ts) | — | `POST /sessions`, `/sessions/{id}/predict` | ≈ $0.26 (10 + 4×4 cr) |
| 04 | Parse pyautogui → actions | [ex04](python/examples/ex04_parse.py) | [ex04](typescript/src/examples/ex04-parse.ts) | — | `POST /parse` | **free** |
| 05 | Task runs + SSE + resume | [ex05](python/examples/ex05_runs.py) | [ex05](typescript/src/examples/ex05-runs.ts) | [runs-sse](go/examples/runs-sse/main.go) | `POST /runs`, `GET /runs/{id}/events` | ≤ $0.50 (10 steps × 5 cr) |
| 06 | Webhook receiver (HMAC) | [ex06](python/examples/ex06_webhook_server.py) | [ex06](typescript/src/examples/ex06-webhook-server.ts) | [webhook-server](go/examples/webhook-server/main.go) | (receives callbacks) | **free** |
| 07 | Workflows DSL end-to-end | [ex07](python/examples/ex07_workflows.py) | [ex07](typescript/src/examples/ex07-workflows.ts) | — | `POST /workflows`, `/workflows/{id}/runs`, events, resume | ≈ $0.40, capped by `budget_cents` |
| 08 | Machines lifecycle | [ex08](python/examples/ex08_machines.py) | [ex08](typescript/src/examples/ex08-machines.ts) | — | `POST /machines`, actions/terminal/files/browser, snapshot | $0.01 + $0.05–0.09/hr (TTL-guarded) |
| 09 | Error-handling matrix | [ex09](python/examples/ex09_error_handling.py) | [ex09](typescript/src/examples/ex09-error-handling.ts) | — | all (deliberate failures) | $0 (failures auto-refund) |
| 10 | Cost/billing helper | [ex10](python/examples/ex10_cost_helper.py) | [ex10](typescript/src/examples/ex10-cost-helper.ts) | — | (local arithmetic) | **free** |

The cURL track covers the core API (models, parse, predict, ground, sessions,
runs + SSE, headers/error envelope) in one script:
[`curl/quickstart.sh`](curl/quickstart.sh) — `bash curl/quickstart.sh`
(≤ 32 cr worst case, gated by `CONFIRM=1` on live keys).

Conventions used below: `PY = python/.venv/Scripts/python.exe`
(`python/.venv/bin/python` on macOS/Linux); TS examples run via `npx tsx`
from `typescript/`; Go examples via `go run` from `go/`.

---

## 01 — Local screen predict-loop

Screenshot → `POST /v1/predict` → execute returned actions locally → repeat
while `status == "continue"`. Python drives your **real desktop** (mss +
pyautogui via the `[local]` extra, FAILSAFE on — slam the mouse into a corner
to abort); TypeScript drives an optional **Playwright page** as the screen
(1280×720 viewport = 1:1 coordinates); Go ships a stub screenshot source +
logging executor. All three downscale/pin to 1280×720 — strictly not HD, so
no +1 cr surcharge — and document the coordinate-scaling pitfall.

```bash
cd python && $PY examples/ex01_local_predict_loop.py "Open the calculator and compute 42 * 17" --max-steps 8
cd typescript && npx tsx src/examples/ex01-local-predict-loop.ts --task "Click the docs link" # needs: npm i -D playwright
cd go && go run ./examples/predict-loop -task "Click OK" -screenshot screen.png
```

## 02 — Grounding

`POST /v1/ground` resolves "the blue Submit button" to exact `(x, y)`, then
the executor clicks it (scaled back to real pixels).

```bash
cd python && $PY examples/ex02_grounding.py "the blue Submit button"
cd typescript && npx tsx src/examples/ex02-grounding.ts --element "the blue Submit button"
cd go && go run ./examples/ground -element "the blue Submit button" -screenshot screen.png
```

## 03 — Stateful sessions

Create a session ($0.10 once), predict step-by-step while the **server**
keeps the trajectory ($0.04/step), inspect `/sessions/{id}`, `reset` (free),
and **always DELETE in `finally`** to free your concurrency slot.

```bash
cd python && $PY examples/ex03_sessions.py "Open the calculator and type 42" --max-steps 4
cd typescript && npx tsx src/examples/ex03-sessions.ts --task "Open the calculator" --max-steps 4
```

## 04 — Parse (free)

`POST /v1/parse` turns raw pyautogui source into structured actions —
deterministic, no model call, $0. Both examples round-trip the result through
the executor's dry-run (NullBackend) to show dispatch of **both documented
param shapes**.

```bash
cd python && $PY examples/ex04_parse.py
cd typescript && npx tsx src/examples/ex04-parse.ts
```

## 05 — Task runs (v3 + v4, poll, SSE, awaiting_human → resume)

Create a run with an `Idempotency-Key` (v3 default; `--v4` for the
autonomous + verifier engine, professional tier+), then either poll
`GET /runs/{id}` to a terminal state or stream `--events` over SSE with
automatic `Last-Event-ID` reconnection. When the run pauses
(`awaiting_human`), resume it via `POST /runs/{id}/resume`.

```bash
cd python && $PY examples/ex05_runs.py --machine-id mch_test_demo --task "Download the latest invoice" --events
cd typescript && npx tsx src/examples/ex05-runs.ts --machine mch_test_demo --task "Download the latest invoice" --events
cd go && go run ./examples/runs-sse -machine mch_test_demo -task "Download the latest invoice" -events
```

Against the mock: a task containing `[pause]` pauses after step 1 (exercise
`--auto-resume`/`-resume-note`); `[fail]` fails the run.

## 06 — Webhook receiver (HMAC verification)

A stdlib-only HTTP server that verifies `Coasty-Signature: t=<ts>,v1=<hex>`
(HMAC-SHA256 over `"<t>." + raw_body`, **constant-time compare**, ±5 min
timestamp tolerance), 401s tampered/stale/malformed deliveries, acks fast,
and dispatches the five `run.*` lifecycle events.

```bash
cd python && COASTY_WEBHOOK_SECRET=whsec_... $PY examples/ex06_webhook_server.py --port 9090
cd typescript && COASTY_WEBHOOK_SECRET=whsec_... npx tsx src/examples/ex06-webhook-server.ts --port 9090
cd go && COASTY_WEBHOOK_SECRET=whsec_... go run ./examples/webhook-server -port 9090
```

The secret is the `webhook_secret` returned **once** when you create a run
with a `webhook_url`. The mock server POSTs signed callbacks to loopback
URLs, so you can watch real deliveries offline.

## 07 — Workflows DSL

Author a definition with the typed DSL builders exercising **every step
type** (task / assert / if / loop / parallel / human_approval / retry /
succeed / fail) and the structured condition ops, validate it locally
(limits: ≤200 steps, ≤8 nesting, ≤16 branches…), create it (name + slug),
start a run with `inputs`, `budget_cents`, and `max_iterations` guards,
stream events, then approve the pending `human_approval`
(`--reject` exercises the failure path). Prints `spent_cents` vs budget.

```bash
cd python && $PY examples/ex07_workflows.py --budget-cents 500
cd typescript && npx tsx src/examples/ex07-workflows.ts --budget-cents 500
```

## 08 — Machines lifecycle (cost-aware)

Provision (sandbox keys get an instant, free `mch_test_*` VM; live needs the
$0.20 wallet gate and your `--confirm`), set a `ttl_minutes` auto-terminate
guard, screenshot → PNG, single + batch actions, terminal, file write/read,
browser navigate, snapshot ($0.01), then **stop + terminate in `finally`** —
with a running cost readout (Linux $0.05/hr, Windows $0.09/hr, metered per
minute, rounded down in your favor).

```bash
cd python && $PY examples/ex08_machines.py --ttl-minutes 10
cd typescript && npx tsx src/examples/ex08-machines.ts --ttl-minutes 10
```

## 09 — Error-handling matrix

Deliberately triggers each documented failure and shows the typed exception,
its stable `code`, the `request_id` to quote to support, the context extras
(`required`/`balance`, `required_scope`, `current_state`…), and exactly what
the client retried (429/5xx with backoff, honoring `Retry-After`) vs
surfaced immediately (all other 4xx; unsafe POSTs without an
`Idempotency-Key`). Pointed at production it only **lists** the catalog —
deliberate failures never fire at the real API. Against the cookbook mock it
detects the `X-Mock-Force-Error` hook and executes everything executable.

```bash
cd python && $PY examples/ex09_error_handling.py            # catalog (safe) or full matrix vs mock
cd typescript && npx tsx src/examples/ex09-error-handling.ts
```

## 10 — Cost helper

Estimate any operation before you buy, straight from the documented pricing
table — including the surcharges people forget: +2 cr per trajectory
screenshot, +1 cr per HD image (strictly >1280×720), +3 cr on the v1 engine,
+1 cr for a system prompt over 500 chars, run steps 5 cr (v3/v4) / 8 cr
(v1), machine hourly rates, snapshots. `plan` mode totals a JSON batch.

```bash
cd python && $PY examples/ex10_cost_helper.py predict --width 1281 --height 720 --trajectory 2
cd python && $PY examples/ex10_cost_helper.py run --steps 12 --cua-version v1
cd typescript && npx tsx src/examples/ex10-cost-helper.ts machine --os windows --hours 1.5
```

---

## Mock-server conventions (offline development)

Documented fully in [`mock/README.md`](mock/README.md):

- any well-formed `sk-coasty-test-*` / `sk-coasty-live-*` key authenticates;
  test keys report `X-Credits-Charged: 0`
- task markers: `[pause]` → `awaiting_human` after step 1, `[fail]` → failed,
  `[done]` (predict) → `status: done`
- `X-Mock-Force-Error: <CODE>` forces any documented error envelope
- `GET …/events?drop_after=N` cuts the SSE stream to exercise reconnection
- `POST /__mock__/reset` and `/__mock__/config` control state, wallet
  balance, and the frozen clock
