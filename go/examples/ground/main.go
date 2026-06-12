// Command ground locates a UI element on a screenshot and "clicks" it.
//
// Purpose: turn a natural-language element description ("the blue Submit
// button") into pixel coordinates with POST /v1/ground, then click those
// coordinates through the logging executor — demonstrating the #1 pitfall:
// coordinates come back in the space of the screenshot you SENT, so when
// the screenshot was downscaled they must be multiplied back up
// (factor = real / sent) before clicking.
//
// Flow:
//  1. Load the PNG from -screenshot (its dimensions are sent as
//     screen_width / screen_height).
//  2. POST /v1/ground with the screenshot and -element.
//  3. Scale the returned (x, y) from model space to the real screen
//     (-real-width / -real-height, defaulting to the PNG's own size = 1:1)
//     and execute a click action through the logging executor.
//
// Endpoints: POST /v1/ground (scope "ground").
//
// Estimated cost (coasty cost package, printed before the call): 3 credits
// (coasty.EstimateGroundCredits), +1 when the screenshot is HD (width >
// 1280 or height > 720; exactly 1280x720 is not HD). Gated behind -confirm
// / COASTY_CONFIRM_SPEND=1 unless the key is a sandbox key
// (sk-coasty-test-*, never billed).
//
// Wiring a real input library (robotgo, ...) is out of scope for this
// stdlib-only cookbook: swap the logging executor for your own
// executor.Executor to really click.
//
// Usage:
//
//	ground -screenshot desk.png -element "the search field" \
//	    [-real-width 2560 -real-height 1440] [-confirm]
package main

import (
	"context"
	"flag"
	"fmt"
	"os"

	"github.com/coasty-ai/computer-use-cookbook/go/coasty"
	"github.com/coasty-ai/computer-use-cookbook/go/examples/internal/executor"
	"github.com/coasty-ai/computer-use-cookbook/go/examples/internal/exutil"
)

// GroundResult is the outcome of GroundAndClick.
type GroundResult struct {
	ModelX, ModelY int // coordinates in the sent-screenshot space
	RealX, RealY   int // scaled to the real screen
	Credits        int // usage.credits_charged
}

// GroundAndClick is the pure, testable core: ground the element, scale the
// returned coordinates back to the real screen, and click them through the
// executor (the executor applies the same scale when it logs/executes).
func GroundAndClick(ctx context.Context, client *coasty.Client, shot exutil.Screenshot, element string, scale executor.Scale, exec executor.Executor) (*GroundResult, error) {
	if element == "" {
		return nil, fmt.Errorf("ground: element description must not be empty")
	}
	resp, err := client.Ground(ctx, &coasty.GroundRequest{
		Screenshot:   shot.B64,
		Element:      element,
		ScreenWidth:  shot.Width,
		ScreenHeight: shot.Height,
	})
	if err != nil {
		return nil, fmt.Errorf("ground: locating %q: %w", element, err)
	}

	realX, realY := scale.Apply(float64(resp.X), float64(resp.Y))
	click := coasty.Action{
		ActionType:  coasty.ActionClick,
		Params:      map[string]any{"x": resp.X, "y": resp.Y}, // model space; the executor scales
		Description: fmt.Sprintf("click grounded element %q", element),
	}
	if err := exec.Execute(ctx, click); err != nil {
		return nil, fmt.Errorf("ground: clicking %q: %w", element, err)
	}
	return &GroundResult{
		ModelX: resp.X, ModelY: resp.Y,
		RealX: realX, RealY: realY,
		Credits: resp.Usage.CreditsCharged,
	}, nil
}

func main() {
	var (
		screenshotPath = flag.String("screenshot", "", "path to the PNG screenshot to ground on (required)")
		element        = flag.String("element", "", "natural-language description of the element to locate (required)")
		realWidth      = flag.Int("real-width", 0, "real screen width when the PNG was pre-downscaled (default: PNG width)")
		realHeight     = flag.Int("real-height", 0, "real screen height when the PNG was pre-downscaled (default: PNG height)")
		confirm        = flag.Bool("confirm", false, "confirm spending on a non-sandbox key")
	)
	flag.Parse()
	if err := run(*screenshotPath, *element, *realWidth, *realHeight, *confirm); err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}

func run(screenshotPath, element string, realWidth, realHeight int, confirm bool) error {
	if screenshotPath == "" || element == "" {
		flag.Usage()
		return fmt.Errorf("-screenshot and -element are required")
	}

	shot, err := exutil.LoadPNG(screenshotPath)
	if err != nil {
		return err
	}
	if realWidth <= 0 {
		realWidth = shot.Width
	}
	if realHeight <= 0 {
		realHeight = shot.Height
	}

	client := coasty.NewClient() // reads COASTY_API_KEY / COASTY_BASE_URL (.env fallback)

	credits := coasty.EstimateGroundCredits(shot.Size())
	label := "1 ground call (3 credits"
	if shot.IsHD() {
		label += " + 1 HD surcharge"
	}
	label += ")"
	exutil.PrintEstimate(os.Stdout, "ground (POST /v1/ground)",
		[]exutil.EstimateLine{{Label: label, Credits: credits}}, client.IsSandbox())
	if err := exutil.ConfirmSpend(confirm, client.IsSandbox()); err != nil {
		return err
	}

	scale := executor.NewScale(realWidth, realHeight, shot.Width, shot.Height)
	if !scale.Identity() {
		fmt.Printf("coordinate scaling: model %dx%d -> real %dx%d (x%.3g, x%.3g)\n",
			shot.Width, shot.Height, realWidth, realHeight, scale.X, scale.Y)
	}

	result, err := GroundAndClick(context.Background(), client, shot, element,
		scale, &executor.Logging{W: os.Stdout, Scale: scale})
	if err != nil {
		return err
	}

	fmt.Printf("\ngrounded %q: model (%d, %d) -> real (%d, %d); charged %d credits (%s)\n",
		element, result.ModelX, result.ModelY, result.RealX, result.RealY,
		result.Credits, coasty.FormatCreditsUSD(result.Credits))
	return nil
}
