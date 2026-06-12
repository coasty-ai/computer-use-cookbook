package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"image"
	"image/color"
	"image/png"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/coasty-ai/computer-use-cookbook/go/coasty"
	"github.com/coasty-ai/computer-use-cookbook/go/examples/internal/executor"
	"github.com/coasty-ai/computer-use-cookbook/go/examples/internal/exutil"
)

const testAPIKey = "sk-coasty-test-000000000000000000000000000000000000000000000000"

// writeTestPNG writes a small deterministic PNG and returns its path.
func writeTestPNG(t *testing.T, w, h int) string {
	t.Helper()
	img := image.NewRGBA(image.Rect(0, 0, w, h))
	for x := 0; x < w; x++ {
		for y := 0; y < h; y++ {
			img.Set(x, y, color.RGBA{R: uint8(x), G: uint8(y), B: 7, A: 255})
		}
	}
	var buf bytes.Buffer
	if err := png.Encode(&buf, img); err != nil {
		t.Fatalf("encoding test PNG: %v", err)
	}
	path := filepath.Join(t.TempDir(), "screen.png")
	if err := os.WriteFile(path, buf.Bytes(), 0o600); err != nil {
		t.Fatalf("writing test PNG: %v", err)
	}
	return path
}

// recordingExecutor records every executed action.
type recordingExecutor struct {
	actions []coasty.Action
}

func (r *recordingExecutor) Execute(_ context.Context, a coasty.Action) error {
	r.actions = append(r.actions, a)
	return nil
}

func newTestClient(t *testing.T, baseURL string) *coasty.Client {
	t.Helper()
	return coasty.NewClient(
		coasty.WithAPIKey(testAPIKey),
		coasty.WithBaseURL(baseURL),
		coasty.WithTimeout(5*time.Second),
		coasty.WithBackoff(time.Millisecond, 2*time.Millisecond),
	)
}

func predictJSON(status coasty.PredictStatus, actions []coasty.Action, credits int) []byte {
	resp := coasty.PredictResponse{
		RequestID: "req_test",
		Status:    status,
		Reasoning: "because",
		Actions:   actions,
		Usage:     coasty.Usage{CreditsCharged: credits, CostCents: credits},
	}
	b, err := json.Marshal(resp)
	if err != nil {
		panic(err)
	}
	return b
}

// TestRunLoopStopsOnDone drives the loop against a mock /predict that says
// continue twice then done, asserting the request contract on the way.
func TestRunLoopStopsOnDone(t *testing.T) {
	pngPath := writeTestPNG(t, 64, 40)
	shot, err := exutil.LoadPNG(pngPath)
	if err != nil {
		t.Fatal(err)
	}

	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/predict" {
			t.Errorf("unexpected request: %s %s", r.Method, r.URL.Path)
			http.NotFound(w, r)
			return
		}
		if got := r.Header.Get("X-API-Key"); got != testAPIKey {
			t.Errorf("X-API-Key = %q, want the test key", got)
		}
		var body coasty.PredictRequest
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Errorf("decoding request body: %v", err)
		}
		if body.Screenshot != shot.B64 {
			t.Error("request screenshot does not match the loaded PNG base64")
		}
		if body.Instruction != "open the calculator" {
			t.Errorf("instruction = %q", body.Instruction)
		}
		if body.ScreenWidth != 64 || body.ScreenHeight != 40 {
			t.Errorf("screen dims = %dx%d, want 64x40 (the PNG's own size)", body.ScreenWidth, body.ScreenHeight)
		}

		w.Header().Set("Content-Type", "application/json")
		switch calls.Add(1) {
		case 1:
			_, _ = w.Write(predictJSON(coasty.PredictStatusContinue, []coasty.Action{
				{ActionType: coasty.ActionClick, Params: map[string]any{"x": 10.0, "y": 20.0}},
				{ActionType: coasty.ActionTypeText, Params: map[string]any{"text": "42*17"}},
			}, 5))
		case 2:
			_, _ = w.Write(predictJSON(coasty.PredictStatusContinue, []coasty.Action{
				{ActionType: coasty.ActionKeyPress, Params: map[string]any{"key": "enter"}},
			}, 5))
		default:
			_, _ = w.Write(predictJSON(coasty.PredictStatusDone, []coasty.Action{
				{ActionType: coasty.ActionDone, Params: map[string]any{}},
			}, 5))
		}
	}))
	defer srv.Close()

	rec := &recordingExecutor{}
	var logBuf bytes.Buffer
	result, err := RunLoop(context.Background(), newTestClient(t, srv.URL),
		FileSource{Path: pngPath}, rec,
		LoopOptions{Instruction: "open the calculator", MaxSteps: 10, Log: &logBuf})
	if err != nil {
		t.Fatalf("RunLoop() error = %v", err)
	}
	if result.Reason != StopDone {
		t.Errorf("Reason = %q, want %q", result.Reason, StopDone)
	}
	if result.Steps != 3 || calls.Load() != 3 {
		t.Errorf("Steps = %d (server saw %d), want 3", result.Steps, calls.Load())
	}
	if result.CreditsUsed != 15 {
		t.Errorf("CreditsUsed = %d, want 15", result.CreditsUsed)
	}
	if len(rec.actions) != 4 {
		t.Errorf("executor saw %d actions, want 4", len(rec.actions))
	}
	if !strings.Contains(logBuf.String(), "step 3: status=done") {
		t.Errorf("loop log missing final step line:\n%s", logBuf.String())
	}
}

// TestRunLoopRespectsMaxSteps verifies the -max-steps bound when the model
// keeps saying continue.
func TestRunLoopRespectsMaxSteps(t *testing.T) {
	pngPath := writeTestPNG(t, 32, 32)
	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write(predictJSON(coasty.PredictStatusContinue, []coasty.Action{
			{ActionType: coasty.ActionScroll, Params: map[string]any{"clicks": -2.0}},
		}, 5))
	}))
	defer srv.Close()

	rec := &recordingExecutor{}
	result, err := RunLoop(context.Background(), newTestClient(t, srv.URL),
		FileSource{Path: pngPath}, rec,
		LoopOptions{Instruction: "scroll forever", MaxSteps: 3})
	if err != nil {
		t.Fatalf("RunLoop() error = %v", err)
	}
	if result.Reason != StopMaxSteps {
		t.Errorf("Reason = %q, want %q", result.Reason, StopMaxSteps)
	}
	if got := calls.Load(); got != 3 {
		t.Errorf("server saw %d predict calls, want exactly 3", got)
	}
	if result.Steps != 3 || result.FinalStatus != coasty.PredictStatusContinue {
		t.Errorf("Steps=%d FinalStatus=%q, want 3/continue", result.Steps, result.FinalStatus)
	}
}

// TestRunLoopStopsOnFail covers the model giving up.
func TestRunLoopStopsOnFail(t *testing.T) {
	pngPath := writeTestPNG(t, 32, 32)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write(predictJSON(coasty.PredictStatusFail, []coasty.Action{
			{ActionType: coasty.ActionFail, Params: map[string]any{"reason": "dialog blocked"}},
		}, 5))
	}))
	defer srv.Close()

	result, err := RunLoop(context.Background(), newTestClient(t, srv.URL),
		FileSource{Path: pngPath}, &recordingExecutor{},
		LoopOptions{Instruction: "do the thing", MaxSteps: 5})
	if err != nil {
		t.Fatalf("RunLoop() error = %v", err)
	}
	if result.Reason != StopFail || result.Steps != 1 {
		t.Errorf("got reason=%q steps=%d, want fail/1", result.Reason, result.Steps)
	}
}

// TestRunLoopSurfacesAPIErrors asserts errors carry the documented code and
// request_id.
func TestRunLoopSurfacesAPIErrors(t *testing.T) {
	pngPath := writeTestPNG(t, 32, 32)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Coasty-Request-Id", "req_err_1")
		w.WriteHeader(http.StatusPaymentRequired)
		_, _ = fmt.Fprint(w, `{"error":{"code":"INSUFFICIENT_CREDITS","message":"top up","type":"billing_error","request_id":"req_err_1","required":5,"balance":0}}`)
	}))
	defer srv.Close()

	_, err := RunLoop(context.Background(), newTestClient(t, srv.URL),
		FileSource{Path: pngPath}, &recordingExecutor{},
		LoopOptions{Instruction: "x", MaxSteps: 2})
	if err == nil {
		t.Fatal("RunLoop() expected an error")
	}
	apiErr, ok := coasty.AsAPIError(err)
	if !ok || apiErr.Code != coasty.CodeInsufficientCredits {
		t.Fatalf("error = %v, want INSUFFICIENT_CREDITS APIError", err)
	}
	if apiErr.RequestID != "req_err_1" || !strings.Contains(err.Error(), "req_err_1") {
		t.Errorf("error must carry request_id req_err_1, got %v", err)
	}
}

// TestRunLoopExecutorErrorAborts: a failing executor must abort the loop,
// not be silently ignored.
func TestRunLoopExecutorErrorAborts(t *testing.T) {
	pngPath := writeTestPNG(t, 32, 32)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		// click without coordinates: the logging executor must reject it.
		_, _ = w.Write(predictJSON(coasty.PredictStatusContinue, []coasty.Action{
			{ActionType: coasty.ActionClick, Params: map[string]any{}},
		}, 5))
	}))
	defer srv.Close()

	_, err := RunLoop(context.Background(), newTestClient(t, srv.URL),
		FileSource{Path: pngPath}, &executor.Logging{W: &bytes.Buffer{}},
		LoopOptions{Instruction: "x", MaxSteps: 2})
	if err == nil || !strings.Contains(err.Error(), "missing required params") {
		t.Fatalf("RunLoop() = %v, want a missing-params executor error", err)
	}
}

func TestRunLoopValidatesInputs(t *testing.T) {
	_, err := RunLoop(context.Background(), coasty.NewClient(coasty.WithAPIKey(testAPIKey)),
		FileSource{Path: "nowhere.png"}, &recordingExecutor{}, LoopOptions{})
	if err == nil || !strings.Contains(err.Error(), "instruction") {
		t.Errorf("empty instruction must error, got %v", err)
	}

	_, err = RunLoop(context.Background(), coasty.NewClient(coasty.WithAPIKey(testAPIKey)),
		FileSource{Path: filepath.Join(t.TempDir(), "missing.png")}, &recordingExecutor{},
		LoopOptions{Instruction: "x", MaxSteps: 1})
	var pathErr *os.PathError
	if err == nil || !errors.As(err, &pathErr) {
		t.Errorf("missing screenshot must surface the file error, got %v", err)
	}
}
