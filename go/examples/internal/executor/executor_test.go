package executor

import (
	"bytes"
	"context"
	"strings"
	"testing"

	"github.com/coasty-ai/computer-use-cookbook/go/coasty"
)

func action(t coasty.ActionType, params map[string]any) coasty.Action {
	return coasty.Action{ActionType: t, Params: params}
}

// TestFormatActionBothShapes covers the canonical reference params AND the
// "local automation" alt shapes from the documented discrepancy.
func TestFormatActionBothShapes(t *testing.T) {
	identity := Scale{}
	double := NewScale(2560, 1440, 1280, 720)

	tests := []struct {
		name  string
		a     coasty.Action
		scale Scale
		want  string
	}{
		{
			name:  "click canonical identity",
			a:     action(coasty.ActionClick, map[string]any{"x": 100.0, "y": 50.0}),
			scale: identity,
			want:  "click at (100, 50)",
		},
		{
			name:  "click scaled",
			a:     action(coasty.ActionClick, map[string]any{"x": 100.0, "y": 50.0}),
			scale: double,
			want:  "click at (200, 100) [model (100, 50)]",
		},
		{
			name: "click alt shape with button and clicks",
			a: action(coasty.ActionClick, map[string]any{
				"x": 10.0, "y": 20.0, "button": "right", "clicks": 2.0,
			}),
			scale: identity,
			want:  "click at (10, 20) button=right clicks=2",
		},
		{
			name:  "move scaled",
			a:     action(coasty.ActionMove, map[string]any{"x": 640.0, "y": 360.0}),
			scale: double,
			want:  "move at (1280, 720) [model (640, 360)]",
		},
		{
			name:  "type_text",
			a:     action(coasty.ActionTypeText, map[string]any{"text": "hello"}),
			scale: identity,
			want:  `type_text "hello"`,
		},
		{
			name:  "key_press canonical key",
			a:     action(coasty.ActionKeyPress, map[string]any{"key": "enter"}),
			scale: identity,
			want:  `key_press "enter"`,
		},
		{
			name:  "key_press alt keys list",
			a:     action(coasty.ActionKeyPress, map[string]any{"keys": []any{"tab", "tab", "enter"}}),
			scale: identity,
			want:  "key_press sequence [tab tab enter]",
		},
		{
			name:  "key_combo",
			a:     action(coasty.ActionKeyCombo, map[string]any{"keys": []any{"ctrl", "c"}}),
			scale: identity,
			want:  "key_combo ctrl+c",
		},
		{
			name: "scroll canonical",
			a: action(coasty.ActionScroll, map[string]any{
				"x": 400.0, "y": 300.0, "direction": "down", "amount": 3.0,
			}),
			scale: identity,
			want:  "scroll down 3 at (400, 300)",
		},
		{
			name:  "scroll alt negative clicks means down",
			a:     action(coasty.ActionScroll, map[string]any{"clicks": -3.0}),
			scale: identity,
			want:  "scroll down 3",
		},
		{
			name:  "scroll alt positive clicks means up",
			a:     action(coasty.ActionScroll, map[string]any{"clicks": 2.0}),
			scale: identity,
			want:  "scroll up 2",
		},
		{
			name: "scroll alt clicks with explicit direction wins",
			a: action(coasty.ActionScroll, map[string]any{
				"clicks": 4.0, "direction": "left",
			}),
			scale: identity,
			want:  "scroll left 4",
		},
		{
			name: "drag canonical scaled",
			a: action(coasty.ActionDrag, map[string]any{
				"from_x": 10.0, "from_y": 20.0, "to_x": 30.0, "to_y": 40.0,
			}),
			scale: double,
			want:  "drag (20, 40) -> (60, 80) [model (10, 20) -> (30, 40)]",
		},
		{
			name: "drag alt shape",
			a: action(coasty.ActionDrag, map[string]any{
				"x1": 1.0, "y1": 2.0, "x2": 3.0, "y2": 4.0,
			}),
			scale: identity,
			want:  "drag (1, 2) -> (3, 4)",
		},
		{
			name:  "wait canonical ms",
			a:     action(coasty.ActionWait, map[string]any{"ms": 500.0}),
			scale: identity,
			want:  "wait 500ms",
		},
		{
			name:  "wait alt seconds",
			a:     action(coasty.ActionWait, map[string]any{"seconds": 1.5}),
			scale: identity,
			want:  "wait 1500ms",
		},
		{
			name:  "done",
			a:     action(coasty.ActionDone, nil),
			scale: identity,
			want:  "done — task complete, stop the loop",
		},
		{
			name:  "fail with reason",
			a:     action(coasty.ActionFail, map[string]any{"reason": "button is disabled"}),
			scale: identity,
			want:  "fail — agent reported failure: button is disabled",
		},
		{
			name:  "fail without reason",
			a:     action(coasty.ActionFail, nil),
			scale: identity,
			want:  "fail — agent reported failure (no reason given)",
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := FormatAction(tt.a, tt.scale)
			if err != nil {
				t.Fatalf("FormatAction() error = %v", err)
			}
			if got != tt.want {
				t.Errorf("FormatAction() = %q, want %q", got, tt.want)
			}
		})
	}
}

func TestFormatActionRawIsNeverExecuted(t *testing.T) {
	a := action(coasty.ActionRaw, map[string]any{"code": "import os\nos.system('rm -rf /')"})
	got, err := FormatAction(a, Scale{})
	if err != nil {
		t.Fatalf("FormatAction() error = %v", err)
	}
	if !strings.Contains(got, "NOT executing") {
		t.Errorf("raw action line %q must state the code is not executed", got)
	}
	if !strings.Contains(got, "import os") || strings.Contains(got, "rm -rf") {
		t.Errorf("raw action line %q should show only the first line of code", got)
	}
}

func TestFormatActionUnknownTypeIsLoggedNotFatal(t *testing.T) {
	a := action(coasty.ActionType("hover"), map[string]any{"x": 1.0})
	got, err := FormatAction(a, Scale{})
	if err != nil {
		t.Fatalf("FormatAction() error = %v (unknown types must not fail the loop)", err)
	}
	if !strings.Contains(got, `unknown action type "hover"`) {
		t.Errorf("FormatAction() = %q, want a logged unknown-type notice", got)
	}
}

func TestFormatActionMissingParams(t *testing.T) {
	tests := []struct {
		name string
		a    coasty.Action
	}{
		{"click without y", action(coasty.ActionClick, map[string]any{"x": 1.0})},
		{"type_text without text", action(coasty.ActionTypeText, nil)},
		{"key_press neither key nor keys", action(coasty.ActionKeyPress, nil)},
		{"key_combo without keys", action(coasty.ActionKeyCombo, nil)},
		{"scroll neither amount nor clicks", action(coasty.ActionScroll, map[string]any{"x": 1.0})},
		{"scroll amount without direction", action(coasty.ActionScroll, map[string]any{"amount": 2.0})},
		{"drag partial coords", action(coasty.ActionDrag, map[string]any{"from_x": 1.0, "from_y": 2.0, "to_x": 3.0})},
		{"wait neither ms nor seconds", action(coasty.ActionWait, nil)},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if _, err := FormatAction(tt.a, Scale{}); err == nil {
				t.Errorf("FormatAction(%v) expected a missing-params error", tt.a)
			}
		})
	}
}

func TestScale(t *testing.T) {
	s := NewScale(2560, 1440, 1280, 720)
	if x, y := s.Apply(640, 360); x != 1280 || y != 720 {
		t.Errorf("Apply(640,360) = (%d,%d), want (1280,720)", x, y)
	}
	if s.Identity() {
		t.Error("2x scale must not be identity")
	}
	var zero Scale
	if x, y := zero.Apply(33, 44); x != 33 || y != 44 {
		t.Errorf("zero Scale must be identity, got (%d,%d)", x, y)
	}
	if !zero.Identity() {
		t.Error("zero Scale.Identity() = false, want true")
	}
	if degenerate := NewScale(0, -1, 1280, 720); !degenerate.Identity() {
		t.Error("degenerate real dims must fall back to identity")
	}
}

func TestLoggingExecute(t *testing.T) {
	var buf bytes.Buffer
	exec := &Logging{W: &buf, Scale: NewScale(200, 100, 100, 50), Prefix: "step 1 "}
	a := coasty.Action{
		ActionType:  coasty.ActionClick,
		Params:      map[string]any{"x": 50.0, "y": 25.0},
		Description: "Click the submit button",
	}
	if err := exec.Execute(context.Background(), a); err != nil {
		t.Fatalf("Execute() error = %v", err)
	}
	got := buf.String()
	want := "step 1 [exec] click at (100, 50) [model (50, 25)] — Click the submit button\n"
	if got != want {
		t.Errorf("Execute() wrote %q, want %q", got, want)
	}

	if err := exec.Execute(context.Background(), action(coasty.ActionClick, nil)); err == nil {
		t.Error("Execute() with missing params must return an error")
	}
}
