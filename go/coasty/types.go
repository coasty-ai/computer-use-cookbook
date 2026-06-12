package coasty

import (
	"encoding/json"
	"time"
)

// CUAVersion selects the inference engine on /predict, /sessions and /runs.
type CUAVersion string

const (
	// CUAVersionV1 is the baseline engine (single action per call,
	// reflection). Carries a +3 credit surcharge per request.
	CUAVersionV1 CUAVersion = "v1"
	// CUAVersionV3 is the lean default engine (multi-action per call).
	CUAVersionV3 CUAVersion = "v3"
	// CUAVersionV4 is the autonomous engine with a verifier (pro+ tiers).
	CUAVersionV4 CUAVersion = "v4"
)

// PredictStatus is the status field of a predict / session-predict response.
type PredictStatus string

const (
	PredictStatusContinue PredictStatus = "continue"
	PredictStatusDone     PredictStatus = "done"
	PredictStatusFail     PredictStatus = "fail"
)

// ActionType enumerates the canonical action types from the reference table.
//
// NOTE (documented discrepancy): the "local automation" docs section shows an
// alternate param spelling for several types (key_press {keys}, wait
// {seconds}, scroll {clicks}, drag {x1,y1,x2,y2}, plus a "raw" type).
// Executors must be defensive and accept both shapes; use the typed getters
// on Action and fall back across spellings.
type ActionType string

const (
	ActionClick    ActionType = "click"     // {x, y}
	ActionTypeText ActionType = "type_text" // {text}
	ActionKeyPress ActionType = "key_press" // {key} (alt: {keys: [...]})
	ActionKeyCombo ActionType = "key_combo" // {keys: [...]}
	ActionScroll   ActionType = "scroll"    // {x, y, direction, amount} (alt: signed {clicks})
	ActionDrag     ActionType = "drag"      // {from_x, from_y, to_x, to_y} (alt: {x1,y1,x2,y2})
	ActionMove     ActionType = "move"      // {x, y}
	ActionWait     ActionType = "wait"      // {ms} (alt: {seconds})
	ActionDone     ActionType = "done"      // {}
	ActionFail     ActionType = "fail"      // {reason?}
	ActionRaw      ActionType = "raw"       // {code} — never execute by default
)

// Action is one predicted action. Params is left as a loose map because the
// docs publish two competing param shapes; use the typed getters to read
// values defensively.
type Action struct {
	ActionType  ActionType     `json:"action_type"`
	Params      map[string]any `json:"params,omitempty"`
	Description string         `json:"description,omitempty"`
	RawCode     string         `json:"raw_code,omitempty"`
}

// IntParam reads an integer param (tolerates JSON float64 / int / int64 /
// json.Number encodings). Non-integral floats are truncated toward zero.
func (a Action) IntParam(key string) (int, bool) {
	f, ok := a.FloatParam(key)
	if !ok {
		return 0, false
	}
	return int(f), true
}

// FloatParam reads a numeric param.
func (a Action) FloatParam(key string) (float64, bool) {
	v, ok := a.Params[key]
	if !ok {
		return 0, false
	}
	switch n := v.(type) {
	case float64:
		return n, true
	case float32:
		return float64(n), true
	case int:
		return float64(n), true
	case int64:
		return float64(n), true
	case json.Number:
		f, err := n.Float64()
		if err != nil {
			return 0, false
		}
		return f, true
	default:
		return 0, false
	}
}

// StringParam reads a string param.
func (a Action) StringParam(key string) (string, bool) {
	v, ok := a.Params[key]
	if !ok {
		return "", false
	}
	s, ok := v.(string)
	return s, ok
}

// BoolParam reads a boolean param.
func (a Action) BoolParam(key string) (bool, bool) {
	v, ok := a.Params[key]
	if !ok {
		return false, false
	}
	b, ok := v.(bool)
	return b, ok
}

// StringsParam reads a list-of-strings param (e.g. key_combo's "keys").
func (a Action) StringsParam(key string) ([]string, bool) {
	v, ok := a.Params[key]
	if !ok {
		return nil, false
	}
	switch list := v.(type) {
	case []string:
		return list, true
	case []any:
		out := make([]string, 0, len(list))
		for _, item := range list {
			s, ok := item.(string)
			if !ok {
				return nil, false
			}
			out = append(out, s)
		}
		return out, true
	default:
		return nil, false
	}
}

// Usage is the per-request billing block on inference responses.
type Usage struct {
	InputTokens    int `json:"input_tokens"`
	OutputTokens   int `json:"output_tokens"`
	CreditsCharged int `json:"credits_charged"`
	CostCents      int `json:"cost_cents"`
}

// TrajectoryStep is one prior step attached to a stateless predict call.
type TrajectoryStep struct {
	Screenshot string   `json:"screenshot"`
	Actions    []Action `json:"actions,omitempty"`
	Reasoning  string   `json:"reasoning,omitempty"`
}

// PredictRequest is the body of POST /v1/predict.
type PredictRequest struct {
	// Screenshot is base64 (no "data:" prefix), must be > 100 chars.
	Screenshot  string     `json:"screenshot"`
	Instruction string     `json:"instruction"`
	CUAVersion  CUAVersion `json:"cua_version,omitempty"`
	// SystemPrompt REPLACES the base prompt; Instructions APPENDS to it.
	SystemPrompt     string           `json:"system_prompt,omitempty"`
	Instructions     string           `json:"instructions,omitempty"`
	ScreenWidth      int              `json:"screen_width,omitempty"`  // default 1920 (320-3840)
	ScreenHeight     int              `json:"screen_height,omitempty"` // default 1080 (240-2160)
	Trajectory       []TrajectoryStep `json:"trajectory,omitempty"`
	MaxActions       int              `json:"max_actions,omitempty"` // default 5 (1-10)
	Tools            []string         `json:"tools,omitempty"`
	IncludeReasoning *bool            `json:"include_reasoning,omitempty"` // server default true
	IncludeRawCode   *bool            `json:"include_raw_code,omitempty"`  // server default true
}

// PredictResponse is the body returned by POST /v1/predict.
type PredictResponse struct {
	RequestID string        `json:"request_id"`
	Status    PredictStatus `json:"status"`
	Reasoning string        `json:"reasoning,omitempty"`
	Actions   []Action      `json:"actions"`
	RawCode   []string      `json:"raw_code,omitempty"`
	Usage     Usage         `json:"usage"`
}

// GroundRequest is the body of POST /v1/ground.
type GroundRequest struct {
	Screenshot   string `json:"screenshot"`
	Element      string `json:"element"`
	ScreenWidth  int    `json:"screen_width,omitempty"`
	ScreenHeight int    `json:"screen_height,omitempty"`
}

// GroundResponse carries the resolved pixel coordinates (in the coordinate
// space of the screenshot you SENT — scale back up if you downscaled).
type GroundResponse struct {
	X     int   `json:"x"`
	Y     int   `json:"y"`
	Usage Usage `json:"usage"`
}

// ParseRequest is the body of POST /v1/parse (free).
type ParseRequest struct {
	Code string `json:"code"` // non-empty pyautogui source, < 50k chars
}

// ParseResponse is the body returned by POST /v1/parse.
type ParseResponse struct {
	Actions []Action `json:"actions"`
}

// CreateSessionRequest is the body of POST /v1/sessions.
type CreateSessionRequest struct {
	CUAVersion          CUAVersion     `json:"cua_version,omitempty"`
	ScreenWidth         int            `json:"screen_width,omitempty"`
	ScreenHeight        int            `json:"screen_height,omitempty"`
	MaxTrajectoryLength int            `json:"max_trajectory_length,omitempty"` // default 3 (1-20)
	SystemPrompt        string         `json:"system_prompt,omitempty"`
	Instructions        string         `json:"instructions,omitempty"`
	Tools               []string       `json:"tools,omitempty"`
	Metadata            map[string]any `json:"metadata,omitempty"`
}

// Session is the body returned by POST /v1/sessions.
type Session struct {
	SessionID  string     `json:"session_id"`
	CUAVersion CUAVersion `json:"cua_version"`
	ScreenSize string     `json:"screen_size"` // e.g. "1920x1080"
	CreatedAt  time.Time  `json:"created_at"`
	ExpiresAt  time.Time  `json:"expires_at"`
}

// SessionInfo is the body returned by GET /v1/sessions/{id}.
type SessionInfo struct {
	SessionID        string     `json:"session_id"`
	CUAVersion       CUAVersion `json:"cua_version"`
	ScreenSize       string     `json:"screen_size"`
	StepCount        int        `json:"step_count"`
	CreatedAt        time.Time  `json:"created_at"`
	ExpiresAt        time.Time  `json:"expires_at"`
	TotalCreditsUsed int        `json:"total_credits_used"`
}

// SessionList is the body returned by GET /v1/sessions.
type SessionList struct {
	Sessions []SessionInfo `json:"sessions"`
}

// SessionAck is returned by session reset and delete.
type SessionAck struct {
	Status    string `json:"status"` // "ok"
	SessionID string `json:"session_id"`
}

// SessionPredictRequest is the body of POST /v1/sessions/{id}/predict.
type SessionPredictRequest struct {
	Screenshot       string `json:"screenshot"`
	Instruction      string `json:"instruction"`
	IncludeReasoning *bool  `json:"include_reasoning,omitempty"`
	IncludeRawCode   *bool  `json:"include_raw_code,omitempty"`
}

// SessionPredictResponse is PredictResponse plus session context.
type SessionPredictResponse struct {
	RequestID string        `json:"request_id"`
	SessionID string        `json:"session_id"`
	Step      int           `json:"step"`
	Status    PredictStatus `json:"status"`
	Reasoning string        `json:"reasoning,omitempty"`
	Actions   []Action      `json:"actions"`
	RawCode   []string      `json:"raw_code,omitempty"`
	Usage     Usage         `json:"usage"`
}

// RunStatus is the lifecycle state of a task run.
// queued -> running -> (awaiting_human <-> running) -> terminal.
type RunStatus string

const (
	RunStatusQueued        RunStatus = "queued"
	RunStatusRunning       RunStatus = "running"
	RunStatusAwaitingHuman RunStatus = "awaiting_human"
	RunStatusSucceeded     RunStatus = "succeeded"
	RunStatusFailed        RunStatus = "failed"
	RunStatusCancelled     RunStatus = "cancelled"
	RunStatusTimedOut      RunStatus = "timed_out"
)

// Terminal reports whether the status is one of the immutable end states.
func (s RunStatus) Terminal() bool {
	switch s {
	case RunStatusSucceeded, RunStatusFailed, RunStatusCancelled, RunStatusTimedOut:
		return true
	default:
		return false
	}
}

// OnAwaitingHuman selects run behaviour when the agent needs a human.
type OnAwaitingHuman string

const (
	OnAwaitingHumanPause  OnAwaitingHuman = "pause"
	OnAwaitingHumanFail   OnAwaitingHuman = "fail"
	OnAwaitingHumanCancel OnAwaitingHuman = "cancel"
)

// CreateRunRequest is the body of POST /v1/runs. Unknown fields are rejected
// by the server with 422, so only documented fields are modeled.
type CreateRunRequest struct {
	// IdempotencyKey is sent as the Idempotency-Key HEADER (not a body
	// field). Setting it makes the create retryable. Up to 128 chars of
	// [A-Za-z0-9_-:].
	IdempotencyKey string `json:"-"`

	MachineID                   string          `json:"machine_id"`
	Task                        string          `json:"task"`
	CUAVersion                  CUAVersion      `json:"cua_version,omitempty"`
	Instructions                string          `json:"instructions,omitempty"`
	SystemPrompt                string          `json:"system_prompt,omitempty"`
	MaxSteps                    int             `json:"max_steps,omitempty"` // default 50 (1-1000)
	DeadlineSeconds             int             `json:"deadline_seconds,omitempty"`
	OnAwaitingHuman             OnAwaitingHuman `json:"on_awaiting_human,omitempty"` // default pause
	AwaitingHumanTimeoutSeconds int             `json:"awaiting_human_timeout_seconds,omitempty"`
	WebhookURL                  string          `json:"webhook_url,omitempty"` // https only
	Metadata                    map[string]any  `json:"metadata,omitempty"`    // <= 50 keys
}

// RunResult is the verification result once a run finishes.
type RunResult struct {
	Passed  bool   `json:"passed"`
	Status  string `json:"status"`
	Summary string `json:"summary"`
	Verdict string `json:"verdict,omitempty"`
}

// RunError is the error block on a failed run.
type RunError struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

// Run is the agent.run object returned by create / get / list.
type Run struct {
	ID                  string          `json:"id"`
	Object              string          `json:"object"` // "agent.run"
	Status              RunStatus       `json:"status"`
	MachineID           string          `json:"machine_id"`
	Task                string          `json:"task"`
	CUAVersion          CUAVersion      `json:"cua_version"`
	Instructions        string          `json:"instructions,omitempty"`
	MaxSteps            int             `json:"max_steps"`
	OnAwaitingHuman     OnAwaitingHuman `json:"on_awaiting_human"`
	StepsCompleted      int             `json:"steps_completed"`
	CreditsCharged      int             `json:"credits_charged"`
	CostCents           int             `json:"cost_cents"`
	Result              *RunResult      `json:"result"`
	Error               *RunError       `json:"error"`
	AwaitingHumanReason string          `json:"awaiting_human_reason,omitempty"`
	Metadata            map[string]any  `json:"metadata,omitempty"`
	WebhookURL          string          `json:"webhook_url,omitempty"`
	// WebhookSecret is returned exactly ONCE on create (null on get/list).
	// Persist it immediately; it signs every webhook for this run.
	WebhookSecret      string     `json:"webhook_secret,omitempty"`
	CreatedAt          *time.Time `json:"created_at"`
	StartedAt          *time.Time `json:"started_at"`
	AwaitingHumanSince *time.Time `json:"awaiting_human_since"`
	FinishedAt         *time.Time `json:"finished_at"`
	RequestID          string     `json:"request_id,omitempty"`
}

// ListRunsParams are the query params of GET /v1/runs.
type ListRunsParams struct {
	Status RunStatus // optional filter
	Limit  int       // optional, server default 20
}

// RunList is the body returned by GET /v1/runs.
type RunList struct {
	Object    string `json:"object"` // "list"
	Data      []Run  `json:"data"`
	HasMore   bool   `json:"has_more"`
	RequestID string `json:"request_id,omitempty"`
}

// RunEventType enumerates SSE event types on GET /v1/runs/{id}/events.
type RunEventType string

const (
	RunEventStatus        RunEventType = "status"
	RunEventText          RunEventType = "text"
	RunEventReasoning     RunEventType = "reasoning"
	RunEventToolCall      RunEventType = "tool_call"
	RunEventToolResult    RunEventType = "tool_result"
	RunEventAwaitingHuman RunEventType = "awaiting_human"
	RunEventResumed       RunEventType = "resumed"
	RunEventStep          RunEventType = "step"
	RunEventBilling       RunEventType = "billing"
	RunEventError         RunEventType = "error"
	RunEventDone          RunEventType = "done" // stream closes after this
)

// RunEvent is one durable event from the run event stream. Seq is the
// reconnect cursor (sent back as Last-Event-ID).
type RunEvent struct {
	Seq  int64           `json:"seq"`
	Type RunEventType    `json:"type"`
	Data json.RawMessage `json:"data"`
}

// ModelInfo describes one entry of GET /v1/models "models".
type ModelInfo struct {
	ID          string `json:"id"`
	Description string `json:"description"`
}

// CUAVersionInfo describes one entry of GET /v1/models "cua_versions".
type CUAVersionInfo struct {
	ID          CUAVersion `json:"id"`
	Description string     `json:"description"`
	AvgStepTime string     `json:"avg_step_time"`
	Features    []string   `json:"features"`
}

// ModelsResponse is the body returned by GET /v1/models (free).
type ModelsResponse struct {
	Models      []ModelInfo      `json:"models"`
	CUAVersions []CUAVersionInfo `json:"cua_versions"`
	ActionTypes []string         `json:"action_types"`
}

// UsageBreakdownEntry is one per-endpoint row of the usage breakdown.
type UsageBreakdownEntry struct {
	Requests int64 `json:"requests"`
	Credits  int64 `json:"credits"`
}

// UsageResponse is the body returned by GET /v1/usage (free).
type UsageResponse struct {
	Period             string                         `json:"period"` // YYYY-MM
	TotalRequests      int64                          `json:"total_requests"`
	TotalCredits       int64                          `json:"total_credits"`
	TotalCostCents     int64                          `json:"total_cost_cents"`
	Breakdown          map[string]UsageBreakdownEntry `json:"breakdown"`
	Balance            int64                          `json:"balance"`              // wallet, cents
	WalletBalanceCents int64                          `json:"wallet_balance_cents"` // same as Balance
	WalletBalanceUSD   float64                        `json:"wallet_balance_usd"`
}

// Bool returns a pointer to b, for the *bool request fields.
func Bool(b bool) *bool { return &b }
