// Package coasty is a small, dependency-free client for the Coasty Computer
// Use API (https://coasty.ai/v1). It covers the inference endpoints
// (predict / ground / parse), stateful sessions, task runs with a
// reconnect-safe SSE event stream, webhook signature verification, a cost
// estimator implementing the published pricing table, and a tiny .env
// loader.
//
// Retry policy (matching docs/API_NOTES.md): 429/500/503/504 and transport
// errors are retried with exponential backoff + full jitter (base 500ms,
// cap 8s, max 4 attempts), honoring Retry-After. Other 4xx are never
// retried. POSTs are only retried when they are inherently safe
// (predict/ground/parse: charged-then-refunded on failure) or carry an
// Idempotency-Key.
package coasty

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math/rand/v2"
	"net/http"
	"net/url"
	"strings"
	"time"
)

const (
	defaultTimeout       = 60 * time.Second
	defaultMaxAttempts   = 4
	defaultBackoffBase   = 500 * time.Millisecond
	defaultBackoffCap    = 8 * time.Second
	defaultMaxReconnects = 5
	defaultUserAgent     = "coasty-go-cookbook/0.1.0"
)

// Client is a Coasty API client. Construct it with NewClient; the zero value
// is not usable.
type Client struct {
	apiKey    string
	baseURL   string
	userAgent string

	httpClient *http.Client
	// streamClient shares the transport but has no overall timeout, so SSE
	// streams are not killed after httpClient.Timeout.
	streamClient *http.Client

	maxAttempts   int
	backoffBase   time.Duration
	backoffCap    time.Duration
	maxReconnects int

	// test seams
	sleep     func(ctx context.Context, d time.Duration) error
	randFloat func() float64
}

// Option configures a Client.
type Option func(*Client)

// WithAPIKey sets the API key (default: APIKey() from the environment /
// .env). The raw key is sent in X-API-Key — never prefix it with "Bearer ".
func WithAPIKey(key string) Option { return func(c *Client) { c.apiKey = key } }

// WithBaseURL overrides the base URL (default: BaseURL(), normally
// https://coasty.ai/v1).
func WithBaseURL(u string) Option {
	return func(c *Client) { c.baseURL = strings.TrimRight(u, "/") }
}

// WithHTTPClient supplies a custom *http.Client. Its Timeout applies to
// regular calls; SSE streaming uses a copy with the timeout removed.
func WithHTTPClient(h *http.Client) Option { return func(c *Client) { c.httpClient = h } }

// WithTimeout sets the per-request timeout (default 60s).
func WithTimeout(d time.Duration) Option {
	return func(c *Client) {
		if c.httpClient == nil {
			c.httpClient = &http.Client{}
		}
		c.httpClient.Timeout = d
	}
}

// WithMaxAttempts sets the total attempt budget for retryable requests
// (default 4 = 1 initial + up to 3 retries). Values < 1 are coerced to 1.
func WithMaxAttempts(n int) Option {
	return func(c *Client) {
		if n < 1 {
			n = 1
		}
		c.maxAttempts = n
	}
}

// WithBackoff tunes the retry / SSE-reconnect backoff window (defaults: base
// 500ms, cap 8s). Non-positive values keep the corresponding default. Tests
// pass tiny values so retries never sleep noticeably.
func WithBackoff(base, cap time.Duration) Option {
	return func(c *Client) {
		if base > 0 {
			c.backoffBase = base
		}
		if cap > 0 {
			c.backoffCap = cap
		}
	}
}

// WithUserAgent overrides the User-Agent header.
func WithUserAgent(ua string) Option { return func(c *Client) { c.userAgent = ua } }

// NewClient builds a Client. With no options it reads COASTY_API_KEY /
// COASTY_BASE_URL from the process environment (falling back to the repo
// root .env file) and uses a 60s per-request timeout.
func NewClient(opts ...Option) *Client {
	c := &Client{
		maxAttempts:   defaultMaxAttempts,
		backoffBase:   defaultBackoffBase,
		backoffCap:    defaultBackoffCap,
		maxReconnects: defaultMaxReconnects,
		userAgent:     defaultUserAgent,
		randFloat:     rand.Float64,
		sleep:         sleepCtx,
	}
	for _, opt := range opts {
		opt(c)
	}
	if c.apiKey == "" {
		c.apiKey = APIKey()
	}
	if c.baseURL == "" {
		c.baseURL = strings.TrimRight(BaseURL(), "/")
	}
	if c.httpClient == nil {
		c.httpClient = &http.Client{Timeout: defaultTimeout}
	} else if c.httpClient.Timeout == 0 {
		c.httpClient.Timeout = defaultTimeout
	}
	stream := *c.httpClient
	stream.Timeout = 0
	c.streamClient = &stream
	return c
}

// BaseURL returns the base URL this client targets.
func (c *Client) BaseURL() string { return c.baseURL }

// IsSandbox reports whether the configured key is a sandbox key
// (sk-coasty-test-*), which never bills.
func (c *Client) IsSandbox() bool { return strings.HasPrefix(c.apiKey, sandboxKeyPrefix) }

func sleepCtx(ctx context.Context, d time.Duration) error {
	if d <= 0 {
		return nil
	}
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-t.C:
		return nil
	}
}

// requestSpec describes one logical API call.
type requestSpec struct {
	method         string
	path           string // e.g. "/predict"
	query          url.Values
	body           any    // JSON-marshalled when non-nil
	idempotencyKey string // sent as Idempotency-Key when non-empty
	// retryable marks the call safe to retry: all GET/DELETE, the
	// inherently-safe POSTs (predict/ground/parse), and POSTs carrying an
	// Idempotency-Key.
	retryable bool
}

// do runs the request with the retry policy and decodes the JSON response
// into out (when out is non-nil).
func (c *Client) do(ctx context.Context, spec requestSpec, out any) error {
	var payload []byte
	if spec.body != nil {
		var err error
		payload, err = json.Marshal(spec.body)
		if err != nil {
			return fmt.Errorf("coasty: encoding %s %s body: %w", spec.method, spec.path, err)
		}
	}

	retryable := spec.retryable || spec.idempotencyKey != ""
	var lastErr error
	for attempt := 0; attempt < c.maxAttempts; attempt++ {
		if attempt > 0 {
			if err := c.sleep(ctx, c.retryDelay(attempt-1, lastErr)); err != nil {
				return err
			}
		}

		resp, err := c.send(ctx, spec, payload)
		if err != nil {
			if ctx.Err() != nil {
				return fmt.Errorf("coasty: %s %s: %w", spec.method, spec.path, ctx.Err())
			}
			lastErr = fmt.Errorf("coasty: %s %s: transport error: %w", spec.method, spec.path, err)
			if !retryable {
				return lastErr
			}
			continue
		}

		if resp.StatusCode >= 200 && resp.StatusCode < 300 {
			return decodeJSON(resp, out)
		}

		apiErr := parseAPIError(resp)
		lastErr = apiErr
		if !retryable || !retryableStatus(resp.StatusCode) {
			return apiErr
		}
	}
	return lastErr
}

// send performs a single HTTP attempt.
func (c *Client) send(ctx context.Context, spec requestSpec, payload []byte) (*http.Response, error) {
	u := c.baseURL + spec.path
	if len(spec.query) > 0 {
		u += "?" + spec.query.Encode()
	}
	var body io.Reader
	if payload != nil {
		body = bytes.NewReader(payload)
	}
	req, err := http.NewRequestWithContext(ctx, spec.method, u, body)
	if err != nil {
		return nil, err
	}
	c.setCommonHeaders(req)
	req.Header.Set("Accept", "application/json")
	if payload != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if spec.idempotencyKey != "" {
		req.Header.Set("Idempotency-Key", spec.idempotencyKey)
	}
	return c.httpClient.Do(req)
}

func (c *Client) setCommonHeaders(req *http.Request) {
	if c.apiKey != "" {
		req.Header.Set("X-API-Key", c.apiKey)
	}
	req.Header.Set("User-Agent", c.userAgent)
}

// retryDelay computes the pause before retry number retryIndex (0-based),
// honoring Retry-After from the previous error when present, otherwise full
// jitter: uniform [0, min(cap, base*2^retryIndex)).
func (c *Client) retryDelay(retryIndex int, lastErr error) time.Duration {
	if apiErr, ok := AsAPIError(lastErr); ok && apiErr.RetryAfter > 0 {
		return apiErr.RetryAfter
	}
	return c.backoff(retryIndex)
}

func (c *Client) backoff(retryIndex int) time.Duration {
	window := c.backoffBase
	for i := 0; i < retryIndex && window < c.backoffCap; i++ {
		window *= 2
	}
	if window > c.backoffCap {
		window = c.backoffCap
	}
	return time.Duration(c.randFloat() * float64(window))
}

func retryableStatus(status int) bool {
	switch status {
	case http.StatusTooManyRequests, http.StatusInternalServerError,
		http.StatusServiceUnavailable, http.StatusGatewayTimeout:
		return true
	default:
		return false
	}
}

func decodeJSON(resp *http.Response, out any) error {
	defer resp.Body.Close()
	if out == nil {
		_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, maxErrorBodyBytes))
		return nil
	}
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("coasty: reading response (request_id %s): %w",
			resp.Header.Get("X-Coasty-Request-Id"), err)
	}
	if err := json.Unmarshal(body, out); err != nil {
		return fmt.Errorf("coasty: decoding response (request_id %s): %w",
			resp.Header.Get("X-Coasty-Request-Id"), err)
	}
	return nil
}

// ---------------------------------------------------------------------------
// Core inference
// ---------------------------------------------------------------------------

// Predict calls POST /v1/predict (scope predict, 5 credits + surcharges).
// Inherently safe to retry: failed calls are auto-refunded.
func (c *Client) Predict(ctx context.Context, req *PredictRequest) (*PredictResponse, error) {
	if req == nil {
		return nil, fmt.Errorf("coasty: Predict: nil request")
	}
	var out PredictResponse
	if err := c.do(ctx, requestSpec{
		method: http.MethodPost, path: "/predict", body: req, retryable: true,
	}, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// Ground calls POST /v1/ground (scope ground, 3 credits +1 if HD).
func (c *Client) Ground(ctx context.Context, req *GroundRequest) (*GroundResponse, error) {
	if req == nil {
		return nil, fmt.Errorf("coasty: Ground: nil request")
	}
	var out GroundResponse
	if err := c.do(ctx, requestSpec{
		method: http.MethodPost, path: "/ground", body: req, retryable: true,
	}, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// Parse calls POST /v1/parse (scope parse, free) to turn pyautogui source
// into structured actions.
func (c *Client) Parse(ctx context.Context, code string) (*ParseResponse, error) {
	if code == "" {
		return nil, fmt.Errorf("coasty: Parse: empty code")
	}
	var out ParseResponse
	if err := c.do(ctx, requestSpec{
		method: http.MethodPost, path: "/parse", body: ParseRequest{Code: code}, retryable: true,
	}, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// ---------------------------------------------------------------------------
// Sessions
// ---------------------------------------------------------------------------

// CreateSession calls POST /v1/sessions (scope session, 10 credits one-time).
// Not retried automatically: a duplicate create would leak a paid session.
func (c *Client) CreateSession(ctx context.Context, req *CreateSessionRequest) (*Session, error) {
	if req == nil {
		req = &CreateSessionRequest{}
	}
	var out Session
	if err := c.do(ctx, requestSpec{
		method: http.MethodPost, path: "/sessions", body: req,
	}, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// SessionPredict calls POST /v1/sessions/{id}/predict (4 credits +
// surcharges). Not retried automatically: it advances server-side session
// state.
func (c *Client) SessionPredict(ctx context.Context, sessionID string, req *SessionPredictRequest) (*SessionPredictResponse, error) {
	if sessionID == "" {
		return nil, fmt.Errorf("coasty: SessionPredict: empty session id")
	}
	if req == nil {
		return nil, fmt.Errorf("coasty: SessionPredict: nil request")
	}
	var out SessionPredictResponse
	if err := c.do(ctx, requestSpec{
		method: http.MethodPost,
		path:   "/sessions/" + url.PathEscape(sessionID) + "/predict",
		body:   req,
	}, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// ResetSession calls POST /v1/sessions/{id}/reset (free) to start a fresh
// task on the same session.
func (c *Client) ResetSession(ctx context.Context, sessionID string) (*SessionAck, error) {
	if sessionID == "" {
		return nil, fmt.Errorf("coasty: ResetSession: empty session id")
	}
	var out SessionAck
	if err := c.do(ctx, requestSpec{
		method: http.MethodPost,
		path:   "/sessions/" + url.PathEscape(sessionID) + "/reset",
	}, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// GetSession calls GET /v1/sessions/{id} (free).
func (c *Client) GetSession(ctx context.Context, sessionID string) (*SessionInfo, error) {
	if sessionID == "" {
		return nil, fmt.Errorf("coasty: GetSession: empty session id")
	}
	var out SessionInfo
	if err := c.do(ctx, requestSpec{
		method:    http.MethodGet,
		path:      "/sessions/" + url.PathEscape(sessionID),
		retryable: true,
	}, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// ListSessions calls GET /v1/sessions (free).
func (c *Client) ListSessions(ctx context.Context) (*SessionList, error) {
	var out SessionList
	if err := c.do(ctx, requestSpec{
		method: http.MethodGet, path: "/sessions", retryable: true,
	}, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// DeleteSession calls DELETE /v1/sessions/{id} (free; frees the concurrency
// slot). Idempotent, so it is retried on transient failures.
func (c *Client) DeleteSession(ctx context.Context, sessionID string) (*SessionAck, error) {
	if sessionID == "" {
		return nil, fmt.Errorf("coasty: DeleteSession: empty session id")
	}
	var out SessionAck
	if err := c.do(ctx, requestSpec{
		method:    http.MethodDelete,
		path:      "/sessions/" + url.PathEscape(sessionID),
		retryable: true,
	}, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// ---------------------------------------------------------------------------
// Task runs
// ---------------------------------------------------------------------------

// CreateRun calls POST /v1/runs (scope runs:write). Set
// req.IdempotencyKey to make the create safe to retry; without it the
// request is attempted exactly once. The response carries WebhookSecret
// exactly once when a WebhookURL was set — persist it immediately.
func (c *Client) CreateRun(ctx context.Context, req *CreateRunRequest) (*Run, error) {
	if req == nil {
		return nil, fmt.Errorf("coasty: CreateRun: nil request")
	}
	if req.MachineID == "" || req.Task == "" {
		return nil, fmt.Errorf("coasty: CreateRun: machine_id and task are required")
	}
	var out Run
	if err := c.do(ctx, requestSpec{
		method:         http.MethodPost,
		path:           "/runs",
		body:           req,
		idempotencyKey: req.IdempotencyKey,
	}, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// GetRun calls GET /v1/runs/{id} (scope runs:read).
func (c *Client) GetRun(ctx context.Context, runID string) (*Run, error) {
	if runID == "" {
		return nil, fmt.Errorf("coasty: GetRun: empty run id")
	}
	var out Run
	if err := c.do(ctx, requestSpec{
		method: http.MethodGet, path: "/runs/" + url.PathEscape(runID), retryable: true,
	}, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// ListRuns calls GET /v1/runs (scope runs:read). params may be nil.
func (c *Client) ListRuns(ctx context.Context, params *ListRunsParams) (*RunList, error) {
	q := url.Values{}
	if params != nil {
		if params.Status != "" {
			q.Set("status", string(params.Status))
		}
		if params.Limit > 0 {
			q.Set("limit", fmt.Sprintf("%d", params.Limit))
		}
	}
	var out RunList
	if err := c.do(ctx, requestSpec{
		method: http.MethodGet, path: "/runs", query: q, retryable: true,
	}, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// CancelRun calls POST /v1/runs/{id}/cancel (scope runs:write). Returns the
// run with status "cancelled".
func (c *Client) CancelRun(ctx context.Context, runID string) (*Run, error) {
	if runID == "" {
		return nil, fmt.Errorf("coasty: CancelRun: empty run id")
	}
	var out Run
	if err := c.do(ctx, requestSpec{
		method: http.MethodPost, path: "/runs/" + url.PathEscape(runID) + "/cancel",
	}, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// ResumeRun calls POST /v1/runs/{id}/resume (scope runs:write). Only valid
// while the run status is awaiting_human, otherwise 409 NOT_AWAITING_HUMAN.
// note is optional (<= 2000 chars).
func (c *Client) ResumeRun(ctx context.Context, runID, note string) (*Run, error) {
	if runID == "" {
		return nil, fmt.Errorf("coasty: ResumeRun: empty run id")
	}
	body := map[string]string{}
	if note != "" {
		body["note"] = note
	}
	var out Run
	if err := c.do(ctx, requestSpec{
		method: http.MethodPost, path: "/runs/" + url.PathEscape(runID) + "/resume", body: body,
	}, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// ---------------------------------------------------------------------------
// Misc (free)
// ---------------------------------------------------------------------------

// Models calls GET /v1/models (free).
func (c *Client) Models(ctx context.Context) (*ModelsResponse, error) {
	var out ModelsResponse
	if err := c.do(ctx, requestSpec{
		method: http.MethodGet, path: "/models", retryable: true,
	}, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// Usage calls GET /v1/usage (free). period is optional ("YYYY-MM"; empty =
// current month).
func (c *Client) Usage(ctx context.Context, period string) (*UsageResponse, error) {
	q := url.Values{}
	if period != "" {
		q.Set("period", period)
	}
	var out UsageResponse
	if err := c.do(ctx, requestSpec{
		method: http.MethodGet, path: "/usage", query: q, retryable: true,
	}, &out); err != nil {
		return nil, err
	}
	return &out, nil
}
