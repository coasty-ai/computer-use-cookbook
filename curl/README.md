# curl quickstart — Coasty Computer Use API

A guided, pure **curl + bash** walkthrough of the Coasty API. No SDK, no
language runtime needed for the script itself — just `curl`, `bash`, and
(`jq` *or* `python3`/`python`) for JSON extraction.

```
curl/
├── quickstart.sh          # the walkthrough (sections 0–7, see below)
├── README.md              # this file
└── tests/
    ├── smoke.sh           # offline smoke test (stub server + assertions)
    ├── stub_server.py     # minimal stdlib-only /v1 stub used by smoke.sh
    └── _png_b64.txt       # the 320x240 PNG fixture embedded in quickstart.sh
```

## What the script does

| Section | Call | Cost (sandbox keys: always $0) |
| ------- | ---- | ------------------------------ |
| 0 | Preflight: key check, sandbox/live detection, itemized cost table, consent gate | free |
| 1 | `GET /v1/models` | free |
| 2 | `POST /v1/parse` (pyautogui code → structured actions) | free |
| 3 | `POST /v1/predict` (embedded 320x240 screenshot + instruction) | 5 credits |
| 4 | `POST /v1/ground` (element description → x,y) | 3 credits |
| 5 | Sessions: create → predict → `DELETE` (trap-guaranteed cleanup) | 10 + 4 credits |
| 6 | Runs: create with `Idempotency-Key` → poll to a terminal state (+ a commented `curl -N` SSE block with `Last-Event-ID`) | ≤ 2 steps × 5 credits |
| 7 | Response headers (`-D` dump: `X-Coasty-Request-Id`, `X-Credits-Charged`, …) + a **deliberate 401** using an obviously-fake key | free |

Maximum possible spend on a live key: **32 credits ($0.32)** plus machine
runtime (metered per minute, rounded down — a sub-minute demo meters 0).
1 credit = 1 cent = $0.01 exactly.

## Requirements

- `bash` (Linux, macOS, WSL, or **Git Bash on Windows** — the script handles
  msys path conversion itself). Plain `sh`/PowerShell will not work.
- `curl` on PATH.
- `jq` **or** a working `python3`/`python` — feature-detected, `jq` preferred.
  On Windows, the Microsoft Store `python3` stub is detected and skipped.

## Run it (live or sandbox API)

```bash
export COASTY_API_KEY="sk-coasty-test-..."   # sandbox keys never bill
bash quickstart.sh
```

Sandbox keys (`sk-coasty-test-` prefix) proceed freely. Any other key is
treated as **live/billing** and section 0 aborts (exit code 3) unless you
explicitly consent:

```bash
CONFIRM=1 bash quickstart.sh      # or: bash quickstart.sh --confirm
```

Optional environment:

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `COASTY_BASE_URL` | `https://coasty.ai/v1` | API base URL (trailing `/` is stripped) |
| `COASTY_MACHINE_ID` | *(unset)* | reuse an existing machine in section 6 instead of provisioning a temporary one |
| `COASTY_POLL_INTERVAL` | `2` | seconds between run polls in section 6 |
| `CONFIRM` | `0` | `1` = consent to spending with a live key |

Security notes: the key is never echoed; it is written to a `umask 077` temp
file and passed to curl as a header *file* (`-H @file`) so it never appears in
the process table. The temp dir, the session, and any machine the script
provisioned are removed by an `EXIT` trap even on failure. Do not add
`set -x` — xtrace would leak request headers.

## Run it against the local mock server (no network, $0)

The cookbook ships an offline mock of the full API (see `../mock`). With the
mock listening on its default address, point the quickstart at it:

```bash
export COASTY_API_KEY="sk-coasty-test-000000000000000000000000000000000000000000000000"
COASTY_BASE_URL=http://127.0.0.1:8787/v1 bash quickstart.sh
```

Any `sk-coasty-test-*` key works against the mock; the one above is obviously
fake. Everything is local loopback traffic and bills nothing.

You can also use this directory's own tiny stub (the smoke-test fixture) the
same way — it binds an ephemeral port and writes it to a file:

```bash
python tests/stub_server.py /tmp/port &        # stdlib only, 127.0.0.1
COASTY_STUB_KEY="sk-coasty-test-111111111111111111111111111111111111111111111111" \
  # ^ optional: the only key the stub accepts (this value is its default)
COASTY_API_KEY="sk-coasty-test-111111111111111111111111111111111111111111111111" \
  COASTY_BASE_URL="http://127.0.0.1:$(cat /tmp/port)/v1" \
  bash quickstart.sh
```

## Run the tests

`tests/smoke.sh` is fully offline and deterministic: it starts
`tests/stub_server.py` on an ephemeral 127.0.0.1 port, runs `quickstart.sh`
end-to-end against it with a fake sandbox key, asserts on the output
(including "the key never leaks into the log"), and verifies the live-key
consent gate aborts with exit code 3.

```bash
# Linux / macOS / WSL / Git Bash:
bash tests/smoke.sh
```

On Windows, run it from **Git Bash** (`C:\Windows\system32\bash.exe` is WSL,
which only works if a distro is installed). From PowerShell:

```powershell
& 'C:\Program Files\Git\bin\bash.exe' tests/smoke.sh
```

Expected output ends with:

```
SMOKE PASS: all assertions held (log: quickstart ran clean against the stub)
```

Lint/typecheck for the stub fixture (repo convention: line-length 100,
strict mypy):

```bash
uv tool run ruff check --line-length 100 tests/stub_server.py
uv tool run ruff format --check --line-length 100 tests/stub_server.py
uv tool run mypy --strict tests/stub_server.py
uv tool run --from shellcheck-py shellcheck quickstart.sh tests/smoke.sh
```

## Things the script demonstrates that are easy to get wrong

- **Coordinate scaling (the #1 pitfall).** Returned x/y are in the pixel
  space of the screenshot you *sent*. If you downscale before uploading,
  send the downscaled `screen_width`/`screen_height` and multiply returned
  coordinates back up by `real / sent`. (≤ 1280x720 avoids the +1 HD
  surcharge; exactly 1280x720 is *not* HD.) See section 3's comment block.
- **Screenshot format.** Raw base64, > 100 chars, **no** `data:image/png;base64,`
  prefix (that's a 422 `INVALID_SCREENSHOT`).
- **Idempotency.** `POST /runs` (and `/machines`) carry an `Idempotency-Key`
  so retries can never double-create; replaying the same key with a
  *different* body fails with code `IDEMPOTENCY_KEY_REUSED` (docs list it
  under both 422 and 409 — branch on the **code**, never the status).
- **Retry discipline.** GET/DELETE always retry on transient failures;
  POSTs retry only when documented-safe (`/predict`, `/ground`, `/parse`,
  session predicts — charged-then-refunded) or idempotency-keyed.
- **Error envelope.** Every error is
  `{"error": {code, message, type, request_id, ...}}` — branch on
  `error.code`, quote `error.request_id` (== `X-Coasty-Request-Id`) to
  support. Section 7 triggers a real 401 with an obviously-fake key
  (`sk-coasty-test-` + 48 zeros); your real key is never used or printed there.
- **Cleanup.** Sessions occupy a concurrency slot and machines bill per
  minute, so both are deleted explicitly *and* covered by an `EXIT` trap;
  the demo machine also gets `ttl_minutes: 10` as a server-side safety net.
- **SSE streaming.** Section 6 includes a commented `curl -N` block showing
  the `id:`/`event:`/`data:` framing and `Last-Event-ID` resumption against
  `GET /v1/runs/{id}/events`.
