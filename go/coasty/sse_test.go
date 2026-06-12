package coasty

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
)

func collectSSE(t *testing.T, input string) []SSEEvent {
	t.Helper()
	sc := NewSSEScanner(strings.NewReader(input))
	var events []SSEEvent
	for sc.Next() {
		events = append(events, sc.Event())
	}
	if err := sc.Err(); err != nil {
		t.Fatalf("scanner error: %v", err)
	}
	return events
}

func TestSSEScannerFraming(t *testing.T) {
	input := "" +
		": keepalive comment, must be ignored\n" +
		"id: 42\n" +
		"event: status\n" +
		"data: {\"status\":\"running\"}\n" +
		"\n" +
		"id: 43\n" +
		"event: text\n" +
		"data: first line\n" +
		"data: second line\n" +
		"\n" +
		": another comment\n" +
		"\n" + // blank line with nothing buffered: no event
		"data:no-space-after-colon\n" +
		"\n" +
		"id: 44\r\n" + // CRLF line endings
		"event: step\r\n" +
		"data: {\"steps_completed\":3}\r\n" +
		"\r\n" +
		"retry: 1000\n" + // unknown/ignored field
		"id: 45\n" +
		"event: done\n" +
		"data: {}\n" +
		"\n" +
		"id: 99\n" + // incomplete event at EOF: discarded per spec
		"event: status\n"

	events := collectSSE(t, input)
	want := []SSEEvent{
		{ID: "42", Event: "status", Data: `{"status":"running"}`},
		{ID: "43", Event: "text", Data: "first line\nsecond line"},
		{ID: "", Event: "message", Data: "no-space-after-colon"},
		{ID: "44", Event: "step", Data: `{"steps_completed":3}`},
		{ID: "45", Event: "done", Data: "{}"},
	}
	if len(events) != len(want) {
		t.Fatalf("got %d events %+v, want %d", len(events), events, len(want))
	}
	for i := range want {
		if events[i] != want[i] {
			t.Errorf("event[%d] = %+v, want %+v", i, events[i], want[i])
		}
	}
}

func TestSSEScannerEmptyStream(t *testing.T) {
	if events := collectSSE(t, ""); len(events) != 0 {
		t.Errorf("events = %+v, want none", events)
	}
	if events := collectSSE(t, ": just keepalives\n\n: more\n\n"); len(events) != 0 {
		t.Errorf("events = %+v, want none", events)
	}
}

func writeEvent(w http.ResponseWriter, id int, event, data string) {
	fmt.Fprintf(w, "id: %d\nevent: %s\ndata: %s\n\n", id, event, data)
	if f, ok := w.(http.Flusher); ok {
		f.Flush()
	}
}

// TestStreamRunEventsReconnect drops the first stream mid-way; the client
// must reconnect with Last-Event-ID and deliver every event exactly once,
// even though the server replays an already-seen event.
func TestStreamRunEventsReconnect(t *testing.T) {
	var requests atomic.Int32
	var lastEventIDs []string
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := requests.Add(1)
		lastEventIDs = append(lastEventIDs, r.Header.Get("Last-Event-ID"))
		if r.URL.Path != "/runs/run_7a1b/events" {
			t.Errorf("path = %s", r.URL.Path)
		}
		if got := r.Header.Get("Accept"); got != "text/event-stream" {
			t.Errorf("Accept = %q", got)
		}
		if got := r.Header.Get("X-API-Key"); got != testAPIKey {
			t.Errorf("X-API-Key = %q", got)
		}
		w.Header().Set("Content-Type", "text/event-stream")
		switch n {
		case 1:
			writeEvent(w, 1, "status", `{"status":"running"}`)
			writeEvent(w, 2, "step", `{"steps_completed":1}`)
			return // connection drops without "done"
		default:
			// Server misbehaves and replays seq 2: the client must skip it.
			writeEvent(w, 2, "step", `{"steps_completed":1}`)
			writeEvent(w, 3, "billing", `{"credits_charged":5,"cost_cents":5}`)
			writeEvent(w, 4, "done", `{"status":"succeeded"}`)
		}
	})
	srv := httptest.NewServer(handler)
	t.Cleanup(srv.Close)
	c, sleeps := newTestClientForURL(t, srv.URL)

	ctx := context.Background()
	stream, err := c.StreamRunEvents(ctx, "run_7a1b", nil)
	if err != nil {
		t.Fatalf("StreamRunEvents: %v", err)
	}
	defer stream.Close()

	var got []RunEvent
	for {
		ev, err := stream.Next(ctx)
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			t.Fatalf("Next: %v", err)
		}
		got = append(got, *ev)
	}

	wantSeqs := []int64{1, 2, 3, 4}
	wantTypes := []RunEventType{RunEventStatus, RunEventStep, RunEventBilling, RunEventDone}
	if len(got) != len(wantSeqs) {
		t.Fatalf("events = %+v, want seqs %v (no loss, no duplicates)", got, wantSeqs)
	}
	for i := range wantSeqs {
		if got[i].Seq != wantSeqs[i] || got[i].Type != wantTypes[i] {
			t.Errorf("event[%d] = seq %d type %s, want seq %d type %s",
				i, got[i].Seq, got[i].Type, wantSeqs[i], wantTypes[i])
		}
	}
	if requests.Load() != 2 {
		t.Errorf("requests = %d, want 2", requests.Load())
	}
	if len(lastEventIDs) != 2 || lastEventIDs[0] != "" || lastEventIDs[1] != "2" {
		t.Errorf("Last-Event-ID per request = %v, want [\"\" \"2\"]", lastEventIDs)
	}
	if stream.LastEventID() != "4" {
		t.Errorf("LastEventID = %q, want 4", stream.LastEventID())
	}
	if len(*sleeps) != 1 {
		t.Errorf("reconnect sleeps = %v, want exactly 1", *sleeps)
	}

	// After done, the stream stays terminated and opens no new connections.
	if _, err := stream.Next(ctx); !errors.Is(err, io.EOF) {
		t.Errorf("Next after done = %v, want io.EOF", err)
	}
	if requests.Load() != 2 {
		t.Errorf("requests after done = %d, want 2 (no reconnect)", requests.Load())
	}
}

func TestStreamRunEventsInitialLastEventID(t *testing.T) {
	var headerSeen string
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		headerSeen = r.Header.Get("Last-Event-ID")
		w.Header().Set("Content-Type", "text/event-stream")
		writeEvent(w, 5, "status", `{"status":"running"}`) // stale replay: skipped
		writeEvent(w, 6, "resumed", `{}`)
		writeEvent(w, 7, "done", `{}`)
	})
	srv := httptest.NewServer(handler)
	t.Cleanup(srv.Close)
	c, _ := newTestClientForURL(t, srv.URL)

	ctx := context.Background()
	stream, err := c.StreamRunEvents(ctx, "run_1", &StreamOptions{LastEventID: "5"})
	if err != nil {
		t.Fatalf("StreamRunEvents: %v", err)
	}
	defer stream.Close()
	if headerSeen != "5" {
		t.Errorf("first request Last-Event-ID = %q, want 5", headerSeen)
	}

	var seqs []int64
	for {
		ev, err := stream.Next(ctx)
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			t.Fatalf("Next: %v", err)
		}
		seqs = append(seqs, ev.Seq)
	}
	if len(seqs) != 2 || seqs[0] != 6 || seqs[1] != 7 {
		t.Errorf("seqs = %v, want [6 7] (seq 5 already seen)", seqs)
	}
}

func TestStreamRunEventsGivesUpAfterMaxReconnects(t *testing.T) {
	var requests atomic.Int32
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests.Add(1)
		w.Header().Set("Content-Type", "text/event-stream")
		// Closes immediately without ever sending "done".
	})
	srv := httptest.NewServer(handler)
	t.Cleanup(srv.Close)
	c, _ := newTestClientForURL(t, srv.URL)

	ctx := context.Background()
	stream, err := c.StreamRunEvents(ctx, "run_1", &StreamOptions{MaxReconnects: 2})
	if err != nil {
		t.Fatalf("StreamRunEvents: %v", err)
	}
	defer stream.Close()

	_, err = stream.Next(ctx)
	if err == nil || errors.Is(err, io.EOF) {
		t.Fatalf("Next = %v, want a reconnect-exhausted error", err)
	}
	if !strings.Contains(err.Error(), "reconnect") {
		t.Errorf("error should mention reconnects: %v", err)
	}
	if got := requests.Load(); got != 3 { // initial + 2 reconnects
		t.Errorf("requests = %d, want 3", got)
	}
}

func TestStreamRunEventsErrorStatus(t *testing.T) {
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Coasty-Request-Id", "req_404")
		w.WriteHeader(http.StatusNotFound)
		_, _ = io.WriteString(w, `{"error":{"code":"RUN_NOT_FOUND","message":"no such run","type":"not_found_error","request_id":"req_404"}}`)
	})
	srv := httptest.NewServer(handler)
	t.Cleanup(srv.Close)
	c, _ := newTestClientForURL(t, srv.URL)

	_, err := c.StreamRunEvents(context.Background(), "run_missing", nil)
	apiErr, ok := AsAPIError(err)
	if !ok || apiErr.Code != CodeRunNotFound || apiErr.RequestID != "req_404" {
		t.Fatalf("err = %v, want RUN_NOT_FOUND with request id", err)
	}
}

func TestStreamRunEventsEmptyRunID(t *testing.T) {
	c, _ := newTestClientForURL(t, "http://127.0.0.1:0")
	if _, err := c.StreamRunEvents(context.Background(), "", nil); err == nil {
		t.Error("empty run id must fail before any request")
	}
}
