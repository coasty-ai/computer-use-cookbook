# Go examples

Four runnable programs over the shared [`coasty`](../coasty) client (stdlib
only). Each opens with a doc comment covering purpose, flow, endpoints and
estimated cost, reads `COASTY_API_KEY` / `COASTY_BASE_URL` from the
environment (repo-root `.env` fallback), prints an itemized cost estimate
via the cost package, and refuses billable calls unless `-confirm` is
passed or `COASTY_CONFIRM_SPEND=1` is set — sandbox keys
(`sk-coasty-test-*`) never bill and skip the gate.

| Example | Endpoints | Estimated cost | Run |
| --- | --- | --- | --- |
| [`predict-loop`](predict-loop) | `POST /v1/predict` | 5 credits/step (+1/step HD, +3/step v1) x `-max-steps` | `go run ./examples/predict-loop -screenshot desk.png -instruction "Open settings"` |
| [`ground`](ground) | `POST /v1/ground` | 3 credits (+1 if HD) | `go run ./examples/ground -screenshot desk.png -element "the search field"` |
| [`runs-sse`](runs-sse) | `POST /v1/runs`, `GET /v1/runs/{id}`, `GET /v1/runs/{id}/events` (SSE), `POST /v1/runs/{id}/resume` | 5 credits per completed step (8 on v1) x `-max-steps` | `go run ./examples/runs-sse -machine mch_test_1 -task "Reconcile the invoice" -events` |
| [`webhook-server`](webhook-server) | none (webhook receiver) | $0 | `go run ./examples/webhook-server -secret whsec_...` |

(`go run` = `& "$env:LOCALAPPDATA\coasty-tools\go\bin\go.exe" run` on this
repo's Windows setup; run from the `go/` directory.)

Shared plumbing lives in [`internal/executor`](internal/executor) (the
`Executor` interface plus a logging executor that decodes BOTH documented
action-param shapes — `key`|`keys`, `ms`|`seconds`, `direction`+`amount` |
signed `clicks`, `from_x...`|`x1...` — never executes `raw` code, and
scales model-space coordinates back to the real screen) and
[`internal/exutil`](internal/exutil) (spend gate, estimate printing, stub
PNG screenshot loader).

`predict-loop` and `ground` take their "screen" from a PNG file: wiring a
real capture/input library (robotgo, kbinani/screenshot, ...) is out of
scope for this stdlib-only track — implement `ScreenshotSource` /
`executor.Executor` against your library and hand them to the example's
core function.

Tests (`go test ./...`) are fully offline: every HTTP interaction is an
`httptest` loopback server, the webhook tests replay the shared
cross-language HMAC vectors from `../../docs/API_NOTES.md` with a pinned
clock, and the SSE test drops the stream mid-way to prove the
`Last-Event-ID` reconnect delivers every event exactly once.
