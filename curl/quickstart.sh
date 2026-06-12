#!/usr/bin/env bash
# =============================================================================
# Coasty Computer Use API — pure curl/bash quickstart
# =============================================================================
#
# A guided, runnable walkthrough of the Coasty API using nothing but curl,
# bash, and (jq OR python) for JSON extraction. Eight sections:
#
#   0. Preflight   — key check, sandbox/live detection, itemized cost, consent
#   1. GET  /models           (free)
#   2. POST /parse            (free)     pyautogui code -> structured actions
#   3. POST /predict          (5 cr)     screenshot + instruction -> actions
#   4. POST /ground           (3 cr)     element description -> (x, y)
#   5. Sessions               (14 cr)    create -> predict -> DELETE (trap'd)
#   6. Task runs              (<=10 cr)  create w/ Idempotency-Key -> poll
#                                        (+ a commented SSE streaming block)
#   7. Response headers + error envelope (free; deliberate 401 with a FAKE key)
#
# Environment:
#   COASTY_API_KEY        (required)  your API key; NEVER printed by this script
#   COASTY_BASE_URL       (optional)  default https://coasty.ai/v1
#                                     point at the local mock server with
#                                     COASTY_BASE_URL=http://127.0.0.1:8787/v1
#   COASTY_MACHINE_ID     (optional)  reuse an existing machine in section 6
#                                     instead of provisioning a temporary one
#   COASTY_POLL_INTERVAL  (optional)  seconds between run polls (default 2)
#   CONFIRM=1 or --confirm            required before spending with a LIVE key
#
# Spend safety:
#   - sk-coasty-test-* (sandbox) keys never bill; the script proceeds freely.
#   - Any other key is treated as billing ("live") and the script ABORTS in
#     section 0 unless you explicitly consent with CONFIRM=1 or --confirm.
#
# Security:
#   - The API key is read from the environment only, written to a 0700 temp
#     dir as a curl header file (keeps it off the process argv / `ps` output),
#     and deleted on exit. It is never echoed. Do NOT add `set -x` to this
#     script: xtrace would leak request headers to stderr.
# =============================================================================

set -euo pipefail

# Refuse to run under a non-bash shell (we use [[ ]], arrays, etc.).
if [[ -z "${BASH_VERSION:-}" ]]; then
  echo "ERROR: this script requires bash (run it as: bash quickstart.sh)" >&2
  exit 2
fi

# -----------------------------------------------------------------------------
# Constants — pricing (1 credit = 1 cent = $0.01 exactly).
# Source: Coasty pricing table (docs/API_NOTES.md §Pricing).
# -----------------------------------------------------------------------------
readonly COST_PREDICT_CREDITS=5          # POST /predict (no surcharges here:
                                         # 320x240 is not HD, no trajectory,
                                         # no >500-char system_prompt, v3)
readonly COST_GROUND_CREDITS=3           # POST /ground (not HD => no +1)
readonly COST_SESSION_CREATE_CREDITS=10  # POST /sessions (one-time, no surcharges)
readonly COST_SESSION_PREDICT_CREDITS=4  # POST /sessions/{id}/predict (first
                                         # step: server trajectory still empty)
readonly COST_RUN_STEP_CREDITS=5         # per completed run step on v3/v4
readonly RUN_MAX_STEPS=2                 # we cap the demo run at 2 steps

# Maximum this script can bill on a live key (machine runtime excluded; see
# the cost table below — runtime is metered per minute and rounded DOWN, so a
# sub-minute demo machine meters 0 minutes).
readonly TOTAL_MAX_CREDITS=$((
  COST_PREDICT_CREDITS
  + COST_GROUND_CREDITS
  + COST_SESSION_CREATE_CREDITS
  + COST_SESSION_PREDICT_CREDITS
  + RUN_MAX_STEPS * COST_RUN_STEP_CREDITS
))

# The deliberately-INVALID key used by the 401 demo in section 7. This is an
# obviously fake value (48 zeros) that can never authenticate. Your real key
# is not used, sent, or printed anywhere in that section.
readonly FAKE_API_KEY="sk-coasty-test-000000000000000000000000000000000000000000000000"

# A real, tiny 320x240 PNG (light-gray background with a blue "OK button"
# rectangle), embedded as a heredoc constant so the script is self-contained.
# 320x240 matches the API minimums for screen_width/screen_height, so the
# dimensions we send are TRUE for the pixels we send — see the coordinate
# scaling notes in section 3. The API requires base64 > 100 chars; this is
# ~900 chars of valid base64 (no "data:image/png;base64," prefix — the API
# rejects data: URLs with 422 INVALID_SCREENSHOT).
SCREENSHOT_B64="$(tr -d ' \n' <<'PNG_B64'
iVBORw0KGgoAAAANSUhEUgAAAUAAAADwCAIAAAD+Tyo8AAACa0lEQVR42u3TQQ0AMAgEwVNSdZWN
A0zUAl/SmaDgyKaBtWICEDAgYEDAIGBAwICAAQGDgAEBAwIGAQMCBgQMCBgEDAgYEDAgYBAwIGBA
wCBgQMCAgAEBg4ABAQMCBgQMAgYEDAgYBAwIGBAwIGAQMCBgQMAgYEDAgIABAYOAAQEDAgYEDAIG
BAwIGAQMCBgQMCBgEDAgYEDAgIBBwICAAQGDgAEBAwIGBAwCBgQMCBgQMAgYEDAgYBAwIGBAwICA
QcCAgAEBg4ABAQMCBgQMAgYEDAgYEDAIGBAwIGAQMCBgQMCAgEHAgIABAQMCBgEzdG59dT4uYAEL
GAELWMACRsACFrCABYyABSxgBCxgAQsYAQtYwAIWMAIWsIAFjIAFLGAEjIAFLGAELGABCxgBC1jA
AhYwAhawgAWMgAUsYAQsYAELWMAIWMACFjACFrCAETACFrCAEbCABSxgBCxgAQtYwAhYwAIWMAIW
sIARsIAFLGABI2ABC1jACFjAAhawgBGwgAWMgAUsYAEjYAELWMACRsACFrCAEbCABQwIGBAwCBgQ
MCBgQMAgYEDAgIBBwICAAQEDAgYBAwIGBAwCBgQMCBgQMAgYEDAgYEDAIGBAwICAQcCAgAEBAwIG
AQMCBgQMCBgEDAgYEDAIGBAwIGBAwCBgQMCAgAEBg4ABAQMCBgEDAgYEDAgYBAwIGBAwCBgQMCBg
QMAgYEDAgIABAYOAAQEDAgYBAwIGBAwIGAQMCBgQMCBgEDAgYEDAIGBAwICAAQGDgAEBAwIGAZsA
BAwIGBAwCBgQMCBgQMAgYEDAgIBBwICAAQEDIw+rqwXohB3P2QAAAABJRU5ErkJggg==
PNG_B64
)"
readonly SCREENSHOT_B64
readonly DEMO_W=320   # MUST match the embedded PNG's real pixel width
readonly DEMO_H=240   # MUST match the embedded PNG's real pixel height

# -----------------------------------------------------------------------------
# Small helpers
# -----------------------------------------------------------------------------

usage() {
  cat <<'USAGE'
Usage: quickstart.sh [--confirm]

A guided curl walkthrough of the Coasty Computer Use API.

Options:
  --confirm     consent to spending with a LIVE key (same as CONFIRM=1)
  -h, --help    show this help

Required environment:
  COASTY_API_KEY        your API key (sandbox keys start with sk-coasty-test-)

Optional environment:
  COASTY_BASE_URL       default https://coasty.ai/v1
                        (mock server: http://127.0.0.1:8787/v1)
  COASTY_MACHINE_ID     existing machine id for section 6 (skips provisioning)
  COASTY_POLL_INTERVAL  seconds between run polls (default 2)
  CONFIRM=1             same as --confirm
USAGE
}

section() { printf '\n=== %s ===\n' "$*"; }
info()    { printf '%s\n' "$*"; }
warn()    { printf 'WARNING: %s\n' "$*" >&2; }
die()     { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# Format an integer credit amount as US dollars (1 credit = 1 cent).
credits_usd() { printf '$%d.%02d' "$(($1 / 100))" "$(($1 % 100))"; }

# Convert an MSYS/Cygwin path (Git Bash on Windows) to a mixed C:/ form that
# both the native Windows curl AND the msys coreutils understand. A no-op
# everywhere else (Linux, macOS, WSL).
native_path() {
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -m "$1"
  else
    printf '%s\n' "$1"
  fi
}

# -----------------------------------------------------------------------------
# JSON extraction — prefer jq, fall back to python3/python (feature-detected).
#
# Note the EXECUTION probe for python: on Windows, `python3` can resolve to a
# Microsoft Store stub that exists on PATH but does not actually run, so
# `command -v` alone is not enough.
# -----------------------------------------------------------------------------
JSON_TOOL=""
detect_json_tool() {
  if command -v jq >/dev/null 2>&1; then
    JSON_TOOL="jq"
  elif command -v python3 >/dev/null 2>&1 \
      && python3 -c 'pass' >/dev/null 2>&1; then
    JSON_TOOL="python3"
  elif command -v python >/dev/null 2>&1 \
      && python -c 'pass' >/dev/null 2>&1; then
    JSON_TOOL="python"
  else
    die "need jq or python3/python on PATH for JSON extraction (jq preferred)"
  fi
}

# json_get JSON DOTTED_PATH
#   Extract a scalar (or sub-object, serialized) at a dotted path, e.g.
#     json_get "$RESP_BODY" 'error.code'
#     json_get "$RESP_BODY" 'actions.0.action_type'   # numeric = array index
#   Prints "" when the path is absent (caller decides whether that is fatal).
json_get() {
  local json="$1" path="$2"
  if [[ "$JSON_TOOL" == "jq" ]]; then
    # -r prints strings raw; -c keeps non-string results on one line.
    printf '%s' "$json" | jq -rc --arg p "$path" \
      'getpath($p / "." | map(tonumber? // .)) | if . == null then "" else . end'
  else
    printf '%s' "$json" | "$JSON_TOOL" -c '
import json, sys
node = json.load(sys.stdin)
for part in sys.argv[1].split("."):
    if isinstance(node, list):
        try:
            node = node[int(part)]
        except (ValueError, IndexError):
            node = None
    elif isinstance(node, dict):
        node = node.get(part)
    else:
        node = None
    if node is None:
        break
if node is None:
    print("")
elif isinstance(node, bool):
    print("true" if node else "false")
elif isinstance(node, (dict, list)):
    print(json.dumps(node))
else:
    print(node)
' "$path"
  fi
}

# json_pretty JSON — pretty-print a JSON document to stdout.
json_pretty() {
  if [[ "$JSON_TOOL" == "jq" ]]; then
    printf '%s' "$1" | jq .
  else
    printf '%s' "$1" | "$JSON_TOOL" -m json.tool
  fi
}

# -----------------------------------------------------------------------------
# HTTP layer — one helper wrapping curl.
#
#   request METHOD PATH [JSON_BODY] [EXTRA_HEADER ...]
#
# After the call these globals are set:
#   RESP_STATUS        - HTTP status code (e.g. 200, 401)
#   RESP_BODY          - response body text
#   RESP_HEADERS_FILE  - raw response headers (-D / --dump-header output),
#                        used by header_value() and shown off in section 7
#
# Retry policy (mirrors docs/API_NOTES.md):
#   - curl --retry handles transient failures (408/429/5xx + timeouts) with
#     backoff and honors the Retry-After header.
#   - GET/DELETE are always safe to retry.
#   - POSTs are retried ONLY when they are documented-safe: /predict, /ground,
#     /parse and session predicts (charged-then-refunded on failure), or when
#     an Idempotency-Key header makes the retry a server-side no-op.
#     POST /sessions has no idempotency support, so it is never auto-retried.
# -----------------------------------------------------------------------------
RESP_STATUS=""
RESP_BODY=""
request() {
  local method="$1" path="$2" body="${3-}"
  shift 2
  if [[ $# -gt 0 ]]; then shift; fi  # drop the body arg; the rest are headers

  local curl_args=(
    --silent --show-error
    --request "$method"
    --url "${BASE_URL}${path}"
    --header "@${AUTH_HEADER_FILE}"
    --header "Accept: application/json"
    --dump-header "$RESP_HEADERS_FILE"
    --output "$RESP_BODY_FILE"
    --write-out '%{http_code}'
    --max-time 120
  )

  local retry_ok=0 h
  case "$method" in
    GET|HEAD|DELETE) retry_ok=1 ;;
    POST)
      case "$path" in
        /predict|/ground|/parse|*/predict) retry_ok=1 ;;
      esac
      for h in "$@"; do
        case "$h" in "Idempotency-Key:"*) retry_ok=1 ;; esac
      done
      ;;
  esac
  if [[ "$retry_ok" == "1" ]]; then
    curl_args+=(--retry 3 --retry-max-time 60)
  fi

  for h in "$@"; do
    curl_args+=(--header "$h")
  done
  if [[ -n "$body" ]]; then
    curl_args+=(--header "Content-Type: application/json" --data "$body")
  fi

  RESP_STATUS="$(curl "${curl_args[@]}")"
  RESP_BODY="$(cat "$RESP_BODY_FILE")"
}

# header_value NAME — case-insensitive lookup in the last response's headers.
header_value() {
  local name="$1"
  { grep -i "^${name}:" "$RESP_HEADERS_FILE" || true; } \
    | tail -n 1 | sed 's/^[^:]*:[[:space:]]*//' | tr -d '\r'
}

# show_header NAME — print one response header (or note its absence).
show_header() {
  local name="$1" val
  val="$(header_value "$name")"
  printf '  %-26s %s\n' "${name}:" "${val:-(not present on this response)}"
}

# api_error_summary — print the stable error-envelope fields from the last
# response. Branch on error.code, NEVER on error.message (messages may change
# between API versions). error.request_id == X-Coasty-Request-Id header; quote
# it verbatim when contacting support.
api_error_summary() {
  printf '  HTTP status:        %s\n' "$RESP_STATUS"
  printf '  error.code:         %s\n' "$(json_get "$RESP_BODY" 'error.code')"
  printf '  error.type:         %s\n' "$(json_get "$RESP_BODY" 'error.type')"
  printf '  error.message:      %s\n' "$(json_get "$RESP_BODY" 'error.message')"
  printf '  error.request_id:   %s\n' "$(json_get "$RESP_BODY" 'error.request_id')"
  local extra
  extra="$(json_get "$RESP_BODY" 'error.suggestion')"
  if [[ -n "$extra" ]]; then printf '  error.suggestion:   %s\n' "$extra"; fi
  extra="$(json_get "$RESP_BODY" 'error.docs_url')"
  if [[ -n "$extra" ]]; then printf '  error.docs_url:     %s\n' "$extra"; fi
}

# require_ok WHAT — abort (with the full error envelope, incl. request_id) if
# the last response was not 2xx. No silent failures.
require_ok() {
  if [[ "$RESP_STATUS" != 2* ]]; then
    echo "ERROR: $1 failed:" >&2
    api_error_summary >&2
    exit 1
  fi
}

# -----------------------------------------------------------------------------
# Cleanup — ALWAYS runs (trap on EXIT): deletes the session, terminates the
# machine we provisioned (if any), and removes the temp dir holding the auth
# header file. Best-effort: cleanup never masks the script's real exit code.
# -----------------------------------------------------------------------------
SESSION_ID=""
CREATED_MACHINE_ID=""
TMP_DIR=""
cleanup() {
  local exit_code=$?
  trap - EXIT
  if [[ -n "$SESSION_ID" ]]; then
    curl --silent --output /dev/null --max-time 30 \
      --request DELETE --url "${BASE_URL}/sessions/${SESSION_ID}" \
      --header "@${AUTH_HEADER_FILE}" || true
    info "[cleanup] deleted session ${SESSION_ID}"
  fi
  if [[ -n "$CREATED_MACHINE_ID" ]]; then
    curl --silent --output /dev/null --max-time 30 \
      --request DELETE --url "${BASE_URL}/machines/${CREATED_MACHINE_ID}" \
      --header "@${AUTH_HEADER_FILE}" || true
    info "[cleanup] terminated machine ${CREATED_MACHINE_ID}"
  fi
  if [[ -n "$TMP_DIR" ]]; then
    rm -rf -- "$TMP_DIR" || true
  fi
  exit "$exit_code"
}

# =============================================================================
# Section 0 — preflight: key, sandbox/live detection, cost table, consent
# =============================================================================

CONFIRM_FLAG="${CONFIRM:-0}"
for arg in "$@"; do
  case "$arg" in
    --confirm) CONFIRM_FLAG=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument: $arg" ;;
  esac
done

section "Section 0: preflight"

if [[ -z "${COASTY_API_KEY:-}" ]]; then
  die "COASTY_API_KEY is not set. Get a key at https://coasty.ai/developers/keys
       (sandbox keys start with sk-coasty-test- and never bill), then:
         export COASTY_API_KEY=\"sk-coasty-test-...\""
fi

# Normalize the base URL (strip a trailing slash so path concatenation is safe).
BASE_URL="${COASTY_BASE_URL:-https://coasty.ai/v1}"
BASE_URL="${BASE_URL%/}"
readonly BASE_URL

command -v curl >/dev/null 2>&1 || die "curl is required on PATH"
detect_json_tool

# Sandbox vs live: sandbox keys are exactly the sk-coasty-test- prefix.
# Everything else (sk-coasty-live-, legacy cua_sk_) BILLS and needs consent.
KEY_KIND="live"
if [[ "$COASTY_API_KEY" == sk-coasty-test-* ]]; then
  KEY_KIND="sandbox"
fi
readonly KEY_KIND

info "Base URL:       ${BASE_URL}"
info "JSON tool:      ${JSON_TOOL}"
info "Key kind:       ${KEY_KIND} (detected from the key prefix; key NOT shown)"
info ""
info "This script will perform the following billable work:"
# NOTE: quoted heredoc — dollar amounts below are literal text, not expansions.
cat <<'COSTS'
  ----------------------------------------------------------------------------
  Section 1  GET  /models                                 free
  Section 2  POST /parse                                  free
  Section 3  POST /predict (320x240, no surcharges)       5 credits  ($0.05)
  Section 4  POST /ground  (320x240, not HD)              3 credits  ($0.03)
  Section 5  POST /sessions (create)                     10 credits  ($0.10)
             POST /sessions/{id}/predict (1 step)         4 credits  ($0.04)
             DELETE /sessions/{id}                        free
  Section 6  POST /machines (if no COASTY_MACHINE_ID)     no fee; needs wallet
                                                          >= 20 credits (gate,
                                                          not a charge); Linux
                                                          runtime bills 5 cr/hr
                                                          metered PER MINUTE,
                                                          rounded down (a sub-
                                                          minute demo = 0)
             POST /runs (max 2 steps x 5 credits)        <= 10 credits ($0.10)
  Section 7  headers demo + deliberate 401                free
  ----------------------------------------------------------------------------
COSTS
# shellcheck disable=SC2016  # "$0.00" below is intentionally literal text
printf '  Maximum total: %s credits (%s) + machine runtime (typically $0.00)\n' \
  "$TOTAL_MAX_CREDITS" "$(credits_usd "$TOTAL_MAX_CREDITS")"

if [[ "$KEY_KIND" == "sandbox" ]]; then
  info "  Your key is a SANDBOX key: everything above is \$0.00 (never bills)."
else
  info "  Your key is a LIVE key: the credits above WILL be charged to your wallet."
  if [[ "$CONFIRM_FLAG" != "1" ]]; then
    echo "" >&2
    echo "ABORTING: refusing to spend with a live key without explicit consent." >&2
    echo "Re-run with either of:" >&2
    echo "  CONFIRM=1 $0" >&2
    echo "  $0 --confirm" >&2
    echo "or use a free sandbox key (sk-coasty-test-...) instead." >&2
    exit 3
  fi
  info "  --confirm/CONFIRM=1 given: proceeding with the live key."
fi

# Private workspace for the auth header file + response captures.
# umask 077 => files are owner-only. Deleted by the EXIT trap.
umask 077
TMP_DIR="$(mktemp -d)"
TMP_DIR="$(native_path "$TMP_DIR")"  # Git Bash on Windows: make it native-curl safe
readonly AUTH_HEADER_FILE="${TMP_DIR}/auth_header"
readonly RESP_HEADERS_FILE="${TMP_DIR}/last_response_headers"
readonly RESP_BODY_FILE="${TMP_DIR}/last_response_body"

# The key goes into a header FILE (curl's -H @file form) so it never appears
# on the curl command line, where other local processes could read it from
# the process table. Authorization: Bearer <key> would work identically.
printf 'X-API-Key: %s\n' "$COASTY_API_KEY" > "$AUTH_HEADER_FILE"

trap cleanup EXIT

# =============================================================================
# Section 1 — GET /v1/models (free)
# =============================================================================
# Lists models, CUA engine versions (v1/v3/v4), and the full action_types
# vocabulary the model can return. Also a cheap connectivity/auth check.

section "Section 1: GET /models (free)"
request GET "/models" ""
require_ok "GET /models"
json_pretty "$RESP_BODY"

# =============================================================================
# Section 2 — POST /v1/parse (free)
# =============================================================================
# Deterministically converts raw pyautogui source into the same structured
# action objects /predict returns. No model call, $0. Handy for migrating
# existing pyautogui scripts onto the structured executor.

section "Section 2: POST /parse (free)"
# Quoted heredoc: the \n sequences below are literal JSON escapes, exactly
# what the API expects inside a JSON string.
PARSE_BODY="$(cat <<'JSON'
{
  "code": "pyautogui.click(120, 80)\npyautogui.typewrite('hello@example.com')\npyautogui.press('enter')"
}
JSON
)"
request POST "/parse" "$PARSE_BODY"
require_ok "POST /parse"
info "Parsed actions:"
json_pretty "$RESP_BODY"
info "First action: $(json_get "$RESP_BODY" 'actions.0.action_type') $(json_get "$RESP_BODY" 'actions.0.params')"

# =============================================================================
# Section 3 — POST /v1/predict (5 credits)
# =============================================================================
# Stateless prediction: one screenshot + an instruction in, ordered actions
# out. We send the embedded 320x240 PNG and — crucially — screen_width=320,
# screen_height=240 to MATCH it.
#
# ---- THE #1 PITFALL: coordinate scaling -------------------------------------
# Returned x/y coordinates are in the pixel space of the screenshot YOU SENT,
# not your physical screen. If you downscale (e.g. a 2560x1440 desktop resized
# to 1280x720 to dodge the +1 credit HD surcharge — HD means width > 1280 OR
# height > 720, so exactly 1280x720 is NOT HD):
#   1. pass the DOWNSCALED size as screen_width/screen_height, and
#   2. multiply returned x/y by (real / sent) before clicking:
#        real_x = x * 2560 / 1280 ; real_y = y * 1440 / 720
# A screenshot whose true size disagrees with screen_width/screen_height is
# the number-one cause of "the agent clicks the wrong place".
# ------------------------------------------------------------------------------

section "Section 3: POST /predict (5 credits; \$0 on sandbox)"
PREDICT_BODY="$(cat <<JSON
{
  "screenshot": "${SCREENSHOT_B64}",
  "instruction": "Click the OK button",
  "cua_version": "v3",
  "screen_width": ${DEMO_W},
  "screen_height": ${DEMO_H},
  "max_actions": 3
}
JSON
)"
request POST "/predict" "$PREDICT_BODY"
require_ok "POST /predict"
info "status:          $(json_get "$RESP_BODY" 'status')   (continue | done | fail)"
info "reasoning:       $(json_get "$RESP_BODY" 'reasoning')"
info "first action:    $(json_get "$RESP_BODY" 'actions.0.action_type') $(json_get "$RESP_BODY" 'actions.0.params')"
info "credits charged: $(json_get "$RESP_BODY" 'usage.credits_charged') (usage.cost_cents=$(json_get "$RESP_BODY" 'usage.cost_cents'))"

# =============================================================================
# Section 4 — POST /v1/ground (3 credits)
# =============================================================================
# Grounding maps a natural-language element description to exact (x, y) pixel
# coordinates — same screenshot rules and the SAME scaling pitfall as above.

section "Section 4: POST /ground (3 credits; \$0 on sandbox)"
GROUND_BODY="$(cat <<JSON
{
  "screenshot": "${SCREENSHOT_B64}",
  "element": "the blue OK button",
  "screen_width": ${DEMO_W},
  "screen_height": ${DEMO_H}
}
JSON
)"
request POST "/ground" "$GROUND_BODY"
require_ok "POST /ground"
GROUND_X="$(json_get "$RESP_BODY" 'x')"
GROUND_Y="$(json_get "$RESP_BODY" 'y')"
info "Element located at (x=${GROUND_X}, y=${GROUND_Y}) in the SENT ${DEMO_W}x${DEMO_H} space."
info "(On a real 1920x1080 screen downscaled to ${DEMO_W}x${DEMO_H} you would click"
info " at x*1920/${DEMO_W}, y*1080/${DEMO_H}.)"

# =============================================================================
# Section 5 — session lifecycle: create -> predict -> DELETE
# =============================================================================
# Sessions are stateful /predict: the server keeps the trajectory, so each
# step sends only the newest screenshot. Create costs 10 credits one-time;
# each step costs 4 (cheaper than stateless 5). Sessions have NO per-minute
# cost, but they DO occupy a concurrency slot — always DELETE when done.
# The EXIT trap above guarantees deletion even if a step below fails.

section "Section 5: sessions (create 10 + predict 4 + delete free)"
SESSION_BODY="$(cat <<JSON
{
  "cua_version": "v3",
  "screen_width": ${DEMO_W},
  "screen_height": ${DEMO_H},
  "max_trajectory_length": 3
}
JSON
)"
request POST "/sessions" "$SESSION_BODY"
require_ok "POST /sessions"
SESSION_ID="$(json_get "$RESP_BODY" 'session_id')"
[[ -n "$SESSION_ID" ]] || die "POST /sessions returned no session_id"
info "Created session: ${SESSION_ID} (expires_at=$(json_get "$RESP_BODY" 'expires_at'))"

SESSION_PREDICT_BODY="$(cat <<JSON
{
  "screenshot": "${SCREENSHOT_B64}",
  "instruction": "Click the OK button"
}
JSON
)"
request POST "/sessions/${SESSION_ID}/predict" "$SESSION_PREDICT_BODY"
require_ok "POST /sessions/{id}/predict"
info "Session step $(json_get "$RESP_BODY" 'step'): status=$(json_get "$RESP_BODY" 'status'), first action=$(json_get "$RESP_BODY" 'actions.0.action_type')"
info "(In a real loop you would repeat this until status != \"continue\".)"

# Explicit DELETE (the trap is only the safety net for failure paths).
request DELETE "/sessions/${SESSION_ID}" ""
require_ok "DELETE /sessions/{id}"
info "Session deleted: ${SESSION_ID} (concurrency slot freed)"
SESSION_ID=""  # tell the trap there is nothing left to clean up

# =============================================================================
# Section 6 — task runs: create (Idempotency-Key) -> poll -> terminal
# =============================================================================
# A run hands the agent a whole task on a machine and drives it server-side.
# Each completed step bills 5 credits on v3/v4 (8 on v1); we cap at
# max_steps=2 so this demo bills at most 10 credits.

section "Section 6: runs (max ${RUN_MAX_STEPS} steps x ${COST_RUN_STEP_CREDITS} credits)"

# Runs need a machine. Reuse COASTY_MACHINE_ID if given; otherwise provision a
# temporary one. Provisioning itself has NO fee, but requires a wallet balance
# of >= 20 credits as a gate; sandbox keys get an instant free mch_test_* VM.
# ttl_minutes=10 is a server-side auto-terminate safety net on top of our trap.
MACHINE_ID="${COASTY_MACHINE_ID:-}"
if [[ -z "$MACHINE_ID" ]]; then
  MACHINE_BODY="$(cat <<'JSON'
{
  "display_name": "curl-quickstart",
  "os_type": "linux",
  "ttl_minutes": 10
}
JSON
)"
  # POST /machines supports Idempotency-Key, which also makes curl's transient
  # retry safe (a replay returns the cached response, not a second machine).
  request POST "/machines" "$MACHINE_BODY" \
    "Idempotency-Key: curl-quickstart-machine-$(date +%s)-$$"
  require_ok "POST /machines"
  MACHINE_ID="$(json_get "$RESP_BODY" 'machine.id')"
  [[ -n "$MACHINE_ID" ]] || die "POST /machines returned no machine.id"
  CREATED_MACHINE_ID="$MACHINE_ID"  # the EXIT trap will terminate it
  info "Provisioned machine: ${MACHINE_ID} (auto-terminates after 10 min; trap terminates it sooner)"
else
  info "Reusing machine from COASTY_MACHINE_ID: ${MACHINE_ID}"
fi

# Idempotency-Key (<=128 chars of [A-Za-z0-9_-:]) makes a retried create safe:
# replaying the SAME key + SAME body returns the original run (with header
# X-Coasty-Idempotent-Replay: true) instead of starting a duplicate. Reusing
# the key with a DIFFERENT body is rejected with code IDEMPOTENCY_KEY_REUSED.
# (The docs list that code under both 422 and 409 — branch on the CODE, not
# the HTTP status.)
IDEMPOTENCY_KEY="curl-quickstart-run-$(date +%s)-$$"

# on_awaiting_human=fail: if the agent gets stuck it fails fast instead of
# pausing for a human, so this demo can never hang in awaiting_human. Use
# "pause" in real integrations and resume with POST /runs/{id}/resume.
RUN_BODY="$(cat <<JSON
{
  "machine_id": "${MACHINE_ID}",
  "task": "Open a terminal and run the 'date' command",
  "cua_version": "v3",
  "max_steps": ${RUN_MAX_STEPS},
  "on_awaiting_human": "fail"
}
JSON
)"
request POST "/runs" "$RUN_BODY" "Idempotency-Key: ${IDEMPOTENCY_KEY}"
require_ok "POST /runs"
RUN_ID="$(json_get "$RESP_BODY" 'id')"
[[ -n "$RUN_ID" ]] || die "POST /runs returned no id"
RUN_STATUS="$(json_get "$RESP_BODY" 'status')"
info "Created run ${RUN_ID} (status=${RUN_STATUS})"
# webhook_secret is returned ONCE on create (only when webhook_url is set) and
# is null on every later GET — if you use webhooks, store it from THIS response.

# ---- Poll until a terminal state --------------------------------------------
# Terminal states are immutable: succeeded | failed | cancelled | timed_out.
POLL_INTERVAL="${COASTY_POLL_INTERVAL:-2}"
MAX_POLLS=90
poll=0
while true; do
  case "$RUN_STATUS" in
    succeeded|failed|cancelled|timed_out)
      break
      ;;
    awaiting_human)
      # Unreachable here (we set on_awaiting_human=fail), but in a real
      # integration this is where a human finishes the blocking step and you
      # call: POST /runs/{id}/resume  with body {"note": "..."}.
      info "Run paused for a human: $(json_get "$RESP_BODY" 'awaiting_human_reason')"
      break
      ;;
  esac
  if (( poll >= MAX_POLLS )); then
    die "run ${RUN_ID} did not reach a terminal state after ${MAX_POLLS} polls (last status: ${RUN_STATUS})"
  fi
  sleep "$POLL_INTERVAL"
  request GET "/runs/${RUN_ID}" ""
  require_ok "GET /runs/{id}"
  RUN_STATUS="$(json_get "$RESP_BODY" 'status')"
  poll=$((poll + 1))
  info "  poll #${poll}: status=${RUN_STATUS} steps_completed=$(json_get "$RESP_BODY" 'steps_completed')"
done

info "Run finished: status=${RUN_STATUS}"
info "  result.summary:  $(json_get "$RESP_BODY" 'result.summary')"
info "  credits_charged: $(json_get "$RESP_BODY" 'credits_charged') (cost_cents=$(json_get "$RESP_BODY" 'cost_cents'))"
if [[ "$RUN_STATUS" == "failed" ]]; then
  info "  error.code:      $(json_get "$RESP_BODY" 'error.code')"
  info "  error.message:   $(json_get "$RESP_BODY" 'error.message')"
fi

# ---- Streaming alternative: Server-Sent Events (SSE) -------------------------
# Instead of polling, stream the run's durable event log. Frames look like:
#
#   id: 42                      <- the event's sequence number (your cursor)
#   event: status               <- status|text|reasoning|tool_call|tool_result|
#   data: {"status":"running"}     awaiting_human|resumed|step|billing|error|done
#                               <- blank line terminates the frame
#
# Lines starting with ":" are keepalive comments — ignore them. The stream
# closes after the "done" event. Events are durable server-side and "id" (seq)
# is the cursor, so a dropped connection NEVER loses or duplicates events:
# remember the last id you processed and reconnect with the Last-Event-ID
# header (or its query-param twin ?after=<seq>) to replay everything after it.
#
#   # -N disables curl's output buffering so frames arrive as they happen
#   curl -N -sS "${BASE_URL}/runs/${RUN_ID}/events" \
#     -H "@${AUTH_HEADER_FILE}" \
#     -H "Last-Event-ID: 42"
#
# (Left commented out: this quickstart's run is already terminal, so the
#  stream would just replay history and close after "done".)

# Terminate the machine we provisioned now rather than waiting for the trap.
if [[ -n "$CREATED_MACHINE_ID" ]]; then
  request DELETE "/machines/${CREATED_MACHINE_ID}" ""
  require_ok "DELETE /machines/{id}"
  info "Machine terminated: ${CREATED_MACHINE_ID}"
  CREATED_MACHINE_ID=""  # nothing left for the trap
fi

# =============================================================================
# Section 7 — response headers + the error envelope (deliberate 401 demo)
# =============================================================================

section "Section 7: response headers and the error envelope"

# 7a. Headers. Every request() call above already dumps response headers via
# curl's -D/--dump-header. Standalone you would run, e.g.:
#   curl -sS -D headers.txt -o body.json "$COASTY_BASE_URL/models" \
#     -H "X-API-Key: $COASTY_API_KEY"
# Key headers on every response:
#   X-Coasty-Request-Id   — unique request id; quote it to support. Errors
#                           repeat it in the body as error.request_id.
#   X-Credits-Charged     — cost of THIS request in credits (0 on test keys);
#                           only on billed responses.
#   X-Credits-Remaining   — wallet balance after the charge (USD cents).
#   X-Coasty-Test-Mode    — "true" when a sandbox key authenticated.
#   X-Coasty-Key-Kind     — live | test | legacy.
request GET "/models" ""
require_ok "GET /models (headers demo)"
info "Headers from a fresh GET /models response:"
show_header "X-Coasty-Request-Id"
show_header "X-Credits-Charged"
show_header "X-Credits-Remaining"
show_header "X-Coasty-Test-Mode"
show_header "X-Coasty-Key-Kind"

# 7b. The error envelope — a DELIBERATE 401.
# ############################################################################
# # DELIBERATE FAILURE DEMO: the request below uses an obviously FAKE key    #
# # (sk-coasty-test- + 48 zeros). Your real COASTY_API_KEY is NOT used here  #
# # and is never printed anywhere in this script. Expected result: HTTP 401  #
# # with code INVALID_API_KEY and a WWW-Authenticate challenge header.       #
# ############################################################################
info ""
info "Deliberate 401 demo (fake key; your real key is not used or shown):"
FAKE_AUTH_FILE="${TMP_DIR}/fake_auth_header"
printf 'X-API-Key: %s\n' "$FAKE_API_KEY" > "$FAKE_AUTH_FILE"
DEMO_STATUS="$(curl --silent --request GET --url "${BASE_URL}/models" \
  --header "@${FAKE_AUTH_FILE}" \
  --header "Accept: application/json" \
  --dump-header "$RESP_HEADERS_FILE" \
  --output "$RESP_BODY_FILE" \
  --write-out '%{http_code}' \
  --max-time 60)" || DEMO_STATUS="000"
RESP_STATUS="$DEMO_STATUS"
RESP_BODY="$(cat "$RESP_BODY_FILE")"
if [[ "$RESP_STATUS" != "401" ]]; then
  die "expected the deliberate bad-key request to return HTTP 401, got: ${RESP_STATUS}"
fi
info "Got the expected HTTP 401. Every Coasty error uses one envelope shape:"
info '  {"error": {"code", "message", "type", "request_id", "suggestion?", "docs_url?", ...context}}'
api_error_summary
show_header "WWW-Authenticate"
show_header "X-Coasty-Request-Id"
info "Rule of thumb: branch on error.code (stable), never parse error.message."

# =============================================================================
section "All sections completed."
info "Cleanup of the session/machine (if still alive) runs automatically via the EXIT trap."
