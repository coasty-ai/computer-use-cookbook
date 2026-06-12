package coasty

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
)

// SSEEvent is one parsed Server-Sent Events frame.
type SSEEvent struct {
	ID    string // "id:" field (the seq cursor on run streams)
	Event string // "event:" field ("message" when absent, per the SSE spec)
	Data  string // "data:" lines joined with "\n"
}

// SSEScanner incrementally parses an SSE byte stream:
//
//	id: 42
//	event: status
//	data: {"status":"running"}
//
// Events are separated by a blank line; multiple data: lines are joined
// with "\n"; comment lines (leading ":") are ignored. Use it like
// bufio.Scanner: for sc.Next() { ev := sc.Event() }; err := sc.Err().
type SSEScanner struct {
	scanner *bufio.Scanner
	current SSEEvent
	err     error
	first   bool
}

const sseMaxLineBytes = 4 << 20 // tool_result payloads can be large

// NewSSEScanner wraps r in an SSE frame parser.
func NewSSEScanner(r io.Reader) *SSEScanner {
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 0, 64*1024), sseMaxLineBytes)
	return &SSEScanner{scanner: sc, first: true}
}

// Next advances to the next complete event. It returns false at end of
// stream or on read error (check Err).
func (s *SSEScanner) Next() bool {
	if s.err != nil {
		return false
	}
	var ev SSEEvent
	dataLines := make([]string, 0, 1)
	dataSeen := false

	for s.scanner.Scan() {
		line := strings.TrimSuffix(s.scanner.Text(), "\r")
		if s.first {
			line = strings.TrimPrefix(line, "\ufeff") // UTF-8 BOM
			s.first = false
		}

		if line == "" { // dispatch boundary
			if !dataSeen {
				ev = SSEEvent{} // nothing buffered (e.g. after a comment) — keep reading
				continue
			}
			ev.Data = strings.Join(dataLines, "\n")
			if ev.Event == "" {
				ev.Event = "message"
			}
			s.current = ev
			return true
		}
		if strings.HasPrefix(line, ":") { // comment / keepalive
			continue
		}

		field, value, found := strings.Cut(line, ":")
		if !found {
			field, value = line, "" // "field" line with no colon: empty value
		}
		value = strings.TrimPrefix(value, " ")
		switch field {
		case "id":
			if !strings.Contains(value, "\x00") {
				ev.ID = value
			}
		case "event":
			ev.Event = value
		case "data":
			dataLines = append(dataLines, value)
			dataSeen = true
		default:
			// "retry" and unknown fields are ignored.
		}
	}
	// EOF (or read error): per the SSE spec an incomplete event is discarded.
	s.err = s.scanner.Err()
	return false
}

// Event returns the event read by the last successful Next.
func (s *SSEScanner) Event() SSEEvent { return s.current }

// Err returns the first read error (nil on clean EOF).
func (s *SSEScanner) Err() error { return s.err }

// ---------------------------------------------------------------------------
// Reconnecting run event stream
// ---------------------------------------------------------------------------

// StreamOptions configures StreamRunEvents.
type StreamOptions struct {
	// LastEventID resumes after a previously-seen seq (sent as the
	// Last-Event-ID header on the first connection too).
	LastEventID string
	// MaxReconnects bounds consecutive reconnect attempts that make no
	// progress (default 5).
	MaxReconnects int
}

// RunEventStream reads GET /v1/runs/{id}/events, transparently reconnecting
// with Last-Event-ID when the connection drops before the "done" event.
// Events the server replays at or before the last seen seq are skipped, so
// callers observe each event exactly once. The stream ends cleanly
// (io.EOF) after the "done" event.
type RunEventStream struct {
	client *Client
	runID  string

	lastEventID string
	lastSeq     int64
	haveSeq     bool

	resp    *http.Response
	scanner *SSEScanner

	done          bool
	maxReconnects int
	consecFails   int
}

// StreamRunEvents opens the SSE event stream of a run (scope runs:read).
// Close the stream when done. opts may be nil.
func (c *Client) StreamRunEvents(ctx context.Context, runID string, opts *StreamOptions) (*RunEventStream, error) {
	if runID == "" {
		return nil, fmt.Errorf("coasty: StreamRunEvents: empty run id")
	}
	s := &RunEventStream{
		client:        c,
		runID:         runID,
		maxReconnects: c.maxReconnects,
	}
	if opts != nil {
		s.lastEventID = opts.LastEventID
		if opts.MaxReconnects > 0 {
			s.maxReconnects = opts.MaxReconnects
		}
		if seq, err := strconv.ParseInt(opts.LastEventID, 10, 64); err == nil && opts.LastEventID != "" {
			s.lastSeq, s.haveSeq = seq, true
		}
	}
	if err := s.connect(ctx); err != nil {
		return nil, err
	}
	return s, nil
}

// LastEventID returns the id of the last delivered event — persist it to
// resume a stream across process restarts.
func (s *RunEventStream) LastEventID() string { return s.lastEventID }

// Close releases the underlying connection. Safe to call multiple times.
func (s *RunEventStream) Close() error {
	if s.resp != nil {
		err := s.resp.Body.Close()
		s.resp, s.scanner = nil, nil
		return err
	}
	return nil
}

func (s *RunEventStream) connect(ctx context.Context) error {
	u := s.client.baseURL + "/runs/" + url.PathEscape(s.runID) + "/events"
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return fmt.Errorf("coasty: StreamRunEvents: %w", err)
	}
	s.client.setCommonHeaders(req)
	req.Header.Set("Accept", "text/event-stream")
	req.Header.Set("Cache-Control", "no-store")
	if s.lastEventID != "" {
		req.Header.Set("Last-Event-ID", s.lastEventID)
	}
	resp, err := s.client.streamClient.Do(req)
	if err != nil {
		return fmt.Errorf("coasty: StreamRunEvents: transport error: %w", err)
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return parseAPIError(resp) // reads + closes body
	}
	s.resp = resp
	s.scanner = NewSSEScanner(resp.Body)
	return nil
}

// Next returns the next run event. After the "done" event has been
// delivered, Next returns io.EOF. Mid-stream disconnects trigger automatic
// reconnection with Last-Event-ID; reconnect attempts that make no progress
// are bounded by MaxReconnects.
func (s *RunEventStream) Next(ctx context.Context) (*RunEvent, error) {
	for {
		if s.done {
			return nil, io.EOF
		}
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		if s.scanner == nil {
			if err := s.reconnect(ctx); err != nil {
				return nil, err
			}
		}

		if s.scanner.Next() {
			frame := s.scanner.Event()
			seq, seqErr := strconv.ParseInt(frame.ID, 10, 64)
			if frame.ID != "" && seqErr == nil {
				if s.haveSeq && seq <= s.lastSeq {
					continue // server replayed an already-delivered event
				}
				s.lastSeq, s.haveSeq = seq, true
				s.lastEventID = frame.ID
			} else if frame.ID != "" {
				s.lastEventID = frame.ID
			}
			s.consecFails = 0
			ev := &RunEvent{Seq: seq, Type: RunEventType(frame.Event), Data: []byte(frame.Data)}
			if ev.Type == RunEventDone {
				s.done = true
				_ = s.Close()
			}
			return ev, nil
		}

		// Stream ended without "done": reconnect and resume.
		readErr := s.scanner.Err()
		_ = s.Close()
		s.consecFails++
		if s.consecFails > s.maxReconnects {
			if readErr == nil {
				readErr = io.ErrUnexpectedEOF
			}
			return nil, fmt.Errorf("coasty: StreamRunEvents: stream for run %s ended before done after %d reconnect attempts: %w",
				s.runID, s.maxReconnects, readErr)
		}
		if err := s.client.sleep(ctx, s.client.backoff(s.consecFails-1)); err != nil {
			return nil, err
		}
	}
}

// reconnect re-opens the stream, retrying transient failures within the
// same consecutive-failure budget as dropped streams.
func (s *RunEventStream) reconnect(ctx context.Context) error {
	for {
		err := s.connect(ctx)
		if err == nil {
			return nil
		}
		apiErr, isAPI := AsAPIError(err)
		transient := !isAPI || retryableStatus(apiErr.StatusCode)
		if !transient {
			return err
		}
		if ctx.Err() != nil {
			return err
		}
		s.consecFails++
		if s.consecFails > s.maxReconnects {
			return err
		}
		delay := s.client.backoff(s.consecFails - 1)
		if isAPI && apiErr.RetryAfter > 0 {
			delay = apiErr.RetryAfter
		}
		if serr := s.client.sleep(ctx, delay); serr != nil {
			return serr
		}
	}
}
