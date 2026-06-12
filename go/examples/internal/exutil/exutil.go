// Package exutil holds the small shared scaffolding of the Go examples:
// the spend gate (-confirm flag / COASTY_CONFIRM_SPEND=1), itemized cost
// estimate printing, and the stub PNG screenshot loader.
package exutil

import (
	"bytes"
	"encoding/base64"
	"errors"
	"fmt"
	"image/png"
	"io"
	"os"

	"github.com/coasty-ai/computer-use-cookbook/go/coasty"
)

// minScreenshotB64Len is the API's minimum base64 screenshot length
// (screenshots must be > 100 chars).
const minScreenshotB64Len = 100

// ErrSpendNotConfirmed is returned by ConfirmSpend when a billable call was
// not explicitly confirmed.
var ErrSpendNotConfirmed = errors.New(
	"spend not confirmed: re-run with -confirm or set COASTY_CONFIRM_SPEND=1 " +
		"(sandbox keys sk-coasty-test-* never bill and skip this gate)")

// ConfirmSpend gates billable calls. It allows the run when the key is a
// sandbox key (never bills), when -confirm was passed, or when
// COASTY_CONFIRM_SPEND=1 is set (process env or repo-root .env); otherwise
// it returns ErrSpendNotConfirmed.
func ConfirmSpend(confirmFlag, sandbox bool) error {
	if sandbox || confirmFlag || coasty.Env("COASTY_CONFIRM_SPEND") == "1" {
		return nil
	}
	return ErrSpendNotConfirmed
}

// EstimateLine is one row of an itemized cost estimate.
type EstimateLine struct {
	Label   string
	Credits int
}

// PrintEstimate writes an itemized cost estimate (1 credit = $0.01) and
// returns the total credits. Sandbox keys are labeled "$0 (sandbox)".
func PrintEstimate(w io.Writer, title string, lines []EstimateLine, sandbox bool) int {
	total := 0
	fmt.Fprintf(w, "Cost estimate — %s\n", title)
	for _, line := range lines {
		fmt.Fprintf(w, "  %-52s %5d credits  (%s)\n",
			line.Label, line.Credits, coasty.FormatCreditsUSD(line.Credits))
		total += line.Credits
	}
	if sandbox {
		fmt.Fprintf(w, "  total: %d credits — $0 (sandbox key, never bills)\n", total)
	} else {
		fmt.Fprintf(w, "  total: %d credits (%s)\n", total, coasty.FormatCreditsUSD(total))
	}
	return total
}

// Screenshot is a base64 PNG plus its pixel dimensions — the dimensions you
// pass as screen_width / screen_height so returned coordinates land in the
// same space.
type Screenshot struct {
	B64    string // standard base64, no "data:" prefix
	Width  int
	Height int
}

// IsHD reports whether this screenshot bills the +1 HD surcharge
// (width > 1280 or height > 720, strictly).
func (s Screenshot) IsHD() bool { return coasty.IsHD(s.Width, s.Height) }

// Size returns the dimensions as a cost-estimator ImageSize.
func (s Screenshot) Size() coasty.ImageSize {
	return coasty.ImageSize{Width: s.Width, Height: s.Height}
}

// LoadPNG reads a PNG file into a Screenshot: raw bytes base64-encoded (no
// "data:" prefix) plus the decoded pixel dimensions. It rejects files whose
// base64 form is at or under the API's 100-char minimum.
func LoadPNG(path string) (Screenshot, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return Screenshot{}, fmt.Errorf("exutil: reading screenshot: %w", err)
	}
	cfg, err := png.DecodeConfig(bytes.NewReader(data))
	if err != nil {
		return Screenshot{}, fmt.Errorf("exutil: decoding %s as PNG: %w", path, err)
	}
	b64 := base64.StdEncoding.EncodeToString(data)
	if len(b64) <= minScreenshotB64Len {
		return Screenshot{}, fmt.Errorf(
			"exutil: %s is too small: the API requires base64 screenshots longer than %d chars (got %d)",
			path, minScreenshotB64Len, len(b64))
	}
	return Screenshot{B64: b64, Width: cfg.Width, Height: cfg.Height}, nil
}
