# LIVE_VALIDATION — running the examples against the real API

This repo was validated **against the live `https://coasty.ai/v1` API** with a
real key, in addition to the offline mock-server e2e pass. This document
records exactly what was run, what worked, the real-API quirks observed (and
how the clients already handle them), and the spend that resulted.

> Secret hygiene: the API key was never printed, logged, or committed during
> validation. Only the key *family* (`sk-coasty-live-*`) and the wallet
> balance were observed; the key value never left `.env`.

## What ran live, and the result

| Surface | How it was exercised | Result |
| --- | --- | --- |
| `GET /v1/models` | client `models()` | ✅ `models=[default]`, `cua_versions=[v1, v3]` |
| `GET /v1/usage` | client `usage()` | ✅ returns period, breakdown, wallet balance |
| `GET /v1/machines/pricing` | client `machine_pricing()` | ✅ matches `docs/API_NOTES.md` exactly (Linux 5/hr, Windows 9/hr, stopped 1/hr, snapshot 1 cr) |
| `POST /v1/parse` (free) | ex04 core (Python + TypeScript) | ✅ returns structured actions; example dispatches them through the executor |
| `POST /v1/predict` | ex01 `run_predict_loop` core, **NullBackend**, synthetic 1280×720 screenshot | ✅ real prediction + `request_id`; 5 cr/call |
| `POST /v1/ground` | ex02 `ground_and_click` core, **NullBackend** | ✅ grounded a synthetic "Login" button at its true center; 3 cr |
| Sessions (create/predict/get/reset/delete) | ex03 `run_session` core, **NullBackend** | ✅ full lifecycle; trajectory surcharge billed correctly on step 2 |
| `POST /v1/machines` (provision) | direct client call | ⛔ `UPSTREAM_ERROR (401)` — see below |
| Runs / Workflows | — | ⛔ require a machine; not runnable live on this account (see below) |

**Mouse-control examples were NOT pointed at the real desktop.** ex01/ex02/ex03
normally drive pyautogui (real mouse/keyboard); they were validated live by
calling their *pure cores* with a `NullBackend` (clicks become no-ops) and a
synthetic screenshot, so the live **API calls** were exercised end-to-end with
zero risk to the host. The full mouse-driving path is covered against the mock.

Runs, workflows, machines, and the full ex01 predict-loop were validated
end-to-end **against the bundled mock server** (`make mock`), including the v4
engine, SSE streaming with `Last-Event-ID` reconnection, `awaiting_human` →
resume, the workflow approve **and** reject paths, and the machine lifecycle
with snapshot + terminate.

## Real-API quirks observed (clients already handle all of these)

1. **`X-Coasty-Request-Id` is not on every response.** The docs state every
   response carries it, but live `GET /v1/models` and `POST /v1/ground` omit
   it (while `POST /v1/predict` and session predict include it). Our clients
   surface `request_id = None` gracefully rather than assuming the header — no
   crash, no wrong value. The live responses that omit it still carry
   `x-coasty-key-kind`, `x-credits-charged`, and `x-ratelimit-*`.
2. **Rate-limit headers.** Live responses include `x-ratelimit-limit`,
   `x-ratelimit-remaining`, `x-ratelimit-reset` (not documented in the main
   reference). The retry layer already honors `Retry-After` on 429s.
3. **Session id prefix is `ses_`** on the live API (the docs' example showed
   `sess_…`). No client hardcodes the prefix, so this is cosmetic.
4. **`/v1/parse` recognizes a subset of pyautogui constructs.** Live `/parse`
   parsed `click`, `press` (→ `key_press` with a `keys` list), `hotkey`
   (→ `key_combo`), and `typewrite`, but skipped `pyautogui.write(...)` and
   `pyautogui.scroll(...)` in one sample. The examples are robust because they
   simply dispatch whatever actions come back; the mock's parser is a superset.
5. **Live `key_press` uses `keys` (a list)** — confirming the
   local-automation doc shape over Reference §6's `key`. Our executors accept
   both (see the action-param discrepancy in `SUMMARY.md`).
6. **Machine provisioning returned `UPSTREAM_ERROR` (HTTP 401, "Unauthorized").**
   This is the Coasty backend's *upstream* (cloud) provisioning failing for
   this account/tier — **not** our key being invalid (the same key authorizes
   all inference calls above). Because runs and workflows require a
   `machine_id`, they could not be exercised live on this account. The client
   surfaces this cleanly as a typed error carrying the `code`; the examples
   print it and exit non-zero without spending (provisioning failures are not
   billed).

## Spend incurred by live validation

Inference only: 2 predict (10 cr) + 1 ground (3 cr) + session create (10 cr) +
2 session predicts (4 + 6 cr, the second including the +2 trajectory
surcharge) ≈ **33 credits ≈ $0.33**. Free endpoints (models, usage, pricing,
parse) and all mock-server runs cost nothing. No machine was provisioned by
this validation (provisioning failed before creating anything).

## Reproducing

```bash
# Free + safe (no spend, no mouse): point the cores at the mock server.
cd mock && .venv/Scripts/python.exe -m coasty_mock --port 8787 &
export COASTY_BASE_URL=http://127.0.0.1:8787/v1
export COASTY_API_KEY=sk-coasty-test-000000000000000000000000000000000000000000000000

# Free against the REAL API (no spend even on a live key):
unset COASTY_BASE_URL
cd python && .venv/Scripts/python.exe examples/ex04_parse.py     # /parse is free
.venv/Scripts/python.exe examples/ex09_error_handling.py         # catalog only vs prod
.venv/Scripts/python.exe examples/ex10_cost_helper.py predict --width 1281
```

Billable live runs require a real key and pass `--confirm`; on a `sk-coasty-test-*`
sandbox key they print `$0 (sandbox)` and never bill.
