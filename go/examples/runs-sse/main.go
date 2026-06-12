// Command runs-sse creates an autonomous task run and follows it to a
// terminal state, either by polling or by streaming Server-Sent Events.
//
// Purpose: show the full run lifecycle — create with an Idempotency-Key,
// watch progress, hand control back when the agent pauses for a human
// (awaiting_human -> resume with a note), and read the final billing and
// verification result.
//
// Flow:
//  1. POST /v1/runs with machine_id + task and an Idempotency-Key header
//     (generated when -idempotency-key is not given) so the create is safe
//     to retry.
//  2. Default mode: poll GET /v1/runs/{id} every -poll-interval until the
//     status is terminal. -events mode: stream GET /v1/runs/{id}/events
//     (SSE) instead — the client reconnects automatically with
//     Last-Event-ID after drops, replaying nothing twice, until the "done"
//     event.
//  3. In both modes, when the run reaches awaiting_human it is resumed via
//     POST /v1/runs/{id}/resume with -resume-note.
//  4. Print the final billing (credits_charged / cost_cents) and result
//     (passed / status / summary).
//
// Endpoints: POST /v1/runs, GET /v1/runs/{id}, GET /v1/runs/{id}/events
// (SSE), POST /v1/runs/{id}/resume (scopes "runs:write", "runs:read").
//
// Estimated cost (coasty cost package, printed before any call): 5 credits
// per completed step on v3/v4, 8 on v1 (coasty.RunStepCredits) — worst case
// -max-steps x step credits; run steps carry no HD/trajectory surcharges.
// Gated behind -confirm / COASTY_CONFIRM_SPEND=1 unless the key is a
// sandbox key (sk-coasty-test-*, never billed).
//
// Usage:
//
//	runs-sse -machine mch_test_1234 -task "Reconcile the invoice" \
//	    [-max-steps 10] [-events] [-poll-interval 2s] \
//	    [-resume-note "carry on"] [-idempotency-key my-key] [-confirm]
package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"time"

	"github.com/coasty-ai/computer-use-cookbook/go/coasty"
	"github.com/coasty-ai/computer-use-cookbook/go/examples/internal/exutil"
)

// AwaitingHumanHandler decides what to do when the run pauses for a human:
// return (note, true) to resume with that note, or (_, false) to keep
// waiting while a human intervenes out-of-band.
type AwaitingHumanHandler func(reason string) (note string, resume bool)

// newIdempotencyKey returns a fresh "runs-sse-<hex>" key (well under the
// 128-char limit, charset [A-Za-z0-9_-:]).
func newIdempotencyKey() (string, error) {
	var buf [16]byte
	if _, err := rand.Read(buf[:]); err != nil {
		return "", fmt.Errorf("runs-sse: generating idempotency key: %w", err)
	}
	return "runs-sse-" + hex.EncodeToString(buf[:]), nil
}

// resumeRun resumes an awaiting_human run, treating a 409 NOT_AWAITING_HUMAN
// as a benign race (someone else resumed first / the run just finished).
func resumeRun(ctx context.Context, client *coasty.Client, runID, note string, logw io.Writer) error {
	if _, err := client.ResumeRun(ctx, runID, note); err != nil {
		if apiErr, ok := coasty.AsAPIError(err); ok && apiErr.Code == coasty.CodeNotAwaitingHuman {
			fmt.Fprintf(logw, "resume race: run %s is no longer awaiting_human (request_id %s)\n",
				runID, apiErr.RequestID)
			return nil
		}
		return fmt.Errorf("runs-sse: resuming run %s: %w", runID, err)
	}
	fmt.Fprintf(logw, "resumed run %s with note %q\n", runID, note)
	return nil
}

// PollOptions configures PollUntilTerminal.
type PollOptions struct {
	Interval        time.Duration // default 2s
	OnAwaitingHuman AwaitingHumanHandler
	Log             io.Writer // defaults to io.Discard
}

// PollUntilTerminal polls GET /v1/runs/{id} until the run reaches a
// terminal state (succeeded / failed / cancelled / timed_out), resuming via
// the handler whenever it observes awaiting_human.
func PollUntilTerminal(ctx context.Context, client *coasty.Client, runID string, opts PollOptions) (*coasty.Run, error) {
	interval := opts.Interval
	if interval <= 0 {
		interval = 2 * time.Second
	}
	logw := opts.Log
	if logw == nil {
		logw = io.Discard
	}

	var lastStatus coasty.RunStatus
	for {
		run, err := client.GetRun(ctx, runID)
		if err != nil {
			return nil, fmt.Errorf("runs-sse: polling run %s: %w", runID, err)
		}
		if run.Status != lastStatus {
			fmt.Fprintf(logw, "run %s: status=%s steps=%d credits=%d\n",
				runID, run.Status, run.StepsCompleted, run.CreditsCharged)
			lastStatus = run.Status
		}
		if run.Status.Terminal() {
			return run, nil
		}
		if run.Status == coasty.RunStatusAwaitingHuman && opts.OnAwaitingHuman != nil {
			note, resume := opts.OnAwaitingHuman(run.AwaitingHumanReason)
			if resume {
				if err := resumeRun(ctx, client, runID, note, logw); err != nil {
					return nil, err
				}
			}
		}
		if err := sleepCtx(ctx, interval); err != nil {
			return nil, err
		}
	}
}

// StreamSummary aggregates what StreamEvents observed.
type StreamSummary struct {
	Events           int    // events delivered (each exactly once)
	LastEventID      string // persist to resume across restarts
	FinalStatus      string // last status seen (status/done events)
	BillingCredits   int    // last billing event credits_charged
	BillingCostCents int    // last billing event cost_cents
	Resumed          int    // how many times we resumed the run
}

// StreamOptions configures StreamEvents.
type StreamOptions struct {
	// LastEventID resumes the stream after a previously persisted seq.
	LastEventID     string
	OnAwaitingHuman AwaitingHumanHandler
	// OnEvent, when set, observes every delivered event (used by tests).
	OnEvent func(ev *coasty.RunEvent)
	Log     io.Writer // defaults to io.Discard
}

// eventData is the loose union of the documented SSE data payloads.
type eventData struct {
	Status         string `json:"status,omitempty"`
	Reason         string `json:"reason,omitempty"`
	StepsCompleted int    `json:"steps_completed,omitempty"`
	CreditsCharged int    `json:"credits_charged,omitempty"`
	CostCents      int    `json:"cost_cents,omitempty"`
}

// StreamEvents follows GET /v1/runs/{id}/events until the "done" event,
// reconnecting transparently with Last-Event-ID (handled by the coasty
// client: dropped connections resume at the cursor with no loss or
// duplication). awaiting_human events trigger the handler; billing and
// status events feed the summary.
func StreamEvents(ctx context.Context, client *coasty.Client, runID string, opts StreamOptions) (*StreamSummary, error) {
	logw := opts.Log
	if logw == nil {
		logw = io.Discard
	}

	stream, err := client.StreamRunEvents(ctx, runID, &coasty.StreamOptions{LastEventID: opts.LastEventID})
	if err != nil {
		return nil, fmt.Errorf("runs-sse: opening event stream for %s: %w", runID, err)
	}
	defer stream.Close()

	summary := &StreamSummary{LastEventID: opts.LastEventID}
	for {
		ev, err := stream.Next(ctx)
		if errors.Is(err, io.EOF) { // clean end: "done" was delivered
			return summary, nil
		}
		if err != nil {
			return nil, fmt.Errorf("runs-sse: streaming run %s (resume with Last-Event-ID %s): %w",
				runID, summary.LastEventID, err)
		}

		summary.Events++
		summary.LastEventID = stream.LastEventID()
		fmt.Fprintf(logw, "seq=%d event=%s data=%s\n", ev.Seq, ev.Type, ev.Data)
		if opts.OnEvent != nil {
			opts.OnEvent(ev)
		}

		var data eventData
		if len(ev.Data) > 0 {
			if err := json.Unmarshal(ev.Data, &data); err != nil {
				fmt.Fprintf(logw, "  (unparseable %s data: %v)\n", ev.Type, err)
			}
		}

		switch ev.Type {
		case coasty.RunEventStatus:
			if data.Status != "" {
				summary.FinalStatus = data.Status
			}
		case coasty.RunEventBilling:
			summary.BillingCredits = data.CreditsCharged
			summary.BillingCostCents = data.CostCents
		case coasty.RunEventAwaitingHuman:
			if opts.OnAwaitingHuman != nil {
				note, resume := opts.OnAwaitingHuman(data.Reason)
				if resume {
					if err := resumeRun(ctx, client, runID, note, logw); err != nil {
						return nil, err
					}
					summary.Resumed++
				}
			}
		case coasty.RunEventDone:
			if data.Status != "" {
				summary.FinalStatus = data.Status
			}
		}
	}
}

func sleepCtx(ctx context.Context, d time.Duration) error {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-t.C:
		return nil
	}
}

func main() {
	var (
		machineID      = flag.String("machine", "", "machine_id to run the task on (required)")
		task           = flag.String("task", "", "natural-language task (required)")
		maxSteps       = flag.Int("max-steps", 10, "max_steps budget for the run")
		cuaVersion     = flag.String("cua-version", "", "cua_version: v1 | v3 | v4 (default: server default v3)")
		events         = flag.Bool("events", false, "stream SSE events instead of polling")
		pollInterval   = flag.Duration("poll-interval", 2*time.Second, "poll interval (poll mode)")
		resumeNote     = flag.String("resume-note", "resumed automatically by the runs-sse example", "note sent when resuming an awaiting_human run")
		idempotencyKey = flag.String("idempotency-key", "", "Idempotency-Key for the create (default: generated)")
		confirm        = flag.Bool("confirm", false, "confirm spending on a non-sandbox key")
	)
	flag.Parse()
	if err := run(*machineID, *task, *maxSteps, *cuaVersion, *events, *pollInterval, *resumeNote, *idempotencyKey, *confirm); err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}

func run(machineID, task string, maxSteps int, cuaVersion string, events bool, pollInterval time.Duration, resumeNote, idempotencyKey string, confirm bool) error {
	if machineID == "" || task == "" {
		flag.Usage()
		return fmt.Errorf("-machine and -task are required")
	}
	if maxSteps <= 0 {
		return fmt.Errorf("-max-steps must be positive, got %d", maxSteps)
	}

	client := coasty.NewClient() // reads COASTY_API_KEY / COASTY_BASE_URL (.env fallback)
	version := coasty.CUAVersion(cuaVersion)

	perStep := coasty.RunStepCredits(version)
	exutil.PrintEstimate(os.Stdout, "task run (POST /v1/runs)", []exutil.EstimateLine{
		{
			Label:   fmt.Sprintf("up to %d run steps x %d credits (billed per completed step)", maxSteps, perStep),
			Credits: coasty.EstimateRunCredits(version, maxSteps),
		},
	}, client.IsSandbox())
	if err := exutil.ConfirmSpend(confirm, client.IsSandbox()); err != nil {
		return err
	}

	if idempotencyKey == "" {
		var err error
		if idempotencyKey, err = newIdempotencyKey(); err != nil {
			return err
		}
	}

	ctx := context.Background()
	created, err := client.CreateRun(ctx, &coasty.CreateRunRequest{
		IdempotencyKey:  idempotencyKey,
		MachineID:       machineID,
		Task:            task,
		CUAVersion:      version,
		MaxSteps:        maxSteps,
		OnAwaitingHuman: coasty.OnAwaitingHumanPause,
	})
	if err != nil {
		return fmt.Errorf("creating run: %w", err)
	}
	fmt.Printf("created run %s (status=%s, idempotency-key=%s)\n", created.ID, created.Status, idempotencyKey)

	onAwaiting := func(reason string) (string, bool) {
		fmt.Printf("run paused (awaiting_human): %s — resuming with note\n", reason)
		return resumeNote, true
	}

	if events {
		summary, err := StreamEvents(ctx, client, created.ID, StreamOptions{
			OnAwaitingHuman: onAwaiting,
			Log:             os.Stdout,
		})
		if err != nil {
			return err
		}
		fmt.Printf("stream finished: %d events, last seq %s, final status %q\n",
			summary.Events, summary.LastEventID, summary.FinalStatus)
		if summary.BillingCredits > 0 {
			fmt.Printf("billing (from stream): %d credits (%s)\n",
				summary.BillingCredits, coasty.FormatCreditsUSD(summary.BillingCredits))
		}
	} else {
		if _, err := PollUntilTerminal(ctx, client, created.ID, PollOptions{
			Interval:        pollInterval,
			OnAwaitingHuman: onAwaiting,
			Log:             os.Stdout,
		}); err != nil {
			return err
		}
	}

	final, err := client.GetRun(ctx, created.ID)
	if err != nil {
		return fmt.Errorf("fetching final run state: %w", err)
	}
	fmt.Printf("\nrun %s finished: status=%s steps=%d\n", final.ID, final.Status, final.StepsCompleted)
	fmt.Printf("billing: %d credits charged, cost %s\n",
		final.CreditsCharged, coasty.FormatCreditsUSD(final.CostCents))
	if final.Result != nil {
		fmt.Printf("result: passed=%t status=%q summary=%q\n",
			final.Result.Passed, final.Result.Status, final.Result.Summary)
	}
	if final.Error != nil {
		return fmt.Errorf("run ended with error %s: %s (request_id %s)",
			final.Error.Code, final.Error.Message, final.RequestID)
	}
	if final.Status != coasty.RunStatusSucceeded {
		return fmt.Errorf("run ended %s", final.Status)
	}
	return nil
}
