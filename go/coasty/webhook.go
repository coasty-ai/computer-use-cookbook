package coasty

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"strconv"
	"strings"
	"time"
)

// DefaultWebhookTolerance is the documented replay window for webhook
// timestamps (±5 minutes).
const DefaultWebhookTolerance = 5 * time.Minute

// VerifySignature verifies a Coasty webhook signature header:
//
//	Coasty-Signature: t=<unix_ts>,v1=<hex>
//
// The signed payload is "<t>." + rawBody and the expected signature is
// hex(HMAC-SHA256(secret, payload)). Comparison is constant-time
// (hmac.Equal) and the timestamp must be within ±tolerance of now.
// Malformed headers, bad timestamps, wrong secrets and tampered bodies all
// return false. Pass time.Now() for now (tests pin it for determinism).
func VerifySignature(rawBody []byte, header, secret string, tolerance time.Duration, now time.Time) bool {
	if secret == "" || header == "" {
		return false
	}

	var tsRaw string
	var candidates []string // all v1= values; any match accepts (key rotation)
	for _, part := range strings.Split(header, ",") {
		key, value, found := strings.Cut(strings.TrimSpace(part), "=")
		if !found {
			return false // malformed element
		}
		switch key {
		case "t":
			if tsRaw != "" {
				return false // duplicate t=
			}
			tsRaw = value
		case "v1":
			candidates = append(candidates, value)
		default:
			// Unknown schemes (e.g. a future v2=) are ignored.
		}
	}
	if tsRaw == "" || len(candidates) == 0 {
		return false
	}

	ts, err := strconv.ParseInt(tsRaw, 10, 64)
	if err != nil {
		return false
	}
	if tolerance < 0 {
		return false
	}
	skew := now.Sub(time.Unix(ts, 0))
	if skew < 0 {
		skew = -skew
	}
	if skew > tolerance {
		return false
	}

	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(tsRaw))
	mac.Write([]byte("."))
	mac.Write(rawBody)
	expected := mac.Sum(nil)

	for _, candidate := range candidates {
		got, err := hex.DecodeString(strings.ToLower(strings.TrimSpace(candidate)))
		if err != nil {
			continue
		}
		if hmac.Equal(expected, got) {
			return true
		}
	}
	return false
}

// SignWebhookPayload computes the signature header value for a payload —
// useful for tests and for emulating Coasty's webhook sender:
//
//	t=<ts>,v1=<hex(HMAC-SHA256(secret, "<ts>." + rawBody))>
func SignWebhookPayload(rawBody []byte, secret string, t time.Time) string {
	ts := strconv.FormatInt(t.Unix(), 10)
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(ts))
	mac.Write([]byte("."))
	mac.Write(rawBody)
	return "t=" + ts + ",v1=" + hex.EncodeToString(mac.Sum(nil))
}
