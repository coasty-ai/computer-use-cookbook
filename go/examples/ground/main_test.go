package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"image"
	"image/color"
	"image/png"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/coasty-ai/computer-use-cookbook/go/coasty"
	"github.com/coasty-ai/computer-use-cookbook/go/examples/internal/executor"
	"github.com/coasty-ai/computer-use-cookbook/go/examples/internal/exutil"
)

const testAPIKey = "sk-coasty-test-000000000000000000000000000000000000000000000000"

func writeTestPNG(t *testing.T, w, h int) string {
	t.Helper()
	img := image.NewRGBA(image.Rect(0, 0, w, h))
	for x := 0; x < w; x++ {
		for y := 0; y < h; y++ {
			img.Set(x, y, color.RGBA{R: uint8(y), G: uint8(x), B: 99, A: 255})
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

func newTestClient(t *testing.T, baseURL string) *coasty.Client {
	t.Helper()
	return coasty.NewClient(
		coasty.WithAPIKey(testAPIKey),
		coasty.WithBaseURL(baseURL),
		coasty.WithTimeout(5*time.Second),
		coasty.WithBackoff(time.Millisecond, 2*time.Millisecond),
	)
}

// TestGroundAndClickScalesCoordinates: PNG is 128x72, the "real" screen is
// 256x144 (2x). The server answers (100, 50) in model space; the example
// must click (200, 100).
func TestGroundAndClickScalesCoordinates(t *testing.T) {
	pngPath := writeTestPNG(t, 128, 72)
	shot, err := exutil.LoadPNG(pngPath)
	if err != nil {
		t.Fatal(err)
	}

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/ground" {
			t.Errorf("unexpected request: %s %s", r.Method, r.URL.Path)
			http.NotFound(w, r)
			return
		}
		if got := r.Header.Get("X-API-Key"); got != testAPIKey {
			t.Errorf("X-API-Key = %q, want the test key", got)
		}
		var body coasty.GroundRequest
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Errorf("decoding request: %v", err)
		}
		if body.Element != "the submit button" {
			t.Errorf("element = %q", body.Element)
		}
		if body.Screenshot != shot.B64 {
			t.Error("screenshot does not match the loaded PNG base64")
		}
		if body.ScreenWidth != 128 || body.ScreenHeight != 72 {
			t.Errorf("sent dims = %dx%d, want the PNG's 128x72", body.ScreenWidth, body.ScreenHeight)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = fmt.Fprint(w, `{"x":100,"y":50,"usage":{"input_tokens":10,"output_tokens":2,"credits_charged":3,"cost_cents":3}}`)
	}))
	defer srv.Close()

	scale := executor.NewScale(256, 144, shot.Width, shot.Height)
	var logBuf bytes.Buffer
	result, err := GroundAndClick(context.Background(), newTestClient(t, srv.URL),
		shot, "the submit button", scale, &executor.Logging{W: &logBuf, Scale: scale})
	if err != nil {
		t.Fatalf("GroundAndClick() error = %v", err)
	}

	if result.ModelX != 100 || result.ModelY != 50 {
		t.Errorf("model coords = (%d, %d), want (100, 50)", result.ModelX, result.ModelY)
	}
	if result.RealX != 200 || result.RealY != 100 {
		t.Errorf("real coords = (%d, %d), want (200, 100) — 2x scaling", result.RealX, result.RealY)
	}
	if result.Credits != 3 {
		t.Errorf("Credits = %d, want 3", result.Credits)
	}
	log := logBuf.String()
	if !strings.Contains(log, "click at (200, 100)") {
		t.Errorf("executor must click the SCALED coordinates, log:\n%s", log)
	}
	if !strings.Contains(log, "[model (100, 50)]") {
		t.Errorf("executor log should note the model-space coordinates, log:\n%s", log)
	}
	if !strings.Contains(log, `click grounded element "the submit button"`) {
		t.Errorf("executor log should carry the action description, log:\n%s", log)
	}
}

// TestGroundAndClickIdentityScale: with no downscaling, coordinates map 1:1.
func TestGroundAndClickIdentityScale(t *testing.T) {
	pngPath := writeTestPNG(t, 64, 64)
	shot, err := exutil.LoadPNG(pngPath)
	if err != nil {
		t.Fatal(err)
	}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = fmt.Fprint(w, `{"x":33,"y":44,"usage":{"credits_charged":3,"cost_cents":3}}`)
	}))
	defer srv.Close()

	var logBuf bytes.Buffer
	scale := executor.NewScale(64, 64, 64, 64)
	result, err := GroundAndClick(context.Background(), newTestClient(t, srv.URL),
		shot, "icon", scale, &executor.Logging{W: &logBuf, Scale: scale})
	if err != nil {
		t.Fatalf("GroundAndClick() error = %v", err)
	}
	if result.RealX != 33 || result.RealY != 44 {
		t.Errorf("identity scale must keep (33, 44), got (%d, %d)", result.RealX, result.RealY)
	}
	if strings.Contains(logBuf.String(), "[model") {
		t.Errorf("identity scale should not log a model-space note:\n%s", logBuf.String())
	}
}

// TestGroundAndClickSurfacesAPIErrors asserts errors carry code+request_id.
func TestGroundAndClickSurfacesAPIErrors(t *testing.T) {
	pngPath := writeTestPNG(t, 64, 64)
	shot, err := exutil.LoadPNG(pngPath)
	if err != nil {
		t.Fatal(err)
	}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Coasty-Request-Id", "req_g_1")
		w.WriteHeader(http.StatusUnprocessableEntity)
		_, _ = fmt.Fprint(w, `{"error":{"code":"INVALID_SCREENSHOT","message":"bad b64","type":"validation_error","request_id":"req_g_1"}}`)
	}))
	defer srv.Close()

	_, err = GroundAndClick(context.Background(), newTestClient(t, srv.URL),
		shot, "thing", executor.Scale{}, &executor.Logging{W: &bytes.Buffer{}})
	if err == nil {
		t.Fatal("expected an error")
	}
	apiErr, ok := coasty.AsAPIError(err)
	if !ok || apiErr.Code != coasty.CodeInvalidScreenshot || apiErr.RequestID != "req_g_1" {
		t.Errorf("error = %v, want INVALID_SCREENSHOT with request_id req_g_1", err)
	}
}

func TestGroundAndClickValidatesElement(t *testing.T) {
	_, err := GroundAndClick(context.Background(),
		coasty.NewClient(coasty.WithAPIKey(testAPIKey)),
		exutil.Screenshot{B64: strings.Repeat("A", 200), Width: 64, Height: 64},
		"", executor.Scale{}, &executor.Logging{W: &bytes.Buffer{}})
	if err == nil || !strings.Contains(err.Error(), "element") {
		t.Errorf("empty element must error before any HTTP call, got %v", err)
	}
}
