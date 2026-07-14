#!/usr/bin/env bash
# Example Notification hook — NOTIFICATION, FAIL-OPEN.
#
# Wire it up in .claude/settings.json:
#   "hooks": {
#     "Notification": [
#       { "hooks": [{ "type": "command", "command": ".claude/hooks/notification-notify-example.sh" }] }
#     ]
#   }
#
# Contract (see docs/hooks-guide.md):
#   - Input arrives as JSON on STDIN; parse with jq.
#   - Notification / Stop / SessionEnd / UserPromptSubmit hooks FAIL-OPEN: any
#     problem exits 0 so the session is NEVER disrupted by the hook.
#   - Best-effort desktop notification, portable across macOS (osascript) and
#     Linux (notify-send); neither present → fall back to stdout. None of these
#     is an error.
set -uo pipefail

fail_open() { exit 0; }

command -v jq >/dev/null 2>&1 || fail_open

input="$(cat)" || fail_open
msg="$(printf '%s' "$input" | jq -r '.message // empty')" || fail_open
[ -n "$msg" ] || fail_open

if command -v osascript >/dev/null 2>&1; then
  # macOS. The message is passed as data, not interpolated into code paths.
  osascript -e 'on run {m}' -e 'display notification m with title "Claude Code"' -e 'end run' \
    -- "$msg" >/dev/null 2>&1 || true
elif command -v notify-send >/dev/null 2>&1; then
  notify-send "Claude Code" "$msg" >/dev/null 2>&1 || true
else
  printf '[Claude Code] %s\n' "$msg"
fi

exit 0
