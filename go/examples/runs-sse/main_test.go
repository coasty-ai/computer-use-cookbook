package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"regexp"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/coasty-ai/computer-use-cookbook/go/coasty"
)

const testAPIKey = "sk-coasty-test-000000000000000000000000000000000000000000000000"

func newTestClient(t *testing.T, baseURL string) *coasty.Client {
	t.Helper()
	return coasty.NewClient(
		coasty.WithAPIKey(testAPIKey),
		coasty.WithBaseURL(baseURL),
		coasty.WithTimeout(5*time.Second),
		coasty.WithBackoff(time.Millisecond, 2*time.Millisecond),
	)
}

func testCtx(t *testing.T) context.Context {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	t.Cleanup(cancel)
	return ctx
}

func writeSSE(t *testing.T, w http.ResponseWriter, seq int, event, data string) {
	t.Helper()
	if _, err := fmt.Fprintf(w, "id: %d\nevent: %s\ndata: %s\n\n", seq, event, data); err != nil {
		t.Errorf("writing SSE frame: %v", err)
	}
	w.(http.Flusher).Flush()
}

func runJSON(id string, status coasty.RunStatus) string {
	return fmt.Sprintf(`{"id":%q,"object":"agent.run","status":%q,"machine_id":"mch_test_1","task":"t",
		"cua_version":"v3","max_steps":10,"on_awaiting_human":"pause","steps_completed":2,
		"credits_charged":10,"cost_cents":10,"result":null,"error":null,"request_id":"req_run"}`, id, status)
}

// TestNewIdempotencyKey checks shape and uniqueness against the documented
// constraints (<= 128 chars of [A-Za-z0-9_-:]).
func TestNewIdempotencyKey(t *testing.T) {
	valid := regexp.MustCompile(`^[A-Za-z0-9_\-:]+$`)
	k1, err := newIdempotencyKey()
	if err != nil {
		t.Fatal(err)
	}
	k2, err := newIdempotencyKey()
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(k1, "runs-sse-") || len(k1) > 128 || !valid.MatchString(k1) {
		t.Errorf("key %q violates the Idempotency-Key constraints", k1)
	}
	if k1 == k2 {
		t.Error("two generated keys must differ")
	}
}

// TestCreateRunSendsIdempotencyKey: the create step must carry the
// Idempotency-Key HEADER (never a body field).
func TestCreateRunSendsIdempotencyKey(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/runs" {
			t.Errorf("unexpected request: %s %s", r.Method, r.URL.Path)
			http.NotFound(w, r)
			return
		}
		if got := r.Header.Get("Idempotency-Key"); got != "runs-sse-fixed-key" {
			t.Errorf("Idempotency-Key header = %q, want runs-sse-fixed-key", got)
		}
		var body map[string]any
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Errorf("decoding body: %v", err)
		}
		if _, leaked := body["IdempotencyKey"]; leaked {
			t.Error("IdempotencyKey must not appear in the JSON body")
		}
		if body["machine_id"] != "mch_test_1" || body["task"] != "do it" {
			t.Errorf("body = %v", body)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = fmt.Fprint(w, runJSON("run_1", coasty.RunStatusQueued))
	}))
	defer srv.Close()

	run, err := newTestClient(t, srv.URL).CreateRun(testCtx(t), &coasty.CreateRunRequest{
		IdempotencyKey: "runs-sse-fixed-key",
		MachineID:      "mch_test_1",
		Task:           "do it",
		MaxSteps:       10,
	})
	if err != nil {
		t.Fatalf("CreateRun() error = %v", err)
	}
	if run.ID != "run_1" || run.Status != coasty.RunStatusQueued {
		t.Errorf("run = %+v", run)
	}
}

// TestPollUntilTerminalResumesAwaitingHuman: poll mode observes
// queued -> running -> awaiting_human (resume with note) -> running ->
// succeeded.
func TestPollUntilTerminalResumesAwaitingHuman(t *testing.T) {
	var (
		polls   atomic.Int32
		resumes atomic.Int32
		mu      sync.Mutex
		note    string
	)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.Method == http.MethodGet && r.URL.Path == "/runs/run_1":
			status := coasty.RunStatusSucceeded
			switch polls.Add(1) {
			case 1:
				status = coasty.RunStatusQueued
			case 2:
				status = coasty.RunStatusRunning
			case 3:
				status = coasty.RunStatusAwaitingHuman
			case 4:
				if resumes.Load() == 0 {
					t.Error("poll 4 happened before the resume call")
				}
				status = coasty.RunStatusRunning
			}
			if status == coasty.RunStatusAwaitingHuman {
				_, _ = fmt.Fprint(w, `{"id":"run_1","object":"agent.run","status":"awaiting_human","awaiting_human_reason":"captcha on screen","request_id":"req_p"}`)
				return
			}
			_, _ = fmt.Fprint(w, runJSON("run_1", status))
		case r.Method == http.MethodPost && r.URL.Path == "/runs/run_1/resume":
			resumes.Add(1)
			var body map[string]string
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
				t.Errorf("decoding resume body: %v", err)
			}
			mu.Lock()
			note = body["note"]
			mu.Unlock()
			_, _ = fmt.Fprint(w, runJSON("run_1", coasty.RunStatusRunning))
		default:
			t.Errorf("unexpected request: %s %s", r.Method, r.URL.Path)
			http.NotFound(w, r)
		}
	}))
	defer srv.Close()

	var reasons []string
	final, err := PollUntilTerminal(testCtx(t), newTestClient(t, srv.URL), "run_1", PollOptions{
		Interval: time.Millisecond,
		OnAwaitingHuman: func(reason string) (string, bool) {
			reasons = append(reasons, reason)
			return "human took over and solved it", true
		},
	})
	if err != nil {
		t.Fatalf("PollUntilTerminal() error = %v", err)
	}
	if final.Status != coasty.RunStatusSucceeded {
		t.Errorf("final status = %q, want succeeded", final.Status)
	}
	if got := resumes.Load(); got != 1 {
		t.Errorf("resume called %d times, want exactly 1", got)
	}
	mu.Lock()
	defer mu.Unlock()
	if note != "human took over and solved it" {
		t.Errorf("resume note = %q", note)
	}
	if len(reasons) != 1 || reasons[0] != "captcha on screen" {
		t.Errorf("handler reasons = %v", reasons)
	}
}

// TestPollTreatsResumeRaceAsBenign: a 409 NOT_AWAITING_HUMAN on resume must
// not abort polling.
func TestPollTreatsResumeRaceAsBenign(t *testing.T) {
	var polls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.Method == http.MethodGet && r.URL.Path == "/runs/run_1":
			if polls.Add(1) == 1 {
				_, _ = fmt.Fprint(w, `{"id":"run_1","object":"agent.run","status":"awaiting_human","awaiting_human_reason":"2fa","request_id":"req_p2"}`)
				return
			}
			_, _ = fmt.Fprint(w, runJSON("run_1", coasty.RunStatusSucceeded))
		case r.Method == http.MethodPost && r.URL.Path == "/runs/run_1/resume":
			w.WriteHeader(http.StatusConflict)
			_, _ = fmt.Fprint(w, `{"error":{"code":"NOT_AWAITING_HUMAN","message":"already resumed","type":"state_error","request_id":"req_409"}}`)
		default:
			t.Errorf("unexpected request: %s %s", r.Method, r.URL.Path)
			http.NotFound(w, r)
		}
	}))
	defer srv.Close()

	final, err := PollUntilTerminal(testCtx(t), newTestClient(t, srv.URL), "run_1", PollOptions{
		Interval:        time.Millisecond,
		OnAwaitingHuman: func(string) (string, bool) { return "n", true },
	})
	if err != nil {
		t.Fatalf("PollUntilTerminal() error = %v (409 resume race must be benign)", err)
	}
	if final.Status != coasty.RunStatusSucceeded {
		t.Errorf("final status = %q, want succeeded", final.Status)
	}
}

// TestStreamEventsReconnectsAndResumes is the big one: the first SSE
// connection drops after seq 2 without "done"; the client must reconnect
// sending Last-Event-ID: 2; the server then replays seq 2 (which must be
// deduplicated) and continues with awaiting_human -> resume -> billing ->
// status -> done.
func TestStreamEventsReconnectsAndResumes(t *testing.T) {
	var (
		conns   atomic.Int32
		resumed = make(chan struct{})
		mu      sync.Mutex
		note    string
	)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodGet && r.URL.Path == "/runs/run_1/events":
			w.Header().Set("Content-Type", "text/event-stream")
			switch conns.Add(1) {
			case 1:
				if got := r.Header.Get("Last-Event-ID"); got != "" {
					t.Errorf("first connection sent Last-Event-ID %q, want none", got)
				}
				writeSSE(t, w, 1, "status", `{"status":"running"}`)
				writeSSE(t, w, 2, "step", `{"steps_completed":1}`)
				return // drop the connection mid-stream (no "done")
			case 2:
				if got := r.Header.Get("Last-Event-ID"); got != "2" {
					t.Errorf("reconnect sent Last-Event-ID %q, want \"2\"", got)
				}
				// Durable log replay at the cursor: the client must skip it.
				writeSSE(t, w, 2, "step", `{"steps_completed":1}`)
				writeSSE(t, w, 3, "awaiting_human", `{"reason":"needs 2fa code"}`)
				select {
				case <-resumed:
				case <-time.After(5 * time.Second):
					t.Error("timed out waiting for the resume call")
					return
				}
				writeSSE(t, w, 4, "resumed", `{"note":"human helped"}`)
				writeSSE(t, w, 5, "billing", `{"credits_charged":10,"cost_cents":10}`)
				writeSSE(t, w, 6, "status", `{"status":"succeeded"}`)
				writeSSE(t, w, 7, "done", `{"status":"succeeded"}`)
				return
			default:
				t.Error("unexpected third events connection")
				http.Error(w, "too many connections", http.StatusInternalServerError)
				return
			}
		case r.Method == http.MethodPost && r.URL.Path == "/runs/run_1/resume":
			var body map[string]string
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
				t.Errorf("decoding resume body: %v", err)
			}
			mu.Lock()
			note = body["note"]
			mu.Unlock()
			w.Header().Set("Content-Type", "application/json")
			_, _ = fmt.Fprint(w, runJSON("run_1", coasty.RunStatusRunning))
			close(resumed)
		default:
			t.Errorf("unexpected request: %s %s", r.Method, r.URL.Path)
			http.NotFound(w, r)
		}
	}))
	defer srv.Close()

	var seqs []int64
	summary, err := StreamEvents(testCtx(t), newTestClient(t, srv.URL), "run_1", StreamOptions{
		OnAwaitingHuman: func(reason string) (string, bool) {
			if reason != "needs 2fa code" {
				t.Errorf("awaiting_human reason = %q", reason)
			}
			return "2fa entered, continue", true
		},
		OnEvent: func(ev *coasty.RunEvent) { seqs = append(seqs, ev.Seq) },
	})
	if err != nil {
		t.Fatalf("StreamEvents() error = %v", err)
	}

	wantSeqs := []int64{1, 2, 3, 4, 5, 6, 7}
	if len(seqs) != len(wantSeqs) {
		t.Fatalf("delivered seqs = %v, want %v (no loss, no duplicates)", seqs, wantSeqs)
	}
	for i, want := range wantSeqs {
		if seqs[i] != want {
			t.Fatalf("delivered seqs = %v, want %v (no loss, no duplicates)", seqs, wantSeqs)
		}
	}
	if got := conns.Load(); got != 2 {
		t.Errorf("server saw %d event connections, want 2 (one reconnect)", got)
	}
	if summary.Events != 7 || summary.LastEventID != "7" {
		t.Errorf("summary events/lastID = %d/%q, want 7/\"7\"", summary.Events, summary.LastEventID)
	}
	if summary.FinalStatus != "succeeded" {
		t.Errorf("FinalStatus = %q, want succeeded", summary.FinalStatus)
	}
	if summary.BillingCredits != 10 || summary.BillingCostCents != 10 {
		t.Errorf("billing = %d credits / %d cents, want 10/10", summary.BillingCredits, summary.BillingCostCents)
	}
	if summary.Resumed != 1 {
		t.Errorf("Resumed = %d, want 1", summary.Resumed)
	}
	mu.Lock()
	defer mu.Unlock()
	if note != "2fa entered, continue" {
		t.Errorf("resume note = %q", note)
	}
}

// TestStreamEventsHonorsInitialLastEventID: resuming a previously persisted
// cursor sends it on the very first connection.
func TestStreamEventsHonorsInitialLastEventID(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get("Last-Event-ID"); got != "41" {
			t.Errorf("Last-Event-ID = %q, want \"41\"", got)
		}
		w.Header().Set("Content-Type", "text/event-stream")
		writeSSE(t, w, 42, "status", `{"status":"running"}`)
		writeSSE(t, w, 43, "done", `{"status":"succeeded"}`)
	}))
	defer srv.Close()

	summary, err := StreamEvents(testCtx(t), newTestClient(t, srv.URL), "run_1", StreamOptions{
		LastEventID: "41",
	})
	if err != nil {
		t.Fatalf("StreamEvents() error = %v", err)
	}
	if summary.Events != 2 || summary.LastEventID != "43" || summary.FinalStatus != "succeeded" {
		t.Errorf("summary = %+v", summary)
	}
}
