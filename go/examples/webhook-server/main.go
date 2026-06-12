// Command webhook-server receives Coasty run webhooks and verifies their
// signatures before trusting a single byte of the payload.
//
// Purpose: show the production-correct receiver pattern. Coasty POSTs a
// JSON payload to your webhook_url on run lifecycle transitions, signed
// with the run's webhook_secret (returned exactly ONCE when the run is
// created — persist it). The header is:
//
//	Coasty-Signature: t=<unix_ts>,v1=<hex(HMAC-SHA256(secret, "<t>." + raw_body))>
//
// Flow:
//  1. Read the raw body (signature covers the exact bytes — verify BEFORE
//     parsing JSON).
//  2. Verify via coasty.VerifySignature: constant-time HMAC compare AND a
//     ±5-minute timestamp tolerance (the documented replay window). Invalid
//     or stale signatures get 401 and are never dispatched.
//  3. Dispatch the five run.* events — run.awaiting_human, run.succeeded,
//     run.failed, run.cancelled, run.timed_out — and answer 200 fast
//     (do heavy work out-of-band; Coasty only needs the 2xx). Unknown
//     event types are acknowledged with 200 and logged so future event
//     types never cause retry storms.
//
// Endpoints: none called — this is the RECEIVER for the webhooks configured
// via POST /v1/runs {webhook_url}.
//
// Estimated cost (coasty cost package): 0 credits — receiving webhooks is
// free, so there is no spend gate here.
//
// Usage:
//
//	webhook-server [-addr 127.0.0.1:8788] [-secret whsec_...]
//
// The secret defaults to COASTY_WEBHOOK_SECRET (process env or repo-root
// .env). Never commit it; it is never logged.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"time"

	"github.com/coasty-ai/computer-use-cookbook/go/coasty"
)

// maxBodyBytes bounds webhook bodies; Coasty payloads are tiny.
const maxBodyBytes = 1 << 20

// The five documented run.* webhook events.
const (
	EventRunAwaitingHuman = "run.awaiting_human"
	EventRunSucceeded     = "run.succeeded"
	EventRunFailed        = "run.failed"
	EventRunCancelled     = "run.cancelled"
	EventRunTimedOut      = "run.timed_out"
)

// Event is a verified webhook payload. Raw holds the exact signed bytes for
// anything the typed fields do not cover.
type Event struct {
	Event  string          `json:"event"`
	RunID  string          `json:"run_id"`
	Status string          `json:"status,omitempty"`
	Reason string          `json:"reason,omitempty"`
	Raw    json.RawMessage `json:"-"`
}

// Handler is the webhook receiver. Only requests whose Coasty-Signature
// verifies against Secret (constant-time, within Tolerance of Now) are
// dispatched; everything else is rejected with 401 before parsing.
type Handler struct {
	// Secret is the run's webhook_secret (returned once on create).
	Secret string
	// Tolerance is the accepted timestamp skew (default ±5 minutes, the
	// documented replay window).
	Tolerance time.Duration
	// Now returns the current time (default time.Now; tests pin it).
	Now func() time.Time
	// Log receives one line per request (default os.Stdout). Secrets and
	// signatures are never logged.
	Log io.Writer
	// OnEvent, when set, observes every verified, dispatched event. Keep it
	// fast: Coasty just needs the 2xx — do heavy work out-of-band.
	OnEvent func(Event)
}

func (h *Handler) logf(format string, args ...any) {
	w := h.Log
	if w == nil {
		w = os.Stdout
	}
	fmt.Fprintf(w, format+"\n", args...)
}

func writeJSON(w http.ResponseWriter, status int, body string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = io.WriteString(w, body)
}

// ServeHTTP implements http.Handler.
func (h *Handler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", http.MethodPost)
		writeJSON(w, http.StatusMethodNotAllowed, `{"error":"webhooks are POSTed"}`)
		return
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, maxBodyBytes+1))
	if err != nil {
		writeJSON(w, http.StatusBadRequest, `{"error":"unreadable body"}`)
		return
	}
	if len(body) > maxBodyBytes {
		writeJSON(w, http.StatusRequestEntityTooLarge, `{"error":"body too large"}`)
		return
	}

	tolerance := h.Tolerance
	if tolerance <= 0 {
		tolerance = coasty.DefaultWebhookTolerance
	}
	now := time.Now()
	if h.Now != nil {
		now = h.Now()
	}
	// Verify the signature over the RAW bytes before parsing anything.
	// VerifySignature is constant-time and rejects malformed headers, wrong
	// secrets, tampered bodies and timestamps outside the tolerance.
	if !coasty.VerifySignature(body, r.Header.Get("Coasty-Signature"), h.Secret, tolerance, now) {
		h.logf("rejected webhook from %s: invalid or stale Coasty-Signature", r.RemoteAddr)
		writeJSON(w, http.StatusUnauthorized, `{"error":"invalid or stale Coasty-Signature"}`)
		return
	}

	var ev Event
	if err := json.Unmarshal(body, &ev); err != nil || ev.Event == "" {
		h.logf("rejected webhook from %s: verified but unparseable payload", r.RemoteAddr)
		writeJSON(w, http.StatusBadRequest, `{"error":"unparseable webhook payload"}`)
		return
	}
	ev.Raw = body

	h.dispatch(ev)
	// 200 fast: the dispatch above only logs / hands off.
	writeJSON(w, http.StatusOK, `{"received":true}`)
}

// dispatch routes the five run.* events. Unknown events are logged and
// acknowledged so new event types never cause retry storms.
func (h *Handler) dispatch(ev Event) {
	switch ev.Event {
	case EventRunAwaitingHuman:
		h.logf("run %s paused for a human (%s) — resume with POST /v1/runs/%s/resume", ev.RunID, ev.Reason, ev.RunID)
	case EventRunSucceeded:
		h.logf("run %s succeeded — verification passed", ev.RunID)
	case EventRunFailed:
		h.logf("run %s failed — check GET /v1/runs/%s for result and error", ev.RunID, ev.RunID)
	case EventRunCancelled:
		h.logf("run %s was cancelled", ev.RunID)
	case EventRunTimedOut:
		h.logf("run %s timed out — it breached its deadline", ev.RunID)
	default:
		h.logf("unknown event %q for run %s — acknowledged and ignored", ev.Event, ev.RunID)
	}
	if h.OnEvent != nil {
		h.OnEvent(ev)
	}
}

func main() {
	var (
		addr   = flag.String("addr", "127.0.0.1:8788", "listen address")
		secret = flag.String("secret", "", "webhook_secret from the run create response (default: COASTY_WEBHOOK_SECRET)")
	)
	flag.Parse()
	if err := run(*addr, *secret); err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}

func run(addr, secret string) error {
	if secret == "" {
		secret = coasty.Env("COASTY_WEBHOOK_SECRET")
	}
	if secret == "" {
		return fmt.Errorf("a webhook secret is required: pass -secret or set COASTY_WEBHOOK_SECRET " +
			"(it is the webhook_secret returned ONCE by POST /v1/runs)")
	}

	mux := http.NewServeMux()
	mux.Handle("/hooks/coasty", &Handler{Secret: secret})
	server := &http.Server{
		Addr:              addr,
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      10 * time.Second,
	}
	fmt.Printf("listening on http://%s/hooks/coasty (cost: $0 — receiving webhooks is free)\n", addr)
	fmt.Println("note: Coasty requires an https webhook_url in production — front this with TLS.")
	return server.ListenAndServe()
}
