package coasty

import (
	"strings"
	"testing"
	"time"
)

// Shared HMAC test vectors from docs/API_NOTES.md — identical across all
// language tracks in this repo.
const (
	vector1Secret = "whsec_test_secret_123"
	vector1T      = int64(1750000000)
	vector1Body   = `{"event":"run.succeeded","run_id":"run_123","status":"succeeded"}`
	vector1V1     = "5f70978eab52dbf5838da76e5eb6c6c465560ce8e746ed8e6113c159d8bbb2d4"

	vector2Secret = "whsec_other_secret_456"
	vector2T      = int64(1750000300)
	vector2Body   = `{"event":"run.awaiting_human","run_id":"run_456","reason":"captcha"}`
	vector2V1     = "844504f42b7498094a83cedd7e050fc2f7fa32593b0814cc514c4be52a932e63"
)

const tolerance = DefaultWebhookTolerance // ±5 min

func header1() string { return "t=1750000000,v1=" + vector1V1 }

func TestVerifySignatureValidVectors(t *testing.T) {
	now1 := time.Unix(vector1T, 0)
	if !VerifySignature([]byte(vector1Body), header1(), vector1Secret, tolerance, now1) {
		t.Error("vector 1 must verify")
	}
	now2 := time.Unix(vector2T, 0)
	header2 := "t=1750000300,v1=" + vector2V1
	if !VerifySignature([]byte(vector2Body), header2, vector2Secret, tolerance, now2) {
		t.Error("vector 2 must verify")
	}
}

func TestVerifySignatureTamperedBody(t *testing.T) {
	now := time.Unix(vector1T, 0)
	tampered := []byte(strings.Replace(vector1Body, "run_123", "run_124", 1))
	if VerifySignature(tampered, header1(), vector1Secret, tolerance, now) {
		t.Error("tampered body must be rejected")
	}
	// Flipping a single byte must also reject.
	flipped := []byte(vector1Body)
	flipped[0] ^= 0x01
	if VerifySignature(flipped, header1(), vector1Secret, tolerance, now) {
		t.Error("byte-flipped body must be rejected")
	}
}

func TestVerifySignatureStaleTimestamp(t *testing.T) {
	body := []byte(vector1Body)
	// Pin "now" relative to the vector's t = 1750000000.
	tooLate := time.Unix(vector1T+301, 0) // 301s after signing: outside ±300s
	if VerifySignature(body, header1(), vector1Secret, tolerance, tooLate) {
		t.Error("timestamp older than the tolerance must be rejected")
	}
	tooEarly := time.Unix(vector1T-301, 0) // signature from the "future"
	if VerifySignature(body, header1(), vector1Secret, tolerance, tooEarly) {
		t.Error("timestamp newer than the tolerance must be rejected")
	}
	boundary := time.Unix(vector1T+300, 0) // exactly at the edge: accepted
	if !VerifySignature(body, header1(), vector1Secret, tolerance, boundary) {
		t.Error("timestamp exactly at the tolerance boundary must verify")
	}
}

func TestVerifySignatureMalformedHeaders(t *testing.T) {
	body := []byte(vector1Body)
	now := time.Unix(vector1T, 0)
	malformed := []string{
		"",
		"v1=" + vector1V1,       // missing t=
		"t=1750000000",          // missing v1=
		"t=abc,v1=" + vector1V1, // non-numeric t
		"t=1750000000,v1",       // v1 element without '='
		"garbage",               // no key=value at all
		"t=1750000000,v1=zzzz",  // v1 is not hex
		"t=1750000000,v1=",      // empty signature
		"t=1750000000,t=1750000000,v1=" + vector1V1, // duplicate t=
	}
	for _, h := range malformed {
		if VerifySignature(body, h, vector1Secret, tolerance, now) {
			t.Errorf("malformed header %q must be rejected", h)
		}
	}
}

func TestVerifySignatureWrongSecret(t *testing.T) {
	now := time.Unix(vector1T, 0)
	// Vector 1's body + timestamp but signed (per the header) with vector
	// 2's secret: must reject under vector 1's secret and vice versa.
	if VerifySignature([]byte(vector1Body), header1(), vector2Secret, tolerance, now) {
		t.Error("signature must not verify under a different secret")
	}
	if VerifySignature([]byte(vector1Body), header1(), "", tolerance, now) {
		t.Error("empty secret must be rejected")
	}
}

func TestVerifySignatureUppercaseHexAccepted(t *testing.T) {
	now := time.Unix(vector1T, 0)
	upper := "t=1750000000,v1=" + strings.ToUpper(vector1V1)
	if !VerifySignature([]byte(vector1Body), upper, vector1Secret, tolerance, now) {
		t.Error("uppercase hex signatures must verify")
	}
}

func TestVerifySignatureMultipleV1Candidates(t *testing.T) {
	now := time.Unix(vector1T, 0)
	// Secret-rotation style header: one stale candidate plus the valid one.
	h := "t=1750000000,v1=" + vector2V1 + ",v1=" + vector1V1
	if !VerifySignature([]byte(vector1Body), h, vector1Secret, tolerance, now) {
		t.Error("any matching v1 candidate must verify")
	}
}

func TestSignWebhookPayloadRoundTrip(t *testing.T) {
	signedAt := time.Unix(vector1T, 0)
	header := SignWebhookPayload([]byte(vector1Body), vector1Secret, signedAt)
	if header != header1() {
		t.Errorf("SignWebhookPayload = %q, want %q (must reproduce vector 1)", header, header1())
	}
	if !VerifySignature([]byte(vector1Body), header, vector1Secret, tolerance, signedAt) {
		t.Error("self-signed payload must verify")
	}
}
