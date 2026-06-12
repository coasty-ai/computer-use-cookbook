# Coasty Go client (secondary track)

A small, **stdlib-only** Go client for the Coasty Computer Use API
(`https://coasty.ai/v1`), shared by the Go examples in this cookbook.
The API contract it implements is distilled in
[`../docs/API_NOTES.md`](../docs/API_NOTES.md) (canonical reference:
`../.llms.txt`).

The runnable examples live in [`examples/`](examples) (see its README):
`predict-loop`, `ground`, `runs-sse` and `webhook-server`, each with an
itemized cost estimate and a `-confirm` / `COASTY_CONFIRM_SPEND=1` spend
gate for non-sandbox keys.

## Toolchain

This repo is developed on Windows with a **portable Go toolchain** installed
at:

```text
%LOCALAPPDATA%\coasty-tools\go\bin\go.exe
```

PowerShell (from this `go/` directory):

```powershell
& "$env:LOCALAPPDATA\coasty-tools\go\bin\go.exe" test ./...
& "$env:LOCALAPPDATA\coasty-tools\go\bin\go.exe" vet ./...
& "$env:LOCALAPPDATA\coasty-tools\go\bin\gofmt.exe" -l .   # empty output = clean
```

If `go` is already on your PATH (any Go >= 1.22 works; no external modules
are needed), plain `go test ./...` etc. work too. The `Makefile` here wraps
the same commands for Git Bash / WSL / CI (`make test lint typecheck`) and
defaults `GO` to the portable toolchain path; override with `make GO=go ...`.

## Package layout (`coasty/`)

| File | What it provides |
| --- | --- |
| `client.go` | `Client` over `net/http`: `X-API-Key` auth, context support, 60s default timeout, retry policy. Methods: `Predict`, `Ground`, `Parse`, `CreateSession` / `SessionPredict` / `ResetSession` / `GetSession` / `ListSessions` / `DeleteSession`, `CreateRun` / `GetRun` / `ListRuns` / `CancelRun` / `ResumeRun`, `Models`, `Usage`. |
| `sse.go` | `SSEScanner` (bufio-based `id:`/`event:`/`data:` framing, multi-line data, comment/keepalive lines) and `StreamRunEvents` — a reconnecting reader that resumes via the `Last-Event-ID` header with no event loss or duplication, ending cleanly (`io.EOF`) after the `done` event. |
| `errors.go` | `APIError` (`Code`, `Message`, `Type`, `RequestID`, `StatusCode`, `Required`, `Balance`, `RequiredScope`, `RetryAfter`, `Extras`) implementing `error`, tolerant of non-JSON bodies, plus helpers `AsAPIError`, `IsInsufficientCredits`, `IsRateLimited`, `IsNotFound`, `IsInsufficientScope`. |
| `webhook.go` | `VerifySignature(rawBody, header, secret, tolerance, now)` for `Coasty-Signature: t=...,v1=...` (HMAC-SHA256 over `"<t>." + raw_body`, constant-time compare, timestamp tolerance — malformed input always returns `false`), and `SignWebhookPayload` for emulating the sender in tests. |
| `cost.go` | The full pricing table: predict/session/ground/parse credits, trajectory/HD/v1/long-system-prompt surcharges (HD is **strict**: exactly 1280x720 is *not* HD; a 500-char system prompt is free, 501 bills +1), run step costs (5 for v3/v4, 8 for v1), machine hourly rates and snapshot cost, credit-to-USD conversions. |
| `env.go` | Tiny `.env` loader: `APIKey()`, `BaseURL()`, `IsSandboxKey()`. Process env (`COASTY_API_KEY`, `COASTY_BASE_URL`) wins; otherwise the loader walks up from the working directory to find the repo-root `.env`. Values are **never logged**. |
| `types.go` | Fully-annotated request/response types, including defensive `Action` param getters that tolerate both documented action-param shapes (see the discrepancy note in `API_NOTES.md`). |

## Retry policy

Matching `docs/API_NOTES.md`:

- Retries `429 / 500 / 503 / 504` and transport errors with exponential
  backoff + **full jitter** (base 500ms, cap 8s, max 4 attempts total),
  honoring `Retry-After` (header wins over the `retry_after` body field).
- Never retries other 4xx.
- POSTs are retried only when inherently safe (`/predict`, `/ground`,
  `/parse` — charged-then-refunded on failure) or when they carry an
  `Idempotency-Key` (`CreateRunRequest.IdempotencyKey` is sent as the
  header, never as a body field).

## Quick start

```go
client := coasty.NewClient() // reads COASTY_API_KEY / COASTY_BASE_URL (.env fallback)

resp, err := client.Predict(ctx, &coasty.PredictRequest{
    Screenshot:  screenshotB64, // base64, no "data:" prefix
    Instruction: "Open the settings menu",
})
if err != nil {
    if coasty.IsInsufficientCredits(err) { /* top up */ }
    apiErr, _ := coasty.AsAPIError(err)
    log.Fatalf("predict failed (request_id %s): %v", apiErr.RequestID, err)
}
for _, action := range resp.Actions { /* execute defensively */ }
```

Streaming run events with automatic `Last-Event-ID` reconnection:

```go
stream, err := client.StreamRunEvents(ctx, runID, nil)
if err != nil { /* ... */ }
defer stream.Close()
for {
    ev, err := stream.Next(ctx)
    if errors.Is(err, io.EOF) { break } // "done" received
    if err != nil { /* ... */ }
    fmt.Printf("seq=%d type=%s %s\n", ev.Seq, ev.Type, ev.Data)
}
```

## Tests

`go test ./...` is fully **offline and deterministic**: every HTTP
interaction is served by `httptest` on loopback, retry sleeps are recorded
through an injectable seam (never actually slept), jitter randomness is
pinned, and the webhook tests use the shared cross-language HMAC vectors
from `docs/API_NOTES.md` with a pinned "now". Sandbox-style fake keys
(`sk-coasty-test-` + 48 zeros) are used throughout; no real key is ever
needed or read.

## Spend safety

- Sandbox keys (`sk-coasty-test-*`) never bill — `IsSandboxKey()` /
  `Client.IsSandbox()` detect them.
- Use the `cost.go` estimators to print an itemized estimate before any
  billable call (see the repo-root PLAN's spend-safety rules).
