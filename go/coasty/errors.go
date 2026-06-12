package coasty

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"
)

// Error codes from the documented catalog. Branch on Code, never on Message.
const (
	CodeInvalidAPIKey        = "INVALID_API_KEY"
	CodeInsufficientScope    = "INSUFFICIENT_SCOPE"
	CodeInsufficientCredits  = "INSUFFICIENT_CREDITS"
	CodeWalletExhausted      = "WALLET_EXHAUSTED"
	CodeValidationError      = "VALIDATION_ERROR"
	CodeInvalidScreenshot    = "INVALID_SCREENSHOT"
	CodePayloadTooLarge      = "PAYLOAD_TOO_LARGE"
	CodeInvalidLimit         = "INVALID_LIMIT"
	CodeInvalidStatusFilter  = "INVALID_STATUS_FILTER"
	CodeFeatureNotAvailable  = "FEATURE_NOT_AVAILABLE"
	CodeNotFound             = "NOT_FOUND"
	CodeRunNotFound          = "RUN_NOT_FOUND"
	CodeSessionNotFound      = "SESSION_NOT_FOUND"
	CodeMachineNotFound      = "MACHINE_NOT_FOUND"
	CodeWorkflowNotFound     = "WORKFLOW_NOT_FOUND"
	CodeNotAwaitingHuman     = "NOT_AWAITING_HUMAN"
	CodeResumeConflict       = "RESUME_CONFLICT"
	CodeInvalidState         = "INVALID_STATE"
	CodeIdempotencyKeyReused = "IDEMPOTENCY_KEY_REUSED"
	CodeRateLimited          = "RATE_LIMITED"
	CodeInternalError        = "INTERNAL_ERROR"
	CodePredictionFailed     = "PREDICTION_FAILED"
	CodeGroundingFailed      = "GROUNDING_FAILED"
	CodeUpstreamUnavailable  = "UPSTREAM_UNAVAILABLE"
	CodeUpstreamTimeout      = "UPSTREAM_TIMEOUT"
)

// APIError is a non-2xx response from the Coasty API. Every error carries
// Code, Message, Type and RequestID; the context fields are code-specific.
type APIError struct {
	// Code is machine-readable and stable across versions (e.g.
	// "INSUFFICIENT_CREDITS"). Empty when the body was not the documented
	// JSON envelope.
	Code string
	// Message is human-readable and may change between versions.
	Message string
	// Type is the coarse category: auth_error, billing_error,
	// validation_error, not_found_error, state_error, rate_limit_error,
	// server_error.
	Type string
	// RequestID ties the request end-to-end (also the X-Coasty-Request-Id
	// header). Quote it verbatim to support.
	RequestID string
	// StatusCode is the HTTP status of the response.
	StatusCode int

	// Required and Balance are set on INSUFFICIENT_CREDITS (cents; 1
	// credit = 1 cent).
	Required int64
	Balance  int64
	// RequiredScope is set on INSUFFICIENT_SCOPE.
	RequiredScope string
	// RetryAfter is how long the server asked us to wait (from the
	// Retry-After header, falling back to the retry_after body field).
	// Zero when absent.
	RetryAfter time.Duration

	// Extras holds the remaining envelope fields verbatim (suggestion,
	// docs_url, support, details, current_state, allowed_from,
	// current_scopes, valid_options, ...).
	Extras map[string]any
}

// Error implements the error interface.
func (e *APIError) Error() string {
	var b strings.Builder
	b.WriteString("coasty: ")
	if e.Code != "" {
		b.WriteString(e.Code)
	} else {
		b.WriteString("api error")
	}
	fmt.Fprintf(&b, " (http %d", e.StatusCode)
	if e.Type != "" {
		fmt.Fprintf(&b, ", %s", e.Type)
	}
	if e.RequestID != "" {
		fmt.Fprintf(&b, ", request_id %s", e.RequestID)
	}
	b.WriteString(")")
	if e.Message != "" {
		b.WriteString(": ")
		b.WriteString(e.Message)
	}
	return b.String()
}

// AsAPIError unwraps err into an *APIError if there is one in the chain.
func AsAPIError(err error) (*APIError, bool) {
	var apiErr *APIError
	if errors.As(err, &apiErr) {
		return apiErr, true
	}
	return nil, false
}

// IsInsufficientCredits reports whether err is a 402 INSUFFICIENT_CREDITS
// (or WALLET_EXHAUSTED) billing error.
func IsInsufficientCredits(err error) bool {
	e, ok := AsAPIError(err)
	return ok && (e.Code == CodeInsufficientCredits || e.Code == CodeWalletExhausted)
}

// IsRateLimited reports whether err is a 429 RATE_LIMITED error.
func IsRateLimited(err error) bool {
	e, ok := AsAPIError(err)
	return ok && (e.Code == CodeRateLimited || e.StatusCode == http.StatusTooManyRequests)
}

// IsNotFound reports whether err is any of the documented 404 codes.
func IsNotFound(err error) bool {
	e, ok := AsAPIError(err)
	return ok && e.StatusCode == http.StatusNotFound
}

// IsInsufficientScope reports whether err is a 403 INSUFFICIENT_SCOPE error.
func IsInsufficientScope(err error) bool {
	e, ok := AsAPIError(err)
	return ok && e.Code == CodeInsufficientScope
}

const maxErrorBodyBytes = 1 << 20 // never buffer more than 1 MiB of an error body

// parseAPIError reads and closes resp.Body, decoding the documented error
// envelope {"error": {code, message, type, request_id, ...}}. Non-JSON
// bodies are tolerated: Code stays empty and Message carries a snippet.
func parseAPIError(resp *http.Response) *APIError {
	body, _ := io.ReadAll(io.LimitReader(resp.Body, maxErrorBodyBytes))
	_ = resp.Body.Close()

	e := &APIError{
		StatusCode: resp.StatusCode,
		RequestID:  resp.Header.Get("X-Coasty-Request-Id"),
		RetryAfter: parseRetryAfter(resp.Header.Get("Retry-After")),
	}

	var envelope struct {
		Error map[string]any `json:"error"`
	}
	if err := json.Unmarshal(body, &envelope); err != nil || envelope.Error == nil {
		snippet := strings.TrimSpace(string(body))
		if len(snippet) > 512 {
			snippet = snippet[:512]
		}
		if snippet == "" {
			snippet = http.StatusText(resp.StatusCode)
		}
		e.Message = snippet
		return e
	}

	fields := envelope.Error
	e.Code = popString(fields, "code")
	e.Message = popString(fields, "message")
	e.Type = popString(fields, "type")
	if id := popString(fields, "request_id"); id != "" {
		e.RequestID = id
	}
	if v, ok := popInt64(fields, "required"); ok {
		e.Required = v
	}
	if v, ok := popInt64(fields, "balance"); ok {
		e.Balance = v
	}
	e.RequiredScope = popString(fields, "required_scope")
	if secs, ok := popFloat(fields, "retry_after"); ok && e.RetryAfter == 0 {
		e.RetryAfter = time.Duration(secs * float64(time.Second))
	}
	if len(fields) > 0 {
		e.Extras = fields
	}
	return e
}

// parseRetryAfter parses a Retry-After header value: either delta-seconds or
// an HTTP-date. Returns 0 when absent or unparsable.
func parseRetryAfter(v string) time.Duration {
	if v == "" {
		return 0
	}
	if secs, err := strconv.ParseFloat(strings.TrimSpace(v), 64); err == nil {
		if secs < 0 {
			return 0
		}
		return time.Duration(secs * float64(time.Second))
	}
	if t, err := http.ParseTime(v); err == nil {
		if d := time.Until(t); d > 0 {
			return d
		}
	}
	return 0
}

func popString(m map[string]any, key string) string {
	v, ok := m[key]
	if !ok {
		return ""
	}
	s, ok := v.(string)
	if !ok {
		return ""
	}
	delete(m, key)
	return s
}

func popFloat(m map[string]any, key string) (float64, bool) {
	v, ok := m[key]
	if !ok {
		return 0, false
	}
	f, ok := v.(float64)
	if !ok {
		return 0, false
	}
	delete(m, key)
	return f, true
}

func popInt64(m map[string]any, key string) (int64, bool) {
	f, ok := popFloat(m, key)
	if !ok {
		return 0, false
	}
	return int64(f), true
}
