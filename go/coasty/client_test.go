package coasty

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"sync/atomic"
	"testing"
	"time"
)

// testAPIKey is an obviously fake sandbox key ("sk-coasty-test-" + 48 zeros).
const testAPIKey = "sk-coasty-test-000000000000000000000000000000000000000000000000"

// newTestClient builds a client against srv with deterministic retry seams:
// recorded (not real) sleeps and rand pinned to 1.0 so jittered backoff
// equals the full window.
func newTestClient(t *testing.T, handler http.Handler) (*Client, *[]time.Duration) {
	t.Helper()
	srv := httptest.NewServer(handler)
	t.Cleanup(srv.Close)
	return newTestClientForURL(t, srv.URL)
}

func newTestClientForURL(t *testing.T, baseURL string) (*Client, *[]time.Duration) {
	t.Helper()
	c := NewClient(WithAPIKey(testAPIKey), WithBaseURL(baseURL))
	sleeps := &[]time.Duration{}
	c.sleep = func(_ context.Context, d time.Duration) error {
		*sleeps = append(*sleeps, d)
		return nil
	}
	c.randFloat = func() float64 { return 1.0 }
	return c, sleeps
}

type capturedRequest struct {
	Method string
	Path   string
	Query  url.Values
	Header http.Header
	Body   []byte
}

func capture(reqs *[]capturedRequest, status int, responseBody string) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		*reqs = append(*reqs, capturedRequest{
			Method: r.Method,
			Path:   r.URL.Path,
			Query:  r.URL.Query(),
			Header: r.Header.Clone(),
			Body:   body,
		})
		w.Header().Set("X-Coasty-Request-Id", "req_test_1")
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		_, _ = io.WriteString(w, responseBody)
	})
}

func bodyMap(t *testing.T, raw []byte) map[string]any {
	t.Helper()
	if len(raw) == 0 {
		return nil
	}
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatalf("request body is not JSON: %v\nbody: %s", err, raw)
	}
	return m
}

const fakeScreenshot = "aGVsbG8td29ybGQtcGFkZGluZy1wYWRkaW5nLXBhZGRpbmctcGFkZGluZy1wYWRkaW5nLXBhZGRpbmctcGFkZGluZy1wYWRkaW5nLXBhZGRpbmc="

// TestClientMethodContracts asserts, per method, the outbound request shape
// (method, path, query, headers, body fields) and the decoded response.
func TestClientMethodContracts(t *testing.T) {
	ctx := context.Background()

	tests := []struct {
		name       string
		call       func(c *Client) (any, error)
		wantMethod string
		wantPath   string
		wantQuery  url.Values
		wantHeader map[string]string
		response   string
		checkBody  func(t *testing.T, body map[string]any)
		checkOut   func(t *testing.T, out any)
	}{
		{
			name: "Predict",
			call: func(c *Client) (any, error) {
				return c.Predict(ctx, &PredictRequest{
					Screenshot:   fakeScreenshot,
					Instruction:  "Click the login button",
					CUAVersion:   CUAVersionV3,
					ScreenWidth:  1280,
					ScreenHeight: 720,
					MaxActions:   3,
					Tools:        []string{"click", "type_text"},
					Trajectory: []TrajectoryStep{
						{Screenshot: fakeScreenshot, Reasoning: "step 1"},
					},
				})
			},
			wantMethod: http.MethodPost,
			wantPath:   "/predict",
			wantHeader: map[string]string{"Content-Type": "application/json"},
			response: `{"request_id":"req_8f2c1e9a","status":"continue",
				"reasoning":"login form visible",
				"actions":[{"action_type":"click","params":{"x":512,"y":340},"description":"Click the email field"},
				           {"action_type":"type_text","params":{"text":"you@example.com"}}],
				"raw_code":["pyautogui.click(512, 340)"],
				"usage":{"input_tokens":1523,"output_tokens":245,"credits_charged":6,"cost_cents":6}}`,
			checkBody: func(t *testing.T, body map[string]any) {
				wantField(t, body, "screenshot", fakeScreenshot)
				wantField(t, body, "instruction", "Click the login button")
				wantField(t, body, "cua_version", "v3")
				wantField(t, body, "screen_width", float64(1280))
				wantField(t, body, "screen_height", float64(720))
				wantField(t, body, "max_actions", float64(3))
				if _, ok := body["trajectory"].([]any); !ok {
					t.Errorf("trajectory missing or wrong type: %v", body["trajectory"])
				}
			},
			checkOut: func(t *testing.T, out any) {
				resp := out.(*PredictResponse)
				if resp.Status != PredictStatusContinue {
					t.Errorf("status = %q, want continue", resp.Status)
				}
				if resp.RequestID != "req_8f2c1e9a" {
					t.Errorf("request_id = %q", resp.RequestID)
				}
				if len(resp.Actions) != 2 || resp.Actions[0].ActionType != ActionClick {
					t.Fatalf("actions = %+v", resp.Actions)
				}
				if x, ok := resp.Actions[0].IntParam("x"); !ok || x != 512 {
					t.Errorf("actions[0].x = %d, %v", x, ok)
				}
				if text, ok := resp.Actions[1].StringParam("text"); !ok || text != "you@example.com" {
					t.Errorf("actions[1].text = %q, %v", text, ok)
				}
				if resp.Usage.CreditsCharged != 6 || resp.Usage.CostCents != 6 {
					t.Errorf("usage = %+v", resp.Usage)
				}
			},
		},
		{
			name: "Ground",
			call: func(c *Client) (any, error) {
				return c.Ground(ctx, &GroundRequest{
					Screenshot: fakeScreenshot, Element: "the blue Submit button",
					ScreenWidth: 1920, ScreenHeight: 1080,
				})
			},
			wantMethod: http.MethodPost,
			wantPath:   "/ground",
			response:   `{"x":512,"y":340,"usage":{"credits_charged":4,"cost_cents":4}}`,
			checkBody: func(t *testing.T, body map[string]any) {
				wantField(t, body, "element", "the blue Submit button")
				wantField(t, body, "screen_width", float64(1920))
			},
			checkOut: func(t *testing.T, out any) {
				resp := out.(*GroundResponse)
				if resp.X != 512 || resp.Y != 340 {
					t.Errorf("coords = (%d,%d)", resp.X, resp.Y)
				}
			},
		},
		{
			name:       "Parse",
			call:       func(c *Client) (any, error) { return c.Parse(ctx, "pyautogui.click(100, 200)") },
			wantMethod: http.MethodPost,
			wantPath:   "/parse",
			response:   `{"actions":[{"action_type":"click","params":{"x":100,"y":200}}]}`,
			checkBody: func(t *testing.T, body map[string]any) {
				wantField(t, body, "code", "pyautogui.click(100, 200)")
			},
			checkOut: func(t *testing.T, out any) {
				resp := out.(*ParseResponse)
				if len(resp.Actions) != 1 || resp.Actions[0].ActionType != ActionClick {
					t.Errorf("actions = %+v", resp.Actions)
				}
			},
		},
		{
			name: "CreateSession",
			call: func(c *Client) (any, error) {
				return c.CreateSession(ctx, &CreateSessionRequest{
					CUAVersion: CUAVersionV3, ScreenWidth: 1280, ScreenHeight: 720,
					MaxTrajectoryLength: 5, Instructions: "Be precise.",
				})
			},
			wantMethod: http.MethodPost,
			wantPath:   "/sessions",
			response: `{"session_id":"sess_3b9c","cua_version":"v3","screen_size":"1280x720",
				"created_at":"2026-06-01T12:00:00Z","expires_at":"2026-06-01T12:30:00Z"}`,
			checkBody: func(t *testing.T, body map[string]any) {
				wantField(t, body, "max_trajectory_length", float64(5))
				wantField(t, body, "instructions", "Be precise.")
			},
			checkOut: func(t *testing.T, out any) {
				resp := out.(*Session)
				if resp.SessionID != "sess_3b9c" || resp.ScreenSize != "1280x720" {
					t.Errorf("session = %+v", resp)
				}
			},
		},
		{
			name: "SessionPredict",
			call: func(c *Client) (any, error) {
				return c.SessionPredict(ctx, "sess_3b9c", &SessionPredictRequest{
					Screenshot: fakeScreenshot, Instruction: "Book a meeting",
					IncludeReasoning: Bool(false),
				})
			},
			wantMethod: http.MethodPost,
			wantPath:   "/sessions/sess_3b9c/predict",
			response: `{"request_id":"req_1","session_id":"sess_3b9c","step":2,"status":"continue",
				"actions":[{"action_type":"key_combo","params":{"keys":["ctrl","c"]}}],
				"usage":{"credits_charged":4,"cost_cents":4}}`,
			checkBody: func(t *testing.T, body map[string]any) {
				wantField(t, body, "instruction", "Book a meeting")
				wantField(t, body, "include_reasoning", false)
			},
			checkOut: func(t *testing.T, out any) {
				resp := out.(*SessionPredictResponse)
				if resp.Step != 2 || resp.SessionID != "sess_3b9c" {
					t.Errorf("resp = %+v", resp)
				}
				keys, ok := resp.Actions[0].StringsParam("keys")
				if !ok || len(keys) != 2 || keys[0] != "ctrl" {
					t.Errorf("keys = %v, %v", keys, ok)
				}
			},
		},
		{
			name:       "GetSession",
			call:       func(c *Client) (any, error) { return c.GetSession(ctx, "sess_3b9c") },
			wantMethod: http.MethodGet,
			wantPath:   "/sessions/sess_3b9c",
			response: `{"session_id":"sess_3b9c","cua_version":"v3","screen_size":"1920x1080",
				"step_count":4,"created_at":"2026-06-01T12:00:00Z","expires_at":"2026-06-01T12:30:00Z",
				"total_credits_used":16}`,
			checkOut: func(t *testing.T, out any) {
				resp := out.(*SessionInfo)
				if resp.StepCount != 4 || resp.TotalCreditsUsed != 16 {
					t.Errorf("info = %+v", resp)
				}
			},
		},
		{
			name:       "ListSessions",
			call:       func(c *Client) (any, error) { return c.ListSessions(ctx) },
			wantMethod: http.MethodGet,
			wantPath:   "/sessions",
			response:   `{"sessions":[{"session_id":"sess_1"},{"session_id":"sess_2"}]}`,
			checkOut: func(t *testing.T, out any) {
				resp := out.(*SessionList)
				if len(resp.Sessions) != 2 {
					t.Errorf("sessions = %+v", resp.Sessions)
				}
			},
		},
		{
			name:       "ResetSession",
			call:       func(c *Client) (any, error) { return c.ResetSession(ctx, "sess_3b9c") },
			wantMethod: http.MethodPost,
			wantPath:   "/sessions/sess_3b9c/reset",
			response:   `{"status":"ok","session_id":"sess_3b9c"}`,
			checkOut: func(t *testing.T, out any) {
				if ack := out.(*SessionAck); ack.Status != "ok" {
					t.Errorf("ack = %+v", ack)
				}
			},
		},
		{
			name:       "DeleteSession",
			call:       func(c *Client) (any, error) { return c.DeleteSession(ctx, "sess_3b9c") },
			wantMethod: http.MethodDelete,
			wantPath:   "/sessions/sess_3b9c",
			response:   `{"status":"ok","session_id":"sess_3b9c"}`,
			checkOut: func(t *testing.T, out any) {
				if ack := out.(*SessionAck); ack.SessionID != "sess_3b9c" {
					t.Errorf("ack = %+v", ack)
				}
			},
		},
		{
			name: "CreateRun",
			call: func(c *Client) (any, error) {
				return c.CreateRun(ctx, &CreateRunRequest{
					IdempotencyKey:  "order-4821",
					MachineID:       "m_9f2c",
					Task:            "Download the latest invoice as PDF",
					CUAVersion:      CUAVersionV3,
					MaxSteps:        40,
					OnAwaitingHuman: OnAwaitingHumanPause,
					WebhookURL:      "https://example.com/hooks/coasty",
					Metadata:        map[string]any{"team": "finance"},
				})
			},
			wantMethod: http.MethodPost,
			wantPath:   "/runs",
			wantHeader: map[string]string{"Idempotency-Key": "order-4821"},
			response: `{"id":"run_7a1b2c3d","object":"agent.run","status":"queued","machine_id":"m_9f2c",
				"task":"Download the latest invoice as PDF","cua_version":"v3","max_steps":40,
				"on_awaiting_human":"pause","steps_completed":0,"credits_charged":0,"cost_cents":0,
				"result":null,"error":null,"metadata":{"team":"finance"},
				"webhook_url":"https://example.com/hooks/coasty",
				"webhook_secret":"whsec_one_time_value","created_at":"2026-06-01T12:00:00Z",
				"started_at":null,"awaiting_human_since":null,"finished_at":null,"request_id":"req_4f9a2b1c"}`,
			checkBody: func(t *testing.T, body map[string]any) {
				wantField(t, body, "machine_id", "m_9f2c")
				wantField(t, body, "task", "Download the latest invoice as PDF")
				wantField(t, body, "max_steps", float64(40))
				wantField(t, body, "on_awaiting_human", "pause")
				// The idempotency key travels as a HEADER, never a body field
				// (unknown body fields are rejected with 422).
				for _, banned := range []string{"idempotency_key", "IdempotencyKey"} {
					if _, ok := body[banned]; ok {
						t.Errorf("body must not contain %q", banned)
					}
				}
			},
			checkOut: func(t *testing.T, out any) {
				run := out.(*Run)
				if run.ID != "run_7a1b2c3d" || run.Status != RunStatusQueued {
					t.Errorf("run = %+v", run)
				}
				if run.WebhookSecret != "whsec_one_time_value" {
					t.Errorf("webhook_secret = %q (returned once on create)", run.WebhookSecret)
				}
				if run.Status.Terminal() {
					t.Error("queued must not be terminal")
				}
				if run.CreatedAt == nil || run.FinishedAt != nil {
					t.Errorf("timestamps: created=%v finished=%v", run.CreatedAt, run.FinishedAt)
				}
			},
		},
		{
			name:       "GetRun",
			call:       func(c *Client) (any, error) { return c.GetRun(ctx, "run_7a1b2c3d") },
			wantMethod: http.MethodGet,
			wantPath:   "/runs/run_7a1b2c3d",
			response: `{"id":"run_7a1b2c3d","object":"agent.run","status":"succeeded","machine_id":"m_9f2c",
				"task":"t","cua_version":"v3","max_steps":40,"on_awaiting_human":"pause",
				"steps_completed":12,"credits_charged":60,"cost_cents":60,
				"result":{"passed":true,"status":"succeeded","summary":"done","verdict":"pass"},
				"error":null,"webhook_secret":null,"created_at":"2026-06-01T12:00:00Z",
				"started_at":"2026-06-01T12:00:05Z","awaiting_human_since":null,
				"finished_at":"2026-06-01T12:03:00Z"}`,
			checkOut: func(t *testing.T, out any) {
				run := out.(*Run)
				if !run.Status.Terminal() || run.Result == nil || !run.Result.Passed {
					t.Errorf("run = %+v result = %+v", run, run.Result)
				}
				if run.WebhookSecret != "" {
					t.Errorf("webhook_secret must be empty on get, got %q", run.WebhookSecret)
				}
				if run.CostCents != 60 || run.StepsCompleted != 12 {
					t.Errorf("billing fields = %+v", run)
				}
			},
		},
		{
			name: "ListRuns",
			call: func(c *Client) (any, error) {
				return c.ListRuns(ctx, &ListRunsParams{Status: RunStatusRunning, Limit: 5})
			},
			wantMethod: http.MethodGet,
			wantPath:   "/runs",
			wantQuery:  url.Values{"status": {"running"}, "limit": {"5"}},
			response:   `{"object":"list","data":[{"id":"run_1","status":"running"}],"has_more":true,"request_id":"req_l"}`,
			checkOut: func(t *testing.T, out any) {
				list := out.(*RunList)
				if list.Object != "list" || len(list.Data) != 1 || !list.HasMore {
					t.Errorf("list = %+v", list)
				}
			},
		},
		{
			name:       "CancelRun",
			call:       func(c *Client) (any, error) { return c.CancelRun(ctx, "run_1") },
			wantMethod: http.MethodPost,
			wantPath:   "/runs/run_1/cancel",
			response:   `{"id":"run_1","status":"cancelled"}`,
			checkOut: func(t *testing.T, out any) {
				if run := out.(*Run); run.Status != RunStatusCancelled {
					t.Errorf("status = %q", run.Status)
				}
			},
		},
		{
			name:       "ResumeRun",
			call:       func(c *Client) (any, error) { return c.ResumeRun(ctx, "run_1", "Solved the captcha; continue") },
			wantMethod: http.MethodPost,
			wantPath:   "/runs/run_1/resume",
			response:   `{"id":"run_1","status":"running"}`,
			checkBody: func(t *testing.T, body map[string]any) {
				wantField(t, body, "note", "Solved the captcha; continue")
			},
			checkOut: func(t *testing.T, out any) {
				if run := out.(*Run); run.Status != RunStatusRunning {
					t.Errorf("status = %q", run.Status)
				}
			},
		},
		{
			name:       "Models",
			call:       func(c *Client) (any, error) { return c.Models(ctx) },
			wantMethod: http.MethodGet,
			wantPath:   "/models",
			response: `{"models":[{"id":"default","description":"Default model"}],
				"cua_versions":[{"id":"v3","description":"Lean","avg_step_time":"3.5-4s","features":["multi_action"]}],
				"action_types":["click","type_text","key_press","key_combo","scroll","drag","move","wait","done","fail"]}`,
			checkOut: func(t *testing.T, out any) {
				m := out.(*ModelsResponse)
				if len(m.ActionTypes) != 10 || m.CUAVersions[0].ID != CUAVersionV3 {
					t.Errorf("models = %+v", m)
				}
			},
		},
		{
			name:       "Usage",
			call:       func(c *Client) (any, error) { return c.Usage(ctx, "2026-06") },
			wantMethod: http.MethodGet,
			wantPath:   "/usage",
			wantQuery:  url.Values{"period": {"2026-06"}},
			response: `{"period":"2026-06","total_requests":128,"total_credits":540,"total_cost_cents":540,
				"breakdown":{"predict":{"requests":100,"credits":500}},
				"balance":9300,"wallet_balance_cents":9300,"wallet_balance_usd":93.0}`,
			checkOut: func(t *testing.T, out any) {
				u := out.(*UsageResponse)
				if u.TotalCredits != 540 || u.Breakdown["predict"].Credits != 500 || u.WalletBalanceUSD != 93.0 {
					t.Errorf("usage = %+v", u)
				}
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			var reqs []capturedRequest
			c, _ := newTestClient(t, capture(&reqs, http.StatusOK, tt.response))

			out, err := tt.call(c)
			if err != nil {
				t.Fatalf("%s returned error: %v", tt.name, err)
			}
			if len(reqs) != 1 {
				t.Fatalf("expected exactly 1 request, got %d", len(reqs))
			}
			req := reqs[0]
			if req.Method != tt.wantMethod {
				t.Errorf("method = %s, want %s", req.Method, tt.wantMethod)
			}
			if req.Path != tt.wantPath {
				t.Errorf("path = %s, want %s", req.Path, tt.wantPath)
			}
			if got := req.Header.Get("X-API-Key"); got != testAPIKey {
				t.Errorf("X-API-Key = %q, want the raw key (no Bearer prefix)", got)
			}
			if got := req.Header.Get("Authorization"); got != "" {
				t.Errorf("unexpected Authorization header %q alongside X-API-Key", got)
			}
			if tt.wantMethod == http.MethodPost && len(req.Body) > 0 {
				if ct := req.Header.Get("Content-Type"); ct != "application/json" {
					t.Errorf("Content-Type = %q", ct)
				}
			}
			for k, v := range tt.wantHeader {
				if got := req.Header.Get(k); got != v {
					t.Errorf("header %s = %q, want %q", k, got, v)
				}
			}
			if tt.wantQuery != nil {
				for k, want := range tt.wantQuery {
					if got := req.Query[k]; len(got) != 1 || got[0] != want[0] {
						t.Errorf("query %s = %v, want %v", k, got, want)
					}
				}
			}
			if tt.checkBody != nil {
				tt.checkBody(t, bodyMap(t, req.Body))
			}
			if tt.checkOut != nil {
				tt.checkOut(t, out)
			}
		})
	}
}

func wantField(t *testing.T, body map[string]any, key string, want any) {
	t.Helper()
	got, ok := body[key]
	if !ok {
		t.Errorf("body missing field %q", key)
		return
	}
	if got != want {
		t.Errorf("body[%q] = %v (%T), want %v (%T)", key, got, got, want, want)
	}
}

// --------------------------------------------------------------------------
// Retry behaviour
// --------------------------------------------------------------------------

func TestRetryHonorsRetryAfterHeader(t *testing.T) {
	var calls atomic.Int32
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if calls.Add(1) <= 2 {
			w.Header().Set("Retry-After", "2")
			w.Header().Set("X-Coasty-Request-Id", "req_429")
			w.WriteHeader(http.StatusTooManyRequests)
			_, _ = io.WriteString(w, `{"error":{"code":"RATE_LIMITED","message":"slow down","type":"rate_limit_error","request_id":"req_429","retry_after":2}}`)
			return
		}
		_, _ = io.WriteString(w, `{"request_id":"req_ok","status":"done","actions":[],"usage":{}}`)
	})
	c, sleeps := newTestClient(t, handler)

	resp, err := c.Predict(context.Background(), &PredictRequest{Screenshot: fakeScreenshot, Instruction: "x"})
	if err != nil {
		t.Fatalf("Predict: %v", err)
	}
	if resp.Status != PredictStatusDone {
		t.Errorf("status = %q", resp.Status)
	}
	if got := calls.Load(); got != 3 {
		t.Errorf("requests = %d, want 3", got)
	}
	want := []time.Duration{2 * time.Second, 2 * time.Second}
	if len(*sleeps) != len(want) || (*sleeps)[0] != want[0] || (*sleeps)[1] != want[1] {
		t.Errorf("sleeps = %v, want %v (Retry-After honored)", *sleeps, want)
	}
}

func TestRetryBackoffFullJitterWindows(t *testing.T) {
	var calls atomic.Int32
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if calls.Add(1) <= 3 {
			w.WriteHeader(http.StatusInternalServerError)
			_, _ = io.WriteString(w, `{"error":{"code":"INTERNAL_ERROR","message":"boom","type":"server_error","request_id":"req_500"}}`)
			return
		}
		_, _ = io.WriteString(w, `{"x":1,"y":2,"usage":{}}`)
	})
	c, sleeps := newTestClient(t, handler) // randFloat pinned to 1.0

	if _, err := c.Ground(context.Background(), &GroundRequest{Screenshot: fakeScreenshot, Element: "btn"}); err != nil {
		t.Fatalf("Ground: %v", err)
	}
	if got := calls.Load(); got != 4 {
		t.Errorf("requests = %d, want 4 (max attempts)", got)
	}
	want := []time.Duration{500 * time.Millisecond, time.Second, 2 * time.Second}
	if len(*sleeps) != 3 {
		t.Fatalf("sleeps = %v, want 3 entries", *sleeps)
	}
	for i := range want {
		if (*sleeps)[i] != want[i] {
			t.Errorf("sleep[%d] = %v, want %v (full window with rand=1.0)", i, (*sleeps)[i], want[i])
		}
	}
}

func TestRetryJitterIsUniformFractionOfWindow(t *testing.T) {
	c := NewClient(WithAPIKey(testAPIKey), WithBaseURL("http://127.0.0.1:0"))
	c.randFloat = func() float64 { return 0.5 }
	if got, want := c.backoff(0), 250*time.Millisecond; got != want {
		t.Errorf("backoff(0) = %v, want %v", got, want)
	}
	c.randFloat = func() float64 { return 1.0 }
	wantSeq := []time.Duration{
		500 * time.Millisecond, time.Second, 2 * time.Second, 4 * time.Second,
		8 * time.Second, 8 * time.Second, 8 * time.Second, // capped at 8s
	}
	for i, want := range wantSeq {
		if got := c.backoff(i); got != want {
			t.Errorf("backoff(%d) = %v, want %v", i, got, want)
		}
	}
}

func TestRetryExhaustsAttemptsThenReturnsLastError(t *testing.T) {
	var calls atomic.Int32
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.Header().Set("X-Coasty-Request-Id", "req_503")
		w.WriteHeader(http.StatusServiceUnavailable)
		_, _ = io.WriteString(w, `{"error":{"code":"UPSTREAM_UNAVAILABLE","message":"down","type":"server_error","request_id":"req_503"}}`)
	})
	c, sleeps := newTestClient(t, handler)

	_, err := c.Parse(context.Background(), "pyautogui.click(1, 2)")
	if err == nil {
		t.Fatal("expected error")
	}
	if got := calls.Load(); got != 4 {
		t.Errorf("requests = %d, want 4", got)
	}
	if len(*sleeps) != 3 {
		t.Errorf("sleeps = %v, want 3", *sleeps)
	}
	apiErr, ok := AsAPIError(err)
	if !ok || apiErr.Code != CodeUpstreamUnavailable || apiErr.StatusCode != 503 {
		t.Errorf("err = %v", err)
	}
	if apiErr.RequestID != "req_503" {
		t.Errorf("request_id = %q", apiErr.RequestID)
	}
}

func Test402InsufficientCreditsIsNeverRetried(t *testing.T) {
	var calls atomic.Int32
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.Header().Set("X-Coasty-Request-Id", "req_402")
		w.WriteHeader(http.StatusPaymentRequired)
		_, _ = io.WriteString(w, `{"error":{"code":"INSUFFICIENT_CREDITS","message":"Operation needs 20 credits; you have 5.","type":"billing_error","request_id":"req_402","suggestion":"Top up at https://coasty.ai/credits","required":20,"balance":5}}`)
	})
	c, sleeps := newTestClient(t, handler)

	_, err := c.Predict(context.Background(), &PredictRequest{Screenshot: fakeScreenshot, Instruction: "x"})
	if err == nil {
		t.Fatal("expected error")
	}
	if got := calls.Load(); got != 1 {
		t.Errorf("requests = %d, want 1 (402 must not be retried)", got)
	}
	if len(*sleeps) != 0 {
		t.Errorf("sleeps = %v, want none", *sleeps)
	}
	if !IsInsufficientCredits(err) {
		t.Errorf("IsInsufficientCredits = false for %v", err)
	}
	apiErr, _ := AsAPIError(err)
	if apiErr.Required != 20 || apiErr.Balance != 5 {
		t.Errorf("required/balance = %d/%d", apiErr.Required, apiErr.Balance)
	}
	if apiErr.Type != "billing_error" || apiErr.RequestID != "req_402" {
		t.Errorf("type/request_id = %q/%q", apiErr.Type, apiErr.RequestID)
	}
	if apiErr.Extras["suggestion"] != "Top up at https://coasty.ai/credits" {
		t.Errorf("extras = %v", apiErr.Extras)
	}
}

func Test422IsNeverRetried(t *testing.T) {
	var calls atomic.Int32
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.WriteHeader(http.StatusUnprocessableEntity)
		_, _ = io.WriteString(w, `{"error":{"code":"VALIDATION_ERROR","message":"bad field","type":"validation_error","request_id":"req_422","details":[{"loc":["body","task"]}]}}`)
	})
	c, _ := newTestClient(t, handler)

	_, err := c.Predict(context.Background(), &PredictRequest{Screenshot: fakeScreenshot, Instruction: "x"})
	if err == nil {
		t.Fatal("expected error")
	}
	if got := calls.Load(); got != 1 {
		t.Errorf("requests = %d, want 1", got)
	}
	apiErr, _ := AsAPIError(err)
	if apiErr.Code != CodeValidationError {
		t.Errorf("code = %q", apiErr.Code)
	}
	if apiErr.Extras["details"] == nil {
		t.Errorf("details extra missing: %v", apiErr.Extras)
	}
}

func TestUnsafePostIsNotRetriedOn500(t *testing.T) {
	var calls atomic.Int32
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = io.WriteString(w, `{"error":{"code":"INTERNAL_ERROR","message":"boom","type":"server_error","request_id":"req_x"}}`)
	})
	c, sleeps := newTestClient(t, handler)

	// CancelRun is a POST without an Idempotency-Key: exactly one attempt.
	if _, err := c.CancelRun(context.Background(), "run_1"); err == nil {
		t.Fatal("expected error")
	}
	// CreateRun WITHOUT an idempotency key: also exactly one attempt.
	if _, err := c.CreateRun(context.Background(), &CreateRunRequest{MachineID: "m", Task: "t"}); err == nil {
		t.Fatal("expected error")
	}
	if got := calls.Load(); got != 2 {
		t.Errorf("requests = %d, want 2 (no retries for unsafe POSTs)", got)
	}
	if len(*sleeps) != 0 {
		t.Errorf("sleeps = %v, want none", *sleeps)
	}
}

func TestCreateRunWithIdempotencyKeyIsRetried(t *testing.T) {
	var calls atomic.Int32
	var keys []string
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		keys = append(keys, r.Header.Get("Idempotency-Key"))
		if calls.Add(1) == 1 {
			w.WriteHeader(http.StatusServiceUnavailable)
			_, _ = io.WriteString(w, `{"error":{"code":"UPSTREAM_UNAVAILABLE","message":"blip","type":"server_error","request_id":"req_1"}}`)
			return
		}
		_, _ = io.WriteString(w, `{"id":"run_1","object":"agent.run","status":"queued"}`)
	})
	c, sleeps := newTestClient(t, handler)

	run, err := c.CreateRun(context.Background(), &CreateRunRequest{
		IdempotencyKey: "retry-safe-1", MachineID: "m_9f2c", Task: "t",
	})
	if err != nil {
		t.Fatalf("CreateRun: %v", err)
	}
	if run.ID != "run_1" {
		t.Errorf("run = %+v", run)
	}
	if got := calls.Load(); got != 2 {
		t.Errorf("requests = %d, want 2", got)
	}
	if len(*sleeps) != 1 {
		t.Errorf("sleeps = %v, want 1", *sleeps)
	}
	for i, k := range keys {
		if k != "retry-safe-1" {
			t.Errorf("attempt %d Idempotency-Key = %q, want retry-safe-1", i, k)
		}
	}
}

func TestTransportErrorRetriedForSafeMethods(t *testing.T) {
	srv := httptest.NewServer(http.NotFoundHandler())
	srv.Close() // every connection now fails at the transport layer
	c, sleeps := newTestClientForURL(t, srv.URL)

	_, err := c.Predict(context.Background(), &PredictRequest{Screenshot: fakeScreenshot, Instruction: "x"})
	if err == nil {
		t.Fatal("expected transport error")
	}
	if _, isAPI := AsAPIError(err); isAPI {
		t.Errorf("transport failure must not be an APIError: %v", err)
	}
	if len(*sleeps) != 3 {
		t.Errorf("sleeps = %d, want 3 (4 attempts)", len(*sleeps))
	}
}

func TestTransportErrorNotRetriedForUnsafePost(t *testing.T) {
	srv := httptest.NewServer(http.NotFoundHandler())
	srv.Close()
	c, sleeps := newTestClientForURL(t, srv.URL)

	if _, err := c.ResumeRun(context.Background(), "run_1", "go"); err == nil {
		t.Fatal("expected transport error")
	}
	if len(*sleeps) != 0 {
		t.Errorf("sleeps = %v, want none (unsafe POST, single attempt)", *sleeps)
	}
}

func TestEmptyIDsRejectedClientSide(t *testing.T) {
	var calls atomic.Int32
	c, _ := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
	}))
	ctx := context.Background()
	if _, err := c.GetRun(ctx, ""); err == nil {
		t.Error("GetRun(\"\") must fail")
	}
	if _, err := c.DeleteSession(ctx, ""); err == nil {
		t.Error("DeleteSession(\"\") must fail")
	}
	if _, err := c.Parse(ctx, ""); err == nil {
		t.Error("Parse(\"\") must fail")
	}
	if _, err := c.CreateRun(ctx, &CreateRunRequest{}); err == nil {
		t.Error("CreateRun without machine_id/task must fail")
	}
	if got := calls.Load(); got != 0 {
		t.Errorf("no HTTP requests expected, got %d", got)
	}
}

func TestClientIsSandbox(t *testing.T) {
	c := NewClient(WithAPIKey(testAPIKey), WithBaseURL("http://127.0.0.1:0"))
	if !c.IsSandbox() {
		t.Error("sk-coasty-test-* key must be detected as sandbox")
	}
	live := NewClient(WithAPIKey("sk-coasty-live-000000000000000000000000000000000000000000000000"),
		WithBaseURL("http://127.0.0.1:0"))
	if live.IsSandbox() {
		t.Error("live key must not be sandbox")
	}
}
