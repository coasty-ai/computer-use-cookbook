// Command predict-loop demonstrates the local agent loop against the
// stateless predict endpoint: screenshot -> predict -> execute -> repeat.
//
// Purpose: drive any screen (your desktop, a browser, an emulator) with
// natural language, looping while the model says status == "continue".
//
// Flow:
//  1. Capture a screenshot via the injected ScreenshotSource (the stub
//     FileSource re-reads the PNG from -screenshot each step).
//  2. POST /v1/predict with the screenshot, its dimensions and -instruction.
//  3. Execute every returned action through the injected executor.Executor
//     (here: a logging executor that prints what a real one would do,
//     defensively decoding BOTH documented param shapes — key|keys,
//     ms|seconds, direction+amount|signed clicks, from_x|x1 — and scaling
//     model-space coordinates back to the real screen).
//  4. Repeat while status == "continue", bounded by -max-steps; stop on
//     "done" or "fail".
//
// Endpoints: POST /v1/predict (scope "predict").
//
// Estimated cost (coasty cost package, printed before any call): 5 credits
// per step (coasty.EstimatePredictCredits), +1/step when the screenshot is
// HD (width > 1280 or height > 720 — exactly 1280x720 is not HD), +3/step
// on -cua-version v1. Worst case = per-step credits x -max-steps. Billable
// calls are gated behind -confirm / COASTY_CONFIRM_SPEND=1 unless the key
// is a sandbox key (sk-coasty-test-*, never billed).
//
// Wiring a real screen-capture / input library (robotgo,
// kbinani/screenshot, ...) is out of scope for this stdlib-only cookbook:
// implement ScreenshotSource and executor.Executor against your library of
// choice and hand them to RunLoop.
//
// Usage:
//
//	predict-loop -screenshot desk.png -instruction "Open the settings menu" \
//	    [-max-steps 10] [-real-width 2560 -real-height 1440] [-cua-version v3] [-confirm]
package main

import (
	"context"
	"flag"
	"fmt"
	"io"
	"os"

	"github.com/coasty-ai/computer-use-cookbook/go/coasty"
	"github.com/coasty-ai/computer-use-cookbook/go/examples/internal/executor"
	"github.com/coasty-ai/computer-use-cookbook/go/examples/internal/exutil"
)

// ScreenshotSource provides the current screen as a base64 PNG plus its
// pixel dimensions (sent as screen_width / screen_height so returned
// coordinates come back in the same space).
type ScreenshotSource interface {
	Capture(ctx context.Context) (exutil.Screenshot, error)
}

// FileSource is the stub ScreenshotSource: it re-reads a PNG from disk on
// every capture. Swap it for a real capture library in production.
type FileSource struct {
	Path string
}

// Capture implements ScreenshotSource.
func (f FileSource) Capture(context.Context) (exutil.Screenshot, error) {
	return exutil.LoadPNG(f.Path)
}

// StopReason explains why RunLoop stopped.
type StopReason string

const (
	// StopDone means the model reported the task complete.
	StopDone StopReason = "done"
	// StopFail means the model reported it is blocked.
	StopFail StopReason = "fail"
	// StopMaxSteps means the step budget ran out while status was "continue".
	StopMaxSteps StopReason = "max_steps"
)

// LoopOptions configures RunLoop.
type LoopOptions struct {
	Instruction string
	MaxSteps    int               // hard bound on predict calls (default 10)
	CUAVersion  coasty.CUAVersion // "" = server default (v3)
	Log         io.Writer         // defaults to io.Discard
}

// LoopResult summarizes a finished loop.
type LoopResult struct {
	Reason      StopReason
	Steps       int // predict calls made
	FinalStatus coasty.PredictStatus
	Reasoning   string // reasoning of the final step
	CreditsUsed int    // sum of usage.credits_charged
}

// RunLoop is the pure, testable core: capture -> predict -> execute,
// looping while status == "continue" and steps remain. API and executor
// errors abort the loop (run errors carry the request_id via APIError).
func RunLoop(ctx context.Context, client *coasty.Client, src ScreenshotSource, exec executor.Executor, opts LoopOptions) (*LoopResult, error) {
	if opts.Instruction == "" {
		return nil, fmt.Errorf("predict-loop: instruction must not be empty")
	}
	if opts.MaxSteps <= 0 {
		opts.MaxSteps = 10
	}
	logw := opts.Log
	if logw == nil {
		logw = io.Discard
	}

	result := &LoopResult{}
	for step := 1; step <= opts.MaxSteps; step++ {
		shot, err := src.Capture(ctx)
		if err != nil {
			return nil, fmt.Errorf("predict-loop: capturing screenshot (step %d): %w", step, err)
		}
		resp, err := client.Predict(ctx, &coasty.PredictRequest{
			Screenshot:   shot.B64,
			Instruction:  opts.Instruction,
			CUAVersion:   opts.CUAVersion,
			ScreenWidth:  shot.Width,
			ScreenHeight: shot.Height,
		})
		if err != nil {
			return nil, fmt.Errorf("predict-loop: step %d: %w", step, err)
		}

		result.Steps = step
		result.FinalStatus = resp.Status
		result.Reasoning = resp.Reasoning
		result.CreditsUsed += resp.Usage.CreditsCharged
		fmt.Fprintf(logw, "step %d: status=%s actions=%d credits=%d request_id=%s\n",
			step, resp.Status, len(resp.Actions), resp.Usage.CreditsCharged, resp.RequestID)
		if resp.Reasoning != "" {
			fmt.Fprintf(logw, "  reasoning: %s\n", resp.Reasoning)
		}

		for _, action := range resp.Actions {
			if err := exec.Execute(ctx, action); err != nil {
				return nil, fmt.Errorf("predict-loop: executing %s action (step %d): %w",
					action.ActionType, step, err)
			}
		}

		switch resp.Status {
		case coasty.PredictStatusContinue:
			// Keep looping: capture a fresh screenshot and predict again.
		case coasty.PredictStatusDone:
			result.Reason = StopDone
			return result, nil
		case coasty.PredictStatusFail:
			result.Reason = StopFail
			return result, nil
		default:
			return nil, fmt.Errorf("predict-loop: step %d: unexpected status %q (request_id %s)",
				step, resp.Status, resp.RequestID)
		}
	}
	result.Reason = StopMaxSteps
	return result, nil
}

func main() {
	var (
		screenshotPath = flag.String("screenshot", "", "path to a PNG used by the stub screenshot source (required)")
		instruction    = flag.String("instruction", "", "natural-language task for the agent (required)")
		maxSteps       = flag.Int("max-steps", 10, "maximum predict calls before stopping")
		realWidth      = flag.Int("real-width", 0, "real screen width when the PNG was pre-downscaled (default: PNG width)")
		realHeight     = flag.Int("real-height", 0, "real screen height when the PNG was pre-downscaled (default: PNG height)")
		cuaVersion     = flag.String("cua-version", "", "cua_version: v1 | v3 | v4 (default: server default v3)")
		confirm        = flag.Bool("confirm", false, "confirm spending on a non-sandbox key")
	)
	flag.Parse()
	if err := run(*screenshotPath, *instruction, *maxSteps, *realWidth, *realHeight, *cuaVersion, *confirm); err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}

func run(screenshotPath, instruction string, maxSteps, realWidth, realHeight int, cuaVersion string, confirm bool) error {
	if screenshotPath == "" || instruction == "" {
		flag.Usage()
		return fmt.Errorf("-screenshot and -instruction are required")
	}
	if maxSteps <= 0 {
		return fmt.Errorf("-max-steps must be positive, got %d", maxSteps)
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
	version := coasty.CUAVersion(cuaVersion)

	perStep := coasty.EstimatePredictCredits(coasty.InferenceCostInput{
		CUAVersion: version,
		Screenshot: shot.Size(),
	})
	lines := []exutil.EstimateLine{
		{Label: fmt.Sprintf("up to %d predict steps x %d credits", maxSteps, perStep), Credits: perStep * maxSteps},
	}
	if shot.IsHD() {
		lines = append(lines, exutil.EstimateLine{
			Label:   fmt.Sprintf("(included above: +1 HD surcharge per step — %dx%d > 1280x720)", shot.Width, shot.Height),
			Credits: 0,
		})
	}
	exutil.PrintEstimate(os.Stdout, "predict-loop (POST /v1/predict)", lines, client.IsSandbox())
	if err := exutil.ConfirmSpend(confirm, client.IsSandbox()); err != nil {
		return err
	}

	scale := executor.NewScale(realWidth, realHeight, shot.Width, shot.Height)
	if !scale.Identity() {
		fmt.Printf("coordinate scaling: model %dx%d -> real %dx%d (x%.3g, x%.3g)\n",
			shot.Width, shot.Height, realWidth, realHeight, scale.X, scale.Y)
	}

	result, err := RunLoop(context.Background(), client,
		FileSource{Path: screenshotPath},
		&executor.Logging{W: os.Stdout, Scale: scale},
		LoopOptions{
			Instruction: instruction,
			MaxSteps:    maxSteps,
			CUAVersion:  version,
			Log:         os.Stdout,
		})
	if err != nil {
		return err
	}

	fmt.Printf("\nloop finished: reason=%s steps=%d credits_used=%d (%s)\n",
		result.Reason, result.Steps, result.CreditsUsed, coasty.FormatCreditsUSD(result.CreditsUsed))
	if result.Reason == StopFail {
		return fmt.Errorf("agent reported failure: %s", result.Reasoning)
	}
	if result.Reason == StopMaxSteps {
		fmt.Println("note: step budget exhausted while the model still wanted to continue")
	}
	return nil
}
