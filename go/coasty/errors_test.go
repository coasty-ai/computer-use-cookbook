package coasty

import (
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

func fakeResponse(status int, headers map[string]string, body string) *http.Response {
	h := http.Header{}
	for k, v := range headers {
		h.Set(k, v)
	}
	return &http.Response{
		StatusCode: status,
		Header:     h,
		Body:       io.NopCloser(strings.NewReader(body)),
	}
}

func TestParseAPIErrorEnvelope(t *testing.T) {
	tests := []struct {
		name    string
		status  int
		headers map[string]string
		body    string
		check   func(t *testing.T, e *APIError)
	}{
		{
			name:    "full billing envelope with extras",
			status:  402,
			headers: map[string]string{"X-Coasty-Request-Id": "req_hdr"},
			body: `{"error":{"code":"INSUFFICIENT_CREDITS","message":"Operation needs 20 credits; you have 5.",
				"type":"billing_error","request_id":"req_8f2c1e9a",
				"suggestion":"Top up","docs_url":"https://coasty.ai/api-docs#errors",
				"required":20,"balance":5}}`,
			check: func(t *testing.T, e *APIError) {
				if e.Code != CodeInsufficientCredits || e.Type != "billing_error" {
					t.Errorf("code/type = %q/%q", e.Code, e.Type)
				}
				if e.RequestID != "req_8f2c1e9a" {
					t.Errorf("body request_id must win over header, got %q", e.RequestID)
				}
				if e.Required != 20 || e.Balance != 5 {
					t.Errorf("required/balance = %d/%d", e.Required, e.Balance)
				}
				if e.Extras["suggestion"] != "Top up" || e.Extras["docs_url"] != "https://coasty.ai/api-docs#errors" {
					t.Errorf("extras = %v", e.Extras)
				}
				if _, leaked := e.Extras["code"]; leaked {
					t.Error("parsed fields must not be duplicated in Extras")
				}
			},
		},
		{
			name:   "scope error",
			status: 403,
			body: `{"error":{"code":"INSUFFICIENT_SCOPE","message":"missing scope","type":"auth_error",
				"request_id":"req_403","required_scope":"runs:write","current_scopes":["predict","ground"]}}`,
			check: func(t *testing.T, e *APIError) {
				if e.RequiredScope != "runs:write" {
					t.Errorf("required_scope = %q", e.RequiredScope)
				}
				if e.Extras["current_scopes"] == nil {
					t.Errorf("current_scopes extra missing: %v", e.Extras)
				}
				if !IsInsufficientScope(e) {
					t.Error("IsInsufficientScope = false")
				}
			},
		},
		{
			name:   "retry_after from body when header absent",
			status: 503,
			body:   `{"error":{"code":"UPSTREAM_UNAVAILABLE","message":"down","type":"server_error","request_id":"req_1","retry_after":3}}`,
			check: func(t *testing.T, e *APIError) {
				if e.RetryAfter != 3*time.Second {
					t.Errorf("RetryAfter = %v, want 3s", e.RetryAfter)
				}
			},
		},
		{
			name:    "Retry-After header wins over body field",
			status:  429,
			headers: map[string]string{"Retry-After": "7"},
			body:    `{"error":{"code":"RATE_LIMITED","message":"slow","type":"rate_limit_error","request_id":"req_1","retry_after":3}}`,
			check: func(t *testing.T, e *APIError) {
				if e.RetryAfter != 7*time.Second {
					t.Errorf("RetryAfter = %v, want 7s (header wins)", e.RetryAfter)
				}
				if !IsRateLimited(e) {
					t.Error("IsRateLimited = false")
				}
			},
		},
		{
			name:    "non-JSON body tolerated",
			status:  502,
			headers: map[string]string{"X-Coasty-Request-Id": "req_html"},
			body:    "<html>Bad Gateway</html>",
			check: func(t *testing.T, e *APIError) {
				if e.Code != "" {
					t.Errorf("code = %q, want empty for non-JSON", e.Code)
				}
				if e.Message != "<html>Bad Gateway</html>" {
					t.Errorf("message = %q", e.Message)
				}
				if e.RequestID != "req_html" {
					t.Errorf("request_id from header = %q", e.RequestID)
				}
			},
		},
		{
			name:   "empty body falls back to status text",
			status: 504,
			body:   "",
			check: func(t *testing.T, e *APIError) {
				if e.Message != "Gateway Timeout" {
					t.Errorf("message = %q", e.Message)
				}
			},
		},
		{
			name:   "request_id falls back to header when body omits it",
			status: 404,
			headers: map[string]string{
				"X-Coasty-Request-Id": "req_only_header",
			},
			body: `{"error":{"code":"RUN_NOT_FOUND","message":"no such run","type":"not_found_error"}}`,
			check: func(t *testing.T, e *APIError) {
				if e.RequestID != "req_only_header" {
					t.Errorf("request_id = %q", e.RequestID)
				}
				if !IsNotFound(e) {
					t.Error("IsNotFound = false")
				}
			},
		},
		{
			name:   "idempotency reuse code is canonical regardless of status",
			status: 409,
			body:   `{"error":{"code":"IDEMPOTENCY_KEY_REUSED","message":"body mismatch","type":"state_error","request_id":"req_409"}}`,
			check: func(t *testing.T, e *APIError) {
				// Docs list this code under both 422 and 409 — branch on the
				// CODE, never the status.
				if e.Code != CodeIdempotencyKeyReused {
					t.Errorf("code = %q", e.Code)
				}
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			e := parseAPIError(fakeResponse(tt.status, tt.headers, tt.body))
			if e.StatusCode != tt.status {
				t.Errorf("StatusCode = %d, want %d", e.StatusCode, tt.status)
			}
			tt.check(t, e)
		})
	}
}

func TestAPIErrorErrorString(t *testing.T) {
	e := &APIError{
		Code: CodeInsufficientCredits, Message: "need more", Type: "billing_error",
		RequestID: "req_42", StatusCode: 402,
	}
	msg := e.Error()
	for _, want := range []string{"INSUFFICIENT_CREDITS", "402", "req_42", "need more", "billing_error"} {
		if !strings.Contains(msg, want) {
			t.Errorf("Error() = %q, missing %q", msg, want)
		}
	}
}

func TestErrorHelpersUnwrapWrappedErrors(t *testing.T) {
	base := &APIError{Code: CodeInsufficientCredits, StatusCode: 402}
	wrapped := fmt.Errorf("step 3 failed: %w", base)
	if !IsInsufficientCredits(wrapped) {
		t.Error("IsInsufficientCredits must see through wrapping")
	}
	if got, ok := AsAPIError(wrapped); !ok || got != base {
		t.Errorf("AsAPIError = %v, %v", got, ok)
	}
	if IsInsufficientCredits(errors.New("plain")) {
		t.Error("plain errors are not billing errors")
	}
	if _, ok := AsAPIError(nil); ok {
		t.Error("AsAPIError(nil) must be false")
	}
	if IsInsufficientCredits(&APIError{Code: CodeWalletExhausted, StatusCode: 402}) != true {
		t.Error("WALLET_EXHAUSTED counts as insufficient credits")
	}
}

func TestParseRetryAfter(t *testing.T) {
	tests := []struct {
		in   string
		want time.Duration
	}{
		{"", 0},
		{"0", 0},
		{"2", 2 * time.Second},
		{"1.5", 1500 * time.Millisecond},
		{"-3", 0},
		{"garbage", 0},
	}
	for _, tt := range tests {
		if got := parseRetryAfter(tt.in); got != tt.want {
			t.Errorf("parseRetryAfter(%q) = %v, want %v", tt.in, got, tt.want)
		}
	}
	// HTTP-date form: a date in the future yields a positive duration.
	future := time.Now().Add(30 * time.Second).UTC().Format(http.TimeFormat)
	if got := parseRetryAfter(future); got <= 0 || got > 31*time.Second {
		t.Errorf("parseRetryAfter(http-date) = %v", got)
	}
}
