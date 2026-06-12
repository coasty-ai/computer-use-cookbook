// Package executor defines the Executor interface the examples drive their
// predicted actions through, plus a logging implementation that prints what
// a real executor would do instead of touching the screen.
//
// The docs publish TWO competing action-param shapes (see the discrepancy
// note in docs/API_NOTES.md): the canonical reference table (key_press
// {key}, wait {ms}, scroll {direction, amount}, drag {from_x...}) and the
// "local automation" section (key_press {keys}, wait {seconds}, scroll
// {signed clicks}, drag {x1,y1,x2,y2}, click {x,y,button?,clicks?}, plus a
// "raw" type carrying pyautogui source). Everything here is defensive and
// accepts BOTH shapes; "raw" code is logged, never executed.
//
// Coordinates come back in the space of the screenshot you SENT. Scale maps
// them back to real screen pixels (factor = real / sent) — see the
// "#1 pitfall" note in the docs.
//
// Wiring a real input library (robotgo, etc.) is out of scope for this
// stdlib-only cookbook: implement Executor against your library of choice
// and reuse FormatAction / Scale for the defensive decoding.
package executor

import (
	"context"
	"fmt"
	"io"
	"math"
	"os"
	"strings"

	"github.com/coasty-ai/computer-use-cookbook/go/coasty"
)

// Executor executes (or simulates) one predicted action.
type Executor interface {
	Execute(ctx context.Context, action coasty.Action) error
}

// Scale maps model-space coordinates (the screenshot you SENT) back to real
// screen pixels: factor = real / sent. The zero value is the identity.
type Scale struct {
	X float64
	Y float64
}

// NewScale builds the real/sent scale factors. Non-positive dimensions fall
// back to a 1:1 factor on that axis.
func NewScale(realW, realH, sentW, sentH int) Scale {
	s := Scale{X: 1, Y: 1}
	if realW > 0 && sentW > 0 {
		s.X = float64(realW) / float64(sentW)
	}
	if realH > 0 && sentH > 0 {
		s.Y = float64(realH) / float64(sentH)
	}
	return s
}

func (s Scale) factors() (fx, fy float64) {
	fx, fy = s.X, s.Y
	if fx <= 0 {
		fx = 1
	}
	if fy <= 0 {
		fy = 1
	}
	return fx, fy
}

// Apply scales a model-space point to real pixels (rounded to nearest).
func (s Scale) Apply(x, y float64) (int, int) {
	fx, fy := s.factors()
	return int(math.Round(x * fx)), int(math.Round(y * fy))
}

// Identity reports whether the scale is a no-op (1:1 on both axes).
func (s Scale) Identity() bool {
	fx, fy := s.factors()
	return fx == 1 && fy == 1
}

// Logging is an Executor that prints one line per action instead of
// executing it. It decodes params defensively across both documented shapes
// and applies coordinate scaling. Raw code is logged, never executed.
type Logging struct {
	W      io.Writer // defaults to os.Stdout
	Scale  Scale
	Prefix string // optional line prefix, e.g. "step 3 "
}

// Execute formats the action and writes it to W. Missing required params
// are an error (never silently skipped); unknown action types are logged as
// such and acknowledged.
func (l *Logging) Execute(_ context.Context, action coasty.Action) error {
	line, err := FormatAction(action, l.Scale)
	if err != nil {
		return err
	}
	if action.Description != "" {
		line += " — " + action.Description
	}
	w := l.W
	if w == nil {
		w = os.Stdout
	}
	_, werr := fmt.Fprintf(w, "%s[exec] %s\n", l.Prefix, line)
	if werr != nil {
		return fmt.Errorf("executor: writing log line: %w", werr)
	}
	return nil
}

// FormatAction renders one action as a human-readable line, accepting both
// documented param shapes and applying coordinate scaling. It returns an
// error when required params are missing in BOTH shapes.
func FormatAction(a coasty.Action, scale Scale) (string, error) {
	switch a.ActionType {
	case coasty.ActionClick, coasty.ActionMove:
		return formatPoint(a, scale)
	case coasty.ActionTypeText:
		text, ok := a.StringParam("text")
		if !ok {
			return "", missingParams(a, "text")
		}
		return fmt.Sprintf("type_text %q", text), nil
	case coasty.ActionKeyPress:
		// Canonical: {key}; local-automation alt: {keys: [...]} in order.
		if key, ok := a.StringParam("key"); ok {
			return fmt.Sprintf("key_press %q", key), nil
		}
		if keys, ok := a.StringsParam("keys"); ok && len(keys) > 0 {
			return fmt.Sprintf("key_press sequence [%s]", strings.Join(keys, " ")), nil
		}
		return "", missingParams(a, `key (or alt "keys" list)`)
	case coasty.ActionKeyCombo:
		keys, ok := a.StringsParam("keys")
		if !ok || len(keys) == 0 {
			return "", missingParams(a, "keys")
		}
		return "key_combo " + strings.Join(keys, "+"), nil
	case coasty.ActionScroll:
		return formatScroll(a, scale)
	case coasty.ActionDrag:
		return formatDrag(a, scale)
	case coasty.ActionWait:
		// Canonical: {ms}; local-automation alt: {seconds}.
		ms, ok := a.FloatParam("ms")
		if !ok {
			secs, okSecs := a.FloatParam("seconds")
			if !okSecs {
				return "", missingParams(a, `ms (or alt "seconds")`)
			}
			ms = secs * 1000
		}
		return fmt.Sprintf("wait %dms", int64(math.Round(ms))), nil
	case coasty.ActionDone:
		return "done — task complete, stop the loop", nil
	case coasty.ActionFail:
		if reason, ok := a.StringParam("reason"); ok && reason != "" {
			return "fail — agent reported failure: " + reason, nil
		}
		return "fail — agent reported failure (no reason given)", nil
	case coasty.ActionRaw:
		code, _ := a.StringParam("code")
		if code == "" {
			code = a.RawCode
		}
		return fmt.Sprintf(
			"raw — NOT executing model-generated code by default (%d chars): %s",
			len(code), firstLine(code, 80)), nil
	default:
		// Forward-compatible: log (never silently drop), do not fail the loop.
		return fmt.Sprintf("unknown action type %q (params: %v) — logged and skipped",
			a.ActionType, a.Params), nil
	}
}

// formatPoint handles click / move: canonical {x, y}, plus the
// local-automation click extras {button?, clicks?}.
func formatPoint(a coasty.Action, scale Scale) (string, error) {
	x, okX := a.FloatParam("x")
	y, okY := a.FloatParam("y")
	if !okX || !okY {
		return "", missingParams(a, "x, y")
	}
	rx, ry := scale.Apply(x, y)
	line := fmt.Sprintf("%s at (%d, %d)%s", a.ActionType, rx, ry, modelNote(scale, x, y))
	if button, ok := a.StringParam("button"); ok && button != "" {
		line += " button=" + button
	}
	if a.ActionType == coasty.ActionClick {
		if clicks, ok := a.IntParam("clicks"); ok && clicks > 1 {
			line += fmt.Sprintf(" clicks=%d", clicks)
		}
	}
	return line, nil
}

// formatScroll handles canonical {x, y, direction, amount} and the
// local-automation alt {clicks} where +clicks scrolls up and -clicks down
// (pyautogui convention).
func formatScroll(a coasty.Action, scale Scale) (string, error) {
	direction, _ := a.StringParam("direction")
	amount, okAmount := a.FloatParam("amount")
	if !okAmount {
		clicks, okClicks := a.FloatParam("clicks")
		if !okClicks {
			return "", missingParams(a, `direction+amount (or alt signed "clicks")`)
		}
		amount = math.Abs(clicks)
		if direction == "" {
			if clicks >= 0 {
				direction = "up"
			} else {
				direction = "down"
			}
		}
	}
	if direction == "" {
		return "", missingParams(a, "direction")
	}
	line := fmt.Sprintf("scroll %s %g", direction, amount)
	x, okX := a.FloatParam("x")
	y, okY := a.FloatParam("y")
	if okX && okY {
		rx, ry := scale.Apply(x, y)
		line += fmt.Sprintf(" at (%d, %d)%s", rx, ry, modelNote(scale, x, y))
	}
	return line, nil
}

// formatDrag handles canonical {from_x, from_y, to_x, to_y} and the
// local-automation alt {x1, y1, x2, y2}.
func formatDrag(a coasty.Action, scale Scale) (string, error) {
	read := func(canonical, alt string) (float64, bool) {
		if v, ok := a.FloatParam(canonical); ok {
			return v, true
		}
		return a.FloatParam(alt)
	}
	fx, ok1 := read("from_x", "x1")
	fy, ok2 := read("from_y", "y1")
	tx, ok3 := read("to_x", "x2")
	ty, ok4 := read("to_y", "y2")
	if !ok1 || !ok2 || !ok3 || !ok4 {
		return "", missingParams(a, "from_x/from_y/to_x/to_y (or alt x1/y1/x2/y2)")
	}
	rfx, rfy := scale.Apply(fx, fy)
	rtx, rty := scale.Apply(tx, ty)
	line := fmt.Sprintf("drag (%d, %d) -> (%d, %d)", rfx, rfy, rtx, rty)
	if !scale.Identity() {
		line += fmt.Sprintf(" [model (%g, %g) -> (%g, %g)]", fx, fy, tx, ty)
	}
	if button, ok := a.StringParam("button"); ok && button != "" {
		line += " button=" + button
	}
	return line, nil
}

func modelNote(scale Scale, x, y float64) string {
	if scale.Identity() {
		return ""
	}
	return fmt.Sprintf(" [model (%g, %g)]", x, y)
}

func missingParams(a coasty.Action, want string) error {
	return fmt.Errorf("executor: %s action missing required params (want %s, got %v)",
		a.ActionType, want, a.Params)
}

func firstLine(s string, max int) string {
	if i := strings.IndexByte(s, '\n'); i >= 0 {
		s = s[:i]
	}
	if len(s) > max {
		s = s[:max] + "…"
	}
	return s
}
