#!/usr/bin/env bash
# post-tool-log.sh — PostToolUse observability (all tools), FAIL-OPEN.
#
# Appends one JSON line per tool call to .claude/logs/YYYY-MM-DD-tools.jsonl. This is the
# raw material for the harness-profile's "observe, then promote" step (docs/harness-profile.md):
# review advisory-hook fires monthly and promote the repeat offenders to blocking gates.
#
# Wire it up in .claude/settings.json:
#   "hooks": {
#     "PostToolUse": [
#       { "hooks": [{ "type": "command", "command": ".claude/hooks/post-tool-log.sh" }] }
#     ]
#   }
#
# Contract (see docs/hooks-guide.md): FAIL-OPEN (exit 0 only) — logging must never disrupt
# the session. Skips silently if jq is absent.
set -uo pipefail

command -v jq >/dev/null 2>&1 || exit 0

INPUT=$(cat)
TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)
[ -n "$TOOL_NAME" ] || exit 0

MATCHER=$(printf '%s' "$INPUT" | jq -r '.matcher // empty' 2>/dev/null)
EXIT_CODE=$(printf '%s' "$INPUT" | jq -r '.tool_response.exit_code // .tool_response.exitCode // empty' 2>/dev/null)

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
LOG_DIR="$REPO_ROOT/.claude/logs"
mkdir -p "$LOG_DIR" 2>/dev/null || exit 0

DATE=$(date +%Y-%m-%d)
TS=$(date +%Y-%m-%dT%H:%M:%S%z)
LOG_FILE="$LOG_DIR/$DATE-tools.jsonl"

# jq -n handles JSON escaping of every field.
jq -nc \
  --arg ts "$TS" \
  --arg tool "$TOOL_NAME" \
  --arg matcher "$MATCHER" \
  --arg exit_code "$EXIT_CODE" \
  '{ts:$ts, tool:$tool, matcher:$matcher, exit_code:$exit_code}' \
  >> "$LOG_FILE" 2>/dev/null

exit 0
