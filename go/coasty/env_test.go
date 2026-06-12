package coasty

import (
	"os"
	"path/filepath"
	"sync"
	"testing"
)

// resetDotenv clears the lazily-loaded .env cache so each test observes a
// fresh load, and restores the clean state afterwards.
func resetDotenv(t *testing.T) {
	t.Helper()
	reset := func() {
		dotenvOnce = sync.Once{}
		dotenvVals = nil
	}
	reset()
	t.Cleanup(reset)
}

// clearEnv removes key for the duration of the test (t.Setenv registers the
// restore; the immediate Unsetenv makes the variable truly absent).
func clearEnv(t *testing.T, key string) {
	t.Helper()
	t.Setenv(key, "")
	if err := os.Unsetenv(key); err != nil {
		t.Fatalf("unsetting %s: %v", key, err)
	}
}

func writeFile(t *testing.T, path, content string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatalf("writing %s: %v", path, err)
	}
}

// chdir switches the working directory for the test and restores it after
// (the module targets go1.22, which predates testing.T.Chdir).
func chdir(t *testing.T, dir string) {
	t.Helper()
	old, err := os.Getwd()
	if err != nil {
		t.Fatalf("getwd: %v", err)
	}
	if err := os.Chdir(dir); err != nil {
		t.Fatalf("chdir %s: %v", dir, err)
	}
	t.Cleanup(func() {
		if err := os.Chdir(old); err != nil {
			t.Errorf("restoring working directory: %v", err)
		}
	})
}

func TestParseEnvFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, ".env")
	writeFile(t, path, ""+
		"# comment line\n"+
		"\n"+
		"COASTY_API_KEY="+testAPIKey+"\n"+
		"COASTY_BASE_URL=http://127.0.0.1:8787/v1\r\n"+ // CRLF tolerated
		"DOUBLE_QUOTED=\"  spaced value  \"\n"+
		"SINGLE_QUOTED='single'\n"+
		"MISMATCHED=\"keep'\n"+
		"  PADDED_KEY  =  padded value  \n"+
		"INNER_EQUALS=a=b=c\n"+
		"EMPTY=\n"+
		"not-a-kv-line\n"+
		"=no-key\n"+
		"# trailing comment\n")

	vals, err := ParseEnvFile(path)
	if err != nil {
		t.Fatalf("ParseEnvFile: %v", err)
	}
	want := map[string]string{
		"COASTY_API_KEY":  testAPIKey,
		"COASTY_BASE_URL": "http://127.0.0.1:8787/v1",
		"DOUBLE_QUOTED":   "  spaced value  ",
		"SINGLE_QUOTED":   "single",
		"MISMATCHED":      "\"keep'",
		"PADDED_KEY":      "padded value",
		"INNER_EQUALS":    "a=b=c",
		"EMPTY":           "",
	}
	if len(vals) != len(want) {
		t.Errorf("parsed %d entries %v, want %d", len(vals), keysOf(vals), len(want))
	}
	for k, w := range want {
		got, ok := vals[k]
		if !ok {
			t.Errorf("missing key %q", k)
			continue
		}
		if got != w {
			t.Errorf("vals[%q] = %q, want %q", k, got, w)
		}
	}
}

// keysOf returns just the key names: .env VALUES must never reach logs or
// test failure messages.
func keysOf(m map[string]string) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	return keys
}

func TestParseEnvFileMissing(t *testing.T) {
	if _, err := ParseEnvFile(filepath.Join(t.TempDir(), "no-such.env")); err == nil {
		t.Error("ParseEnvFile on a missing file must return an error")
	}
}

func TestAPIKeyProcessEnvWins(t *testing.T) {
	resetDotenv(t)
	dir := t.TempDir()
	writeFile(t, filepath.Join(dir, ".env"), "COASTY_API_KEY=sk-coasty-test-"+zeros48+"\n")
	chdir(t, dir)

	t.Setenv("COASTY_API_KEY", "sk-coasty-test-fromprocessenv")
	if got := APIKey(); got != "sk-coasty-test-fromprocessenv" {
		t.Error("process env must win over .env")
	}

	// An env var explicitly set to "" still wins over the .env entry.
	t.Setenv("COASTY_API_KEY", "")
	if got := APIKey(); got != "" {
		t.Error("empty process env value must win over .env")
	}
}

const zeros48 = "000000000000000000000000000000000000000000000000"

func TestAPIKeyFallsBackToDotEnv(t *testing.T) {
	resetDotenv(t)
	clearEnv(t, "COASTY_API_KEY")
	clearEnv(t, "COASTY_BASE_URL")

	root := t.TempDir()
	writeFile(t, filepath.Join(root, ".env"),
		"COASTY_API_KEY="+testAPIKey+"\nCOASTY_BASE_URL=http://127.0.0.1:8787/v1\n")
	// Run from a nested directory: the loader must walk up to find .env,
	// like examples run from go/examples/<name> finding the repo root file.
	nested := filepath.Join(root, "go", "examples")
	if err := os.MkdirAll(nested, 0o750); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	chdir(t, nested)

	if got := APIKey(); got != testAPIKey {
		t.Error("APIKey must fall back to the .env file")
	}
	if got := BaseURL(); got != "http://127.0.0.1:8787/v1" {
		t.Errorf("BaseURL = %q, want the .env value", got)
	}
	if !IsSandboxKey() {
		t.Error("IsSandboxKey must detect the sandbox key from .env")
	}
}

func TestBaseURLDefault(t *testing.T) {
	resetDotenv(t)
	clearEnv(t, "COASTY_BASE_URL")
	// A .env exists but has no COASTY_BASE_URL: the walk stops here, finds
	// nothing, and the default applies.
	dir := t.TempDir()
	writeFile(t, filepath.Join(dir, ".env"), "# no base url here\nOTHER_KEY=other\n")
	chdir(t, dir)

	if got := BaseURL(); got != DefaultBaseURL {
		t.Errorf("BaseURL = %q, want %q", got, DefaultBaseURL)
	}
	// Set but empty also falls back to the default.
	t.Setenv("COASTY_BASE_URL", "")
	if got := BaseURL(); got != DefaultBaseURL {
		t.Errorf("BaseURL with empty env = %q, want %q", got, DefaultBaseURL)
	}
}

func TestIsSandboxKey(t *testing.T) {
	resetDotenv(t)
	chdir(t, t.TempDir())

	tests := []struct {
		key  string
		want bool
	}{
		{"sk-coasty-test-" + zeros48, true},
		{"sk-coasty-live-" + zeros48, false},
		{"cua_sk_" + zeros48, false},
		{"", false},
	}
	for _, tt := range tests {
		t.Setenv("COASTY_API_KEY", tt.key)
		if got := IsSandboxKey(); got != tt.want {
			t.Errorf("IsSandboxKey() for key kind %q... = %v, want %v",
				tt.key[:min(len(tt.key), 15)], got, tt.want)
		}
	}
}

func TestMissingDotEnvIsNotAnError(t *testing.T) {
	resetDotenv(t)
	clearEnv(t, "COASTY_API_KEY")
	// Nest deeper than the 8-level walk limit so no .env (not even one in a
	// temp ancestor) is reachable.
	deep := t.TempDir()
	for i := 0; i < 9; i++ {
		deep = filepath.Join(deep, "d")
	}
	if err := os.MkdirAll(deep, 0o750); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	chdir(t, deep)

	if got := APIKey(); got != "" {
		t.Error("APIKey with no env and no .env must be empty")
	}
	if IsSandboxKey() {
		t.Error("no key configured must not be a sandbox key")
	}
}
