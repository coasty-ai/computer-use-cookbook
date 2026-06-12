package exutil

import (
	"bytes"
	"encoding/base64"
	"errors"
	"image"
	"image/color"
	"image/png"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestConfirmSpend(t *testing.T) {
	// Pin the env var so a developer's .env can never flip the outcome
	// (the process environment always wins over .env).
	t.Setenv("COASTY_CONFIRM_SPEND", "0")

	if err := ConfirmSpend(false, true); err != nil {
		t.Errorf("sandbox keys must skip the gate, got %v", err)
	}
	if err := ConfirmSpend(true, false); err != nil {
		t.Errorf("-confirm must pass the gate, got %v", err)
	}
	if err := ConfirmSpend(false, false); !errors.Is(err, ErrSpendNotConfirmed) {
		t.Errorf("unconfirmed spend = %v, want ErrSpendNotConfirmed", err)
	}

	t.Setenv("COASTY_CONFIRM_SPEND", "1")
	if err := ConfirmSpend(false, false); err != nil {
		t.Errorf("COASTY_CONFIRM_SPEND=1 must pass the gate, got %v", err)
	}
}

func TestPrintEstimate(t *testing.T) {
	var buf bytes.Buffer
	total := PrintEstimate(&buf, "predict loop", []EstimateLine{
		{Label: "10 steps x 5 credits", Credits: 50},
		{Label: "HD surcharge x 10", Credits: 10},
	}, false)
	if total != 60 {
		t.Errorf("total = %d, want 60", total)
	}
	out := buf.String()
	for _, want := range []string{"predict loop", "10 steps x 5 credits", "$0.50", "total: 60 credits ($0.60)"} {
		if !strings.Contains(out, want) {
			t.Errorf("estimate output missing %q:\n%s", want, out)
		}
	}

	buf.Reset()
	PrintEstimate(&buf, "ground", []EstimateLine{{Label: "1 ground call", Credits: 3}}, true)
	if !strings.Contains(buf.String(), "$0 (sandbox key, never bills)") {
		t.Errorf("sandbox estimate must be labeled $0 (sandbox):\n%s", buf.String())
	}
}

// writeTestPNG writes a w x h PNG into dir and returns its path.
func writeTestPNG(t *testing.T, dir string, w, h int) string {
	t.Helper()
	img := image.NewRGBA(image.Rect(0, 0, w, h))
	for x := 0; x < w; x++ {
		for y := 0; y < h; y++ {
			img.Set(x, y, color.RGBA{R: uint8(x), G: uint8(y), B: 128, A: 255})
		}
	}
	var buf bytes.Buffer
	if err := png.Encode(&buf, img); err != nil {
		t.Fatalf("encoding test PNG: %v", err)
	}
	path := filepath.Join(dir, "shot.png")
	if err := os.WriteFile(path, buf.Bytes(), 0o600); err != nil {
		t.Fatalf("writing test PNG: %v", err)
	}
	return path
}

func TestLoadPNG(t *testing.T) {
	path := writeTestPNG(t, t.TempDir(), 64, 40)
	shot, err := LoadPNG(path)
	if err != nil {
		t.Fatalf("LoadPNG() error = %v", err)
	}
	if shot.Width != 64 || shot.Height != 40 {
		t.Errorf("dims = %dx%d, want 64x40", shot.Width, shot.Height)
	}
	if len(shot.B64) <= minScreenshotB64Len {
		t.Errorf("base64 length %d must exceed the API minimum %d", len(shot.B64), minScreenshotB64Len)
	}
	if _, err := base64.StdEncoding.DecodeString(shot.B64); err != nil {
		t.Errorf("B64 is not valid standard base64: %v", err)
	}
	if strings.HasPrefix(shot.B64, "data:") {
		t.Error("B64 must not carry a data: prefix")
	}
	if shot.IsHD() {
		t.Error("64x40 must not be HD")
	}
	if !(Screenshot{Width: 1281, Height: 720}).IsHD() {
		t.Error("1281x720 must be HD (strict boundary)")
	}
	if (Screenshot{Width: 1280, Height: 720}).IsHD() {
		t.Error("exactly 1280x720 is NOT HD")
	}
}

func TestLoadPNGErrors(t *testing.T) {
	if _, err := LoadPNG(filepath.Join(t.TempDir(), "missing.png")); err == nil {
		t.Error("missing file must error")
	}
	notPNG := filepath.Join(t.TempDir(), "not.png")
	if err := os.WriteFile(notPNG, []byte("definitely not a png"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadPNG(notPNG); err == nil {
		t.Error("non-PNG file must error")
	}
}
