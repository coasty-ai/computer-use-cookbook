package coasty

import "fmt"

// Pricing table (docs/API_NOTES.md §Pricing; 1 credit = 1 cent = $0.01
// exactly). Charges are debited up front and auto-refunded on failure;
// sandbox keys (sk-coasty-test-*) never bill.
const (
	// Base per-request costs.
	CreditsPredictBase        = 5  // POST /predict
	CreditsSessionCreate      = 10 // POST /sessions (one-time, NO surcharges)
	CreditsSessionPredictBase = 4  // POST /sessions/{id}/predict
	CreditsGroundBase         = 3  // POST /ground (+1 if HD)
	CreditsParse              = 0  // POST /parse is free

	// Surcharges (predict / session-predict unless noted).
	CreditsPerTrajectoryScreenshot = 2 // each trajectory screenshot attached
	CreditsPerHDImage              = 1 // current + each trajectory shot, when HD
	CreditsV1EngineSurcharge       = 3 // cua_version == "v1", per request
	CreditsLongSystemPrompt        = 1 // system_prompt > 500 chars (exactly 500 = free)

	// Run / workflow task steps (no trajectory/HD/prompt surcharges apply).
	CreditsRunStepV3V4 = 5
	CreditsRunStepV1   = 8 // 5 base + 3 v1 engine surcharge

	// Machines (hourly rates, metered per minute, rounded down).
	CreditsMachineRunningLinuxPerHour   = 5 // incl. starting/stopping/restarting
	CreditsMachineRunningWindowsPerHour = 9
	CreditsMachineStoppedPerHour        = 1 // stopped/suspended, any OS
	CreditsSnapshot                     = 1 // one-time, refunded on failure

	// SystemPromptFreeChars is the longest system_prompt with no surcharge.
	SystemPromptFreeChars = 500

	// HD boundary: an image is HD when width > 1280 OR height > 720,
	// strictly — exactly 1280x720 is NOT HD.
	hdMaxWidth  = 1280
	hdMaxHeight = 720
)

// IsHD reports whether an image bills the HD surcharge (width > 1280 or
// height > 720, strictly — exactly 1280x720 is not HD).
func IsHD(width, height int) bool {
	return width > hdMaxWidth || height > hdMaxHeight
}

// ImageSize is a screenshot's pixel dimensions, for cost estimation.
type ImageSize struct {
	Width  int
	Height int
}

// IsHD reports whether this image bills the HD surcharge.
func (s ImageSize) IsHD() bool { return IsHD(s.Width, s.Height) }

// InferenceCostInput describes one predict / session-predict call for cost
// estimation.
type InferenceCostInput struct {
	CUAVersion CUAVersion // "" counts as the default v3
	Screenshot ImageSize  // the current screenshot
	// Trajectory holds the dimensions of each trajectory screenshot (the
	// ones you attach on /predict, or the server-kept ones on session
	// predict).
	Trajectory        []ImageSize
	SystemPromptChars int // length of system_prompt ("" = 0)
}

func inferenceCredits(base int, in InferenceCostInput) int {
	credits := base
	credits += CreditsPerTrajectoryScreenshot * len(in.Trajectory)
	if in.Screenshot.IsHD() {
		credits += CreditsPerHDImage
	}
	for _, shot := range in.Trajectory {
		if shot.IsHD() {
			credits += CreditsPerHDImage
		}
	}
	if in.CUAVersion == CUAVersionV1 {
		credits += CreditsV1EngineSurcharge
	}
	if in.SystemPromptChars > SystemPromptFreeChars {
		credits += CreditsLongSystemPrompt
	}
	return credits
}

// EstimatePredictCredits estimates POST /v1/predict: 5 base, +2 per
// trajectory screenshot, +1 per HD image (current + trajectory), +3 on v1,
// +1 when system_prompt exceeds 500 chars.
func EstimatePredictCredits(in InferenceCostInput) int {
	return inferenceCredits(CreditsPredictBase, in)
}

// EstimateSessionCreateCredits estimates POST /v1/sessions: a flat 10
// credits, with no surcharges.
func EstimateSessionCreateCredits() int { return CreditsSessionCreate }

// EstimateSessionPredictCredits estimates POST /v1/sessions/{id}/predict: 4
// base plus the same surcharges as /predict (the trajectory is server-kept;
// pass the sizes of the shots currently in the window).
func EstimateSessionPredictCredits(in InferenceCostInput) int {
	return inferenceCredits(CreditsSessionPredictBase, in)
}

// EstimateGroundCredits estimates POST /v1/ground: 3 credits, +1 if the
// screenshot is HD.
func EstimateGroundCredits(screenshot ImageSize) int {
	credits := CreditsGroundBase
	if screenshot.IsHD() {
		credits += CreditsPerHDImage
	}
	return credits
}

// EstimateParseCredits estimates POST /v1/parse: always free.
func EstimateParseCredits() int { return CreditsParse }

// RunStepCredits returns the per-completed-step cost of a run (or workflow
// task step): 8 on v1, 5 on v3/v4 ("" = default v3). Run steps carry no
// trajectory/HD/prompt surcharges.
func RunStepCredits(v CUAVersion) int {
	if v == CUAVersionV1 {
		return CreditsRunStepV1
	}
	return CreditsRunStepV3V4
}

// EstimateRunCredits estimates a run of n completed steps. Steps are billed
// one at a time as they complete; bookkeeping steps after a resume are free.
func EstimateRunCredits(v CUAVersion, steps int) int {
	if steps < 0 {
		steps = 0
	}
	return RunStepCredits(v) * steps
}

// SnapshotCredits returns the one-time machine snapshot cost (refunded on
// failure).
func SnapshotCredits() int { return CreditsSnapshot }

// OSType is a machine operating system, for the hourly rate table.
type OSType string

const (
	OSLinux   OSType = "linux"
	OSWindows OSType = "windows"
)

// MachineState buckets the machine lifecycle states that share an hourly
// rate.
type MachineState string

const (
	MachineStateCreating            MachineState = "creating"
	MachineStateRunning             MachineState = "running"
	MachineStateStarting            MachineState = "starting"
	MachineStateStopping            MachineState = "stopping"
	MachineStateRestarting          MachineState = "restarting"
	MachineStateStopped             MachineState = "stopped"
	MachineStateSuspended           MachineState = "suspended"
	MachineStateSuspendedForBilling MachineState = "suspended_for_billing"
	MachineStateError               MachineState = "error"
	MachineStateTerminated          MachineState = "terminated"
)

// MachineHourlyCredits returns the hourly runtime rate for a machine:
// running (incl. starting/stopping/restarting) bills 5/hr on Linux and 9/hr
// on Windows; stopped/suspended bills 1/hr on any OS; creating, error,
// terminated and unknown states are free. Runtime is metered per minute and
// rounded down in whole credits.
func MachineHourlyCredits(os OSType, state MachineState) int {
	switch state {
	case MachineStateRunning, MachineStateStarting, MachineStateStopping, MachineStateRestarting:
		if os == OSWindows {
			return CreditsMachineRunningWindowsPerHour
		}
		return CreditsMachineRunningLinuxPerHour
	case MachineStateStopped, MachineStateSuspended, MachineStateSuspendedForBilling:
		return CreditsMachineStoppedPerHour
	default: // creating / error / terminated — never billed
		return 0
	}
}

// CreditsToCents converts credits to USD cents (1 credit = 1 cent, exactly).
func CreditsToCents(credits int) int { return credits }

// CreditsToUSD converts credits to US dollars.
func CreditsToUSD(credits int) float64 { return float64(credits) / 100 }

// FormatCreditsUSD renders credits as a dollar string, e.g. 6 -> "$0.06".
func FormatCreditsUSD(credits int) string {
	sign := ""
	if credits < 0 {
		sign = "-"
		credits = -credits
	}
	return fmt.Sprintf("%s$%d.%02d", sign, credits/100, credits%100)
}
