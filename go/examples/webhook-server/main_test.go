package main

import (
	"bytes"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/coasty-ai/computer-use-cookbook/go/coasty"
)

// Shared cross-language HMAC test vectors (docs/API_NOTES.md §Test vectors).
const (
	v1Secret = "whsec_test_secret_123"
	v1T      = "1750000000"
	v1Body   = `{"event":"run.succeeded","run_id":"run_123","status":"succeeded"}`
	v1Sig    = "5f70978eab52dbf5838da76e5eb6c6c465560ce8e746ed8e6113c159d8bbb2d4"

	v2Secret = "whsec_other_secret_456"
	v2T      = "1750000300"
	v2Body   = `{"event":"run.awaiting_human","run_id":"run_456","reason":"captcha"}`
	v2Sig    = "844504f42b7498094a83cedd7e050fc2f7fa32593b0814cc514c4be52a932e63"
)

// pinnedNow is the "now" the vector timestamps are validated against.
var pinnedNow = time.Unix(1750000000, 0)

// newServer starts the example handler on a real loopback listener and
// returns the server plus a channel-free capture of dispatched events.
func newServer(t *testing.T, secret string, now time.Time) (*httptest.Server, *[]Event, *bytes.Buffer) {
	t.Helper()
	var (
		events []Event
		logBuf bytes.Buffer
	)
	h := &Handler{
		Secret:  secret,
		Now:     func() time.Time { return now },
		Log:     &logBuf,
		OnEvent: func(ev Event) { events = append(events, ev) },
	}
	srv := httptest.NewServer(h) // binds 127.0.0.1
	t.Cleanup(srv.Close)
	return srv, &events, &logBuf
}

// post sends a real HTTP request over loopback with the given signature
// header and returns status code + response body.
func post(t *testing.T, url, body, signature string) (int, string) {
	t.Helper()
	req, err := http.NewRequest(http.MethodPost, url, strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Content-Type", "application/json")
	if signature != "" {
		req.Header.Set("Coasty-Signature", signature)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("POST %s: %v", url, err)
	}
	defer resp.Body.Close()
	b, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatal(err)
	}
	return resp.StatusCode, string(b)
}

// TestVectorMatrix runs the full shared HMAC vector matrix from
// docs/API_NOTES.md against the live loopback server.
func TestVectorMatrix(t *testing.T) {
	tamperedBody := strings.Replace(v1Body, "run_123", "run_124", 1) // flip body bytes

	tests := []struct {
		name       string
		secret     string
		now        time.Time
		body       string
		header     string
		wantStatus int
	}{
		{
			name:   "vector 1 valid",
			secret: v1Secret, now: pinnedNow,
			body:   v1Body,
			header: "t=" + v1T + ",v1=" + v1Sig, wantStatus: http.StatusOK,
		},
		{
			name:   "vector 2 valid second key",
			secret: v2Secret, now: time.Unix(1750000300, 0),
			body:   v2Body,
			header: "t=" + v2T + ",v1=" + v2Sig, wantStatus: http.StatusOK,
		},
		{
			name:   "vector 2 within tolerance of vector-1 now",
			secret: v2Secret, now: pinnedNow, // 300s skew == tolerance boundary, still valid
			body:   v2Body,
			header: "t=" + v2T + ",v1=" + v2Sig, wantStatus: http.StatusOK,
		},
		{
			name:   "negative a: tampered body byte",
			secret: v1Secret, now: pinnedNow,
			body:   tamperedBody,
			header: "t=" + v1T + ",v1=" + v1Sig, wantStatus: http.StatusUnauthorized,
		},
		{
			name:   "negative b: same sig but t outside +-300s of now",
			secret: v1Secret, now: pinnedNow.Add(301 * time.Second),
			body:   v1Body,
			header: "t=" + v1T + ",v1=" + v1Sig, wantStatus: http.StatusUnauthorized,
		},
		{
			name:   "negative b': stale re-signed payload",
			secret: v1Secret, now: pinnedNow,
			body:   v1Body,
			header: coasty.SignWebhookPayload([]byte(v1Body), v1Secret, pinnedNow.Add(-10*time.Minute)),
			// HMAC is valid for the old t, but t is outside the window.
			wantStatus: http.StatusUnauthorized,
		},
		{
			name:   "negative c: malformed header missing v1=",
			secret: v1Secret, now: pinnedNow,
			body:   v1Body,
			header: "t=" + v1T, wantStatus: http.StatusUnauthorized,
		},
		{
			name:   "negative c: malformed header missing t=",
			secret: v1Secret, now: pinnedNow,
			body:   v1Body,
			header: "v1=" + v1Sig, wantStatus: http.StatusUnauthorized,
		},
		{
			name:   "negative c: garbage header",
			secret: v1Secret, now: pinnedNow,
			body:   v1Body,
			header: "not-a-signature", wantStatus: http.StatusUnauthorized,
		},
		{
			name:   "negative c: missing header entirely",
			secret: v1Secret, now: pinnedNow,
			body:   v1Body,
			header: "", wantStatus: http.StatusUnauthorized,
		},
		{
			name:   "negative d: signed with vector 2's secret",
			secret: v1Secret, now: pinnedNow,
			body:       v1Body,
			header:     coasty.SignWebhookPayload([]byte(v1Body), v2Secret, pinnedNow),
			wantStatus: http.StatusUnauthorized,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			srv, events, _ := newServer(t, tt.secret, tt.now)
			status, body := post(t, srv.URL, tt.body, tt.header)
			if status != tt.wantStatus {
				t.Fatalf("status = %d (body %s), want %d", status, body, tt.wantStatus)
			}
			if tt.wantStatus == http.StatusOK {
				if len(*events) != 1 {
					t.Fatalf("dispatched %d events, want 1", len(*events))
				}
			} else if len(*events) != 0 {
				t.Errorf("rejected request must never dispatch, got %v", *events)
			}
		})
	}
}

// TestDispatchesAllFiveRunEvents signs one payload per documented run.*
// event and checks each is parsed, dispatched and logged distinctly.
func TestDispatchesAllFiveRunEvents(t *testing.T) {
	const secret = "whsec_dispatch_secret"
	srv, events, logBuf := newServer(t, secret, pinnedNow)

	cases := []struct {
		event   string
		body    string
		wantLog string
	}{
		{EventRunAwaitingHuman, `{"event":"run.awaiting_human","run_id":"run_a","reason":"captcha"}`, "paused for a human"},
		{EventRunSucceeded, `{"event":"run.succeeded","run_id":"run_b","status":"succeeded"}`, "succeeded"},
		{EventRunFailed, `{"event":"run.failed","run_id":"run_c","status":"failed"}`, "failed"},
		{EventRunCancelled, `{"event":"run.cancelled","run_id":"run_d","status":"cancelled"}`, "cancelled"},
		{EventRunTimedOut, `{"event":"run.timed_out","run_id":"run_e","status":"timed_out"}`, "timed out"},
	}
	for _, c := range cases {
		sig := coasty.SignWebhookPayload([]byte(c.body), secret, pinnedNow)
		status, respBody := post(t, srv.URL, c.body, sig)
		if status != http.StatusOK {
			t.Fatalf("%s: status = %d (%s), want 200", c.event, status, respBody)
		}
		if !strings.Contains(respBody, `"received":true`) {
			t.Errorf("%s: response body = %s", c.event, respBody)
		}
	}

	if len(*events) != len(cases) {
		t.Fatalf("dispatched %d events, want %d", len(*events), len(cases))
	}
	for i, c := range cases {
		got := (*events)[i]
		if got.Event != c.event {
			t.Errorf("event[%d] = %q, want %q", i, got.Event, c.event)
		}
		if got.RunID == "" || !strings.Contains(string(got.Raw), got.RunID) {
			t.Errorf("event[%d] run_id/raw mismatch: %+v", i, got)
		}
		if !strings.Contains(logBuf.String(), c.wantLog) {
			t.Errorf("log missing %q for %s:\n%s", c.wantLog, c.event, logBuf.String())
		}
	}
	if !strings.Contains(logBuf.String(), "captcha") {
		t.Errorf("awaiting_human reason should be logged:\n%s", logBuf.String())
	}
}

// TestUnknownEventIsAcknowledged: verified-but-unknown events return 200 so
// future event types never cause retry storms.
func TestUnknownEventIsAcknowledged(t *testing.T) {
	const secret = "whsec_unknown"
	srv, events, logBuf := newServer(t, secret, pinnedNow)
	body := `{"event":"run.paused_for_coffee","run_id":"run_z"}`
	status, _ := post(t, srv.URL, body, coasty.SignWebhookPayload([]byte(body), secret, pinnedNow))
	if status != http.StatusOK {
		t.Fatalf("status = %d, want 200", status)
	}
	if len(*events) != 1 || (*events)[0].Event != "run.paused_for_coffee" {
		t.Errorf("events = %v", *events)
	}
	if !strings.Contains(logBuf.String(), "unknown event") {
		t.Errorf("unknown events must be logged:\n%s", logBuf.String())
	}
}

// TestVerifiedButMalformedJSONIs400: the signature can verify while the
// payload is still not a usable event.
func TestVerifiedButMalformedJSONIs400(t *testing.T) {
	const secret = "whsec_badjson"
	srv, events, _ := newServer(t, secret, pinnedNow)
	for _, body := range []string{"not json at all", `{"run_id":"missing event field"}`} {
		status, _ := post(t, srv.URL, body, coasty.SignWebhookPayload([]byte(body), secret, pinnedNow))
		if status != http.StatusBadRequest {
			t.Errorf("body %q: status = %d, want 400", body, status)
		}
	}
	if len(*events) != 0 {
		t.Errorf("malformed payloads must not dispatch, got %v", *events)
	}
}

// TestMethodNotAllowed: webhooks are POSTs.
func TestMethodNotAllowed(t *testing.T) {
	srv, _, _ := newServer(t, "whsec_x", pinnedNow)
	resp, err := http.Get(srv.URL)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusMethodNotAllowed {
		t.Errorf("GET status = %d, want 405", resp.StatusCode)
	}
	if got := resp.Header.Get("Allow"); got != http.MethodPost {
		t.Errorf("Allow = %q, want POST", got)
	}
}

// TestSecretsNeverLogged: neither the secret nor the signature may appear
// in the request log, accepted or rejected.
func TestSecretsNeverLogged(t *testing.T) {
	srv, _, logBuf := newServer(t, v1Secret, pinnedNow)
	post(t, srv.URL, v1Body, "t="+v1T+",v1="+v1Sig)                   // accepted
	post(t, srv.URL, v1Body, "t="+v1T+",v1="+strings.Repeat("0", 64)) // rejected
	log := logBuf.String()
	if strings.Contains(log, v1Secret) || strings.Contains(log, v1Sig) {
		t.Errorf("log leaked the secret or signature:\n%s", log)
	}
}

// TestRunRequiresSecret: the CLI refuses to start without a secret.
func TestRunRequiresSecret(t *testing.T) {
	t.Setenv("COASTY_WEBHOOK_SECRET", "")
	if err := run("127.0.0.1:0", ""); err == nil || !strings.Contains(err.Error(), "secret") {
		t.Errorf("run() without a secret = %v, want a secret-required error", err)
	}
}

// sanity check on the helper used across negative cases: SignWebhookPayload
// must reproduce vector 1 exactly.
func TestSignWebhookPayloadMatchesVector1(t *testing.T) {
	got := coasty.SignWebhookPayload([]byte(v1Body), v1Secret, pinnedNow)
	want := fmt.Sprintf("t=%s,v1=%s", v1T, v1Sig)
	if got != want {
		t.Errorf("SignWebhookPayload = %q, want %q", got, want)
	}
}
