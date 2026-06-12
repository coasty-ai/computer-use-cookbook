package coasty

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
)

// DefaultBaseURL is the production API root.
const DefaultBaseURL = "https://coasty.ai/v1"

const sandboxKeyPrefix = "sk-coasty-test-"

var (
	dotenvOnce sync.Once
	dotenvVals map[string]string
)

// APIKey returns the Coasty API key: the COASTY_API_KEY environment
// variable, falling back to the repo-root .env file. The value is never
// logged.
func APIKey() string { return lookupEnv("COASTY_API_KEY") }

// BaseURL returns the API base URL: the COASTY_BASE_URL environment
// variable (or .env entry) when set and non-empty, otherwise
// https://coasty.ai/v1.
func BaseURL() string {
	if v := lookupEnv("COASTY_BASE_URL"); v != "" {
		return v
	}
	return DefaultBaseURL
}

// IsSandboxKey reports whether the configured API key is a sandbox key
// (sk-coasty-test-*). Sandbox keys never bill.
func IsSandboxKey() bool { return strings.HasPrefix(APIKey(), sandboxKeyPrefix) }

// Env reads a configuration value by name: the process environment wins
// (even when set to an empty string), falling back to the repo-root .env
// file. Used by the examples for settings like COASTY_CONFIRM_SPEND and
// COASTY_WEBHOOK_SECRET. Values are never logged by this package.
func Env(key string) string { return lookupEnv(key) }

// lookupEnv reads the process environment first (it always wins, even when
// set to an empty string), then the lazily-loaded .env file.
func lookupEnv(key string) string {
	if v, ok := os.LookupEnv(key); ok {
		return v
	}
	return dotenv()[key]
}

// dotenv loads the repo-root .env exactly once. Missing or unreadable files
// yield an empty map: the .env is optional by design.
func dotenv() map[string]string {
	dotenvOnce.Do(func() {
		dotenvVals = map[string]string{}
		path := findDotEnv()
		if path == "" {
			return
		}
		vals, err := ParseEnvFile(path)
		if err != nil {
			return // optional file; values must never be logged
		}
		dotenvVals = vals
	})
	return dotenvVals
}

// findDotEnv walks up from the working directory looking for a .env file,
// so examples run from go/examples/<name> still find the repo root .env.
func findDotEnv() string {
	dir, err := os.Getwd()
	if err != nil {
		return ""
	}
	for i := 0; i < 8; i++ {
		candidate := filepath.Join(dir, ".env")
		if info, err := os.Stat(candidate); err == nil && info.Mode().IsRegular() {
			return candidate
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return ""
		}
		dir = parent
	}
	return ""
}

// ParseEnvFile reads a minimal .env file: one KEY=VALUE per line, blank
// lines and lines starting with "#" ignored, optional matching single or
// double quotes stripped from the value. There is no "export" handling, no
// variable expansion, and values are never logged.
func ParseEnvFile(path string) (map[string]string, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("coasty: opening env file: %w", err)
	}
	defer f.Close()

	vals := map[string]string{}
	scanner := bufio.NewScanner(f)
	scanner.Buffer(make([]byte, 0, 4096), 1<<20)
	for scanner.Scan() {
		line := strings.TrimSpace(strings.TrimSuffix(scanner.Text(), "\r"))
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		key, value, found := strings.Cut(line, "=")
		key = strings.TrimSpace(key)
		if !found || key == "" {
			continue // not a KEY=VALUE line; skip rather than fail the load
		}
		value = strings.TrimSpace(value)
		if len(value) >= 2 {
			if (value[0] == '"' && value[len(value)-1] == '"') ||
				(value[0] == '\'' && value[len(value)-1] == '\'') {
				value = value[1 : len(value)-1]
			}
		}
		vals[key] = value
	}
	if err := scanner.Err(); err != nil {
		return nil, fmt.Errorf("coasty: reading env file: %w", err)
	}
	return vals, nil
}
