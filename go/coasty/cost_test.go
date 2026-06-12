package coasty

import "testing"

func TestIsHDStrictBoundary(t *testing.T) {
	tests := []struct {
		w, h int
		want bool
	}{
		{1280, 720, false}, // exactly 1280x720 is NOT HD
		{1281, 720, true},  // width strictly over
		{1280, 721, true},  // height strictly over
		{1920, 1080, true},
		{640, 480, false},
		{1, 721, true},
		{1281, 1, true},
		{0, 0, false},
	}
	for _, tt := range tests {
		if got := IsHD(tt.w, tt.h); got != tt.want {
			t.Errorf("IsHD(%d, %d) = %v, want %v", tt.w, tt.h, got, tt.want)
		}
		if got := (ImageSize{tt.w, tt.h}).IsHD(); got != tt.want {
			t.Errorf("ImageSize{%d,%d}.IsHD() = %v, want %v", tt.w, tt.h, got, tt.want)
		}
	}
}

func TestEstimatePredictCredits(t *testing.T) {
	sd := ImageSize{1280, 720}
	hd := ImageSize{1920, 1080}

	tests := []struct {
		name string
		in   InferenceCostInput
		want int
	}{
		{"base SD v3", InferenceCostInput{CUAVersion: CUAVersionV3, Screenshot: sd}, 5},
		{"default version counts as v3", InferenceCostInput{Screenshot: sd}, 5},
		{"v4 has no engine surcharge", InferenceCostInput{CUAVersion: CUAVersionV4, Screenshot: sd}, 5},
		{"HD current screenshot", InferenceCostInput{Screenshot: hd}, 6},
		{"v1 engine surcharge", InferenceCostInput{CUAVersion: CUAVersionV1, Screenshot: sd}, 8},
		{
			"two SD trajectory shots",
			InferenceCostInput{Screenshot: sd, Trajectory: []ImageSize{sd, sd}},
			5 + 2*2,
		},
		{
			"HD fee applies to current AND each HD trajectory shot",
			InferenceCostInput{Screenshot: hd, Trajectory: []ImageSize{hd, sd}},
			5 + 2*2 + 1 + 1, // base + 2 traj + HD current + 1 HD traj
		},
		{
			"system_prompt exactly 500 chars is free",
			InferenceCostInput{Screenshot: sd, SystemPromptChars: 500},
			5,
		},
		{
			"system_prompt 501 chars bills +1",
			InferenceCostInput{Screenshot: sd, SystemPromptChars: 501},
			6,
		},
		{
			"kitchen sink: v1 + HD + 2 HD trajectory + long prompt",
			InferenceCostInput{
				CUAVersion:        CUAVersionV1,
				Screenshot:        hd,
				Trajectory:        []ImageSize{hd, hd},
				SystemPromptChars: 1000,
			},
			5 + 2*2 + 1 + 2 + 3 + 1, // = 16
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := EstimatePredictCredits(tt.in); got != tt.want {
				t.Errorf("EstimatePredictCredits = %d, want %d", got, tt.want)
			}
		})
	}
}

func TestEstimateSessionCredits(t *testing.T) {
	if got := EstimateSessionCreateCredits(); got != 10 {
		t.Errorf("session create = %d, want 10 (flat, no surcharges)", got)
	}
	sd := ImageSize{1280, 720}
	hd := ImageSize{1921, 1080}
	if got := EstimateSessionPredictCredits(InferenceCostInput{Screenshot: sd}); got != 4 {
		t.Errorf("session predict base = %d, want 4", got)
	}
	in := InferenceCostInput{
		CUAVersion: CUAVersionV1, Screenshot: hd,
		Trajectory: []ImageSize{hd}, SystemPromptChars: 501,
	}
	// 4 base + 2 traj + 1 HD current + 1 HD traj + 3 v1 + 1 prompt = 12
	if got := EstimateSessionPredictCredits(in); got != 12 {
		t.Errorf("session predict = %d, want 12", got)
	}
}

func TestEstimateGroundAndParseCredits(t *testing.T) {
	if got := EstimateGroundCredits(ImageSize{1280, 720}); got != 3 {
		t.Errorf("ground SD = %d, want 3", got)
	}
	if got := EstimateGroundCredits(ImageSize{1280, 721}); got != 4 {
		t.Errorf("ground HD = %d, want 4 (+1 HD)", got)
	}
	if got := EstimateParseCredits(); got != 0 {
		t.Errorf("parse = %d, want 0 (free)", got)
	}
}

func TestRunStepCredits(t *testing.T) {
	tests := []struct {
		v    CUAVersion
		want int
	}{
		{CUAVersionV1, 8}, // 5 base + 3 v1 engine surcharge
		{CUAVersionV3, 5},
		{CUAVersionV4, 5},
		{"", 5}, // default engine is v3
	}
	for _, tt := range tests {
		if got := RunStepCredits(tt.v); got != tt.want {
			t.Errorf("RunStepCredits(%q) = %d, want %d", tt.v, got, tt.want)
		}
	}
	if got := EstimateRunCredits(CUAVersionV1, 3); got != 24 {
		t.Errorf("EstimateRunCredits(v1, 3) = %d, want 24", got)
	}
	if got := EstimateRunCredits(CUAVersionV3, 40); got != 200 {
		t.Errorf("EstimateRunCredits(v3, 40) = %d, want 200", got)
	}
	if got := EstimateRunCredits(CUAVersionV3, -1); got != 0 {
		t.Errorf("EstimateRunCredits(v3, -1) = %d, want 0", got)
	}
}

func TestMachineHourlyCredits(t *testing.T) {
	tests := []struct {
		os    OSType
		state MachineState
		want  int
	}{
		{OSLinux, MachineStateRunning, 5},
		{OSLinux, MachineStateStarting, 5},
		{OSLinux, MachineStateStopping, 5},
		{OSLinux, MachineStateRestarting, 5},
		{OSWindows, MachineStateRunning, 9},
		{OSWindows, MachineStateStarting, 9},
		{OSLinux, MachineStateStopped, 1},
		{OSWindows, MachineStateStopped, 1}, // storage rate is OS-independent
		{OSWindows, MachineStateSuspended, 1},
		{OSLinux, MachineStateSuspendedForBilling, 1},
		{OSLinux, MachineStateCreating, 0},
		{OSWindows, MachineStateError, 0},
		{OSLinux, MachineStateTerminated, 0},
		{OSLinux, MachineState("unknown"), 0},
	}
	for _, tt := range tests {
		if got := MachineHourlyCredits(tt.os, tt.state); got != tt.want {
			t.Errorf("MachineHourlyCredits(%s, %s) = %d, want %d", tt.os, tt.state, got, tt.want)
		}
	}
	if got := SnapshotCredits(); got != 1 {
		t.Errorf("SnapshotCredits = %d, want 1", got)
	}
}

func TestCreditConversions(t *testing.T) {
	if got := CreditsToCents(7); got != 7 {
		t.Errorf("CreditsToCents(7) = %d (1 credit = 1 cent exactly)", got)
	}
	if got := CreditsToUSD(540); got != 5.40 {
		t.Errorf("CreditsToUSD(540) = %v, want 5.40", got)
	}
	tests := []struct {
		credits int
		want    string
	}{
		{0, "$0.00"},
		{6, "$0.06"},
		{99, "$0.99"},
		{100, "$1.00"},
		{540, "$5.40"},
		{-5, "-$0.05"},
	}
	for _, tt := range tests {
		if got := FormatCreditsUSD(tt.credits); got != tt.want {
			t.Errorf("FormatCreditsUSD(%d) = %q, want %q", tt.credits, got, tt.want)
		}
	}
}
