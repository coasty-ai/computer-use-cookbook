#!/usr/bin/env bash
# =============================================================================
# Offline smoke test for curl/quickstart.sh
#
# Starts the local stub server (tests/stub_server.py) on an ephemeral port,
# runs quickstart.sh against it with an obviously-fake sandbox key, and
# asserts on the output. Fully offline and deterministic; the only sleeps are
# 50 ms server-readiness polls (the run-poll loop runs with
# COASTY_POLL_INTERVAL=0).
#
# Usage:  bash tests/smoke.sh     (or: cd tests && bash smoke.sh)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QUICKSTART="${SCRIPT_DIR}/../quickstart.sh"

# Obviously-fake sandbox key for the stub (sk-coasty-test- + 48 ones). It must
# differ from the quickstart's deliberate-401 key (48 zeros) so the 401 demo
# actually fails auth against the stub.
TEST_KEY="sk-coasty-test-111111111111111111111111111111111111111111111111"

# Pick a working python (on Windows, `python3` can be a dead Store stub).
PY=""
if command -v python3 >/dev/null 2>&1 && python3 -c 'pass' >/dev/null 2>&1; then
  PY="python3"
elif command -v python >/dev/null 2>&1 && python -c 'pass' >/dev/null 2>&1; then
  PY="python"
else
  echo "SKIP-FAIL: need python3/python to run the stub server" >&2
  exit 1
fi

# Git Bash on Windows: convert msys paths to mixed C:/ form so the native
# Windows python/curl can use them. No-op elsewhere.
native_path() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s\n' "$1"; fi
}

TMP_DIR="$(mktemp -d)"
TMP_DIR="$(native_path "$TMP_DIR")"
PORT_FILE="${TMP_DIR}/port"
OUT_LOG="${TMP_DIR}/quickstart.log"
STUB_PID=""

cleanup() {
  local exit_code=$?
  trap - EXIT
  if [[ -n "$STUB_PID" ]]; then
    kill "$STUB_PID" >/dev/null 2>&1 || true
    wait "$STUB_PID" 2>/dev/null || true
  fi
  rm -rf -- "$TMP_DIR" || true
  exit "$exit_code"
}
trap cleanup EXIT

fail() {
  echo "SMOKE FAIL: $*" >&2
  echo "---- quickstart output (tail) ----" >&2
  tail -n 60 "$OUT_LOG" >&2 2>/dev/null || true
  exit 1
}

# --- start the stub and wait (max ~10 s, 50 ms polls) for its port file -----
COASTY_STUB_KEY="$TEST_KEY" "$PY" "$(native_path "${SCRIPT_DIR}/stub_server.py")" "$PORT_FILE" &
STUB_PID=$!

tries=0
while [[ ! -s "$PORT_FILE" ]]; do
  tries=$((tries + 1))
  if (( tries > 200 )); then
    fail "stub server did not start (no port file after 10s)"
  fi
  if ! kill -0 "$STUB_PID" 2>/dev/null; then
    fail "stub server process exited prematurely"
  fi
  sleep 0.05
done
PORT="$(tr -d '[:space:]' < "$PORT_FILE")"
echo "stub server up on 127.0.0.1:${PORT} (pid ${STUB_PID})"

# --- run the quickstart against the stub -------------------------------------
# NOTE: `cmd || EC=$?` (not `if ! cmd`) so we capture the command's own exit
# code; `$?` after a `!`-negated pipeline is the negation's status, not cmd's.
EC=0
COASTY_API_KEY="$TEST_KEY" \
  COASTY_BASE_URL="http://127.0.0.1:${PORT}/v1" \
  COASTY_POLL_INTERVAL=0 \
  bash "$QUICKSTART" >"$OUT_LOG" 2>&1 || EC=$?

# --- assertions ---------------------------------------------------------------
[[ "$EC" -eq 0 ]] || fail "quickstart.sh exited with code ${EC}"

assert_contains() {
  grep -qF -- "$1" "$OUT_LOG" || fail "output is missing: $1"
}

assert_contains "Section 0: preflight"
assert_contains "Key kind:       sandbox"
assert_contains "Section 1: GET /models (free)"
assert_contains "action_types"
assert_contains "Section 2: POST /parse (free)"
assert_contains "First action: click"
assert_contains "Section 3: POST /predict"
assert_contains "first action:    click"
assert_contains "Section 4: POST /ground"
assert_contains "Element located at (x=160, y=120)"
assert_contains "Section 5: sessions"
assert_contains "Created session: sess_stub_0001"
assert_contains "Session deleted: sess_stub_0001"
assert_contains "Section 6: runs"
assert_contains "Provisioned machine: mch_test_stub01"
assert_contains "Run finished: status=succeeded"
assert_contains "Ran date successfully."
assert_contains "Machine terminated: mch_test_stub01"
assert_contains "Section 7: response headers and the error envelope"
assert_contains "X-Coasty-Request-Id:"
assert_contains "Got the expected HTTP 401"
assert_contains "INVALID_API_KEY"
assert_contains "All sections completed."

# The real key must NEVER appear in the output (secret hygiene).
if grep -qF -- "$TEST_KEY" "$OUT_LOG"; then
  fail "the API key leaked into quickstart output"
fi

# The live-key consent gate must abort without CONFIRM (and bill nothing).
GATE_LOG="${TMP_DIR}/gate.log"
GATE_EC=0
COASTY_API_KEY="sk-coasty-live-000000000000000000000000000000000000000000000000" \
  COASTY_BASE_URL="http://127.0.0.1:${PORT}/v1" \
  bash "$QUICKSTART" >"$GATE_LOG" 2>&1 || GATE_EC=$?
[[ "$GATE_EC" -eq 3 ]] || { cat "$GATE_LOG" >&2; fail "live key without CONFIRM should exit 3, got ${GATE_EC}"; }
grep -qF -- "ABORTING: refusing to spend with a live key" "$GATE_LOG" \
  || { cat "$GATE_LOG" >&2; fail "live-key abort message missing"; }

echo "SMOKE PASS: all assertions held (log: quickstart ran clean against the stub)"
