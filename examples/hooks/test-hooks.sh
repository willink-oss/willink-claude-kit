#!/usr/bin/env bash
# Self-test for the example hooks — asserts BOTH a block case and a pass case for each.
#
# Self-contained on purpose: no external test library, so you can copy examples/hooks/
# into your project wholesale and `bash test-hooks.sh` still works. The kit's CI runs
# this via scripts/test/test_hooks.sh.
#
# Requires jq (the hooks parse stdin JSON via jq). If jq is absent it SKIPS (exit 0)
# rather than failing, to stay friendly in dependency-light environments.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRE="pretooluse-block-example.sh"
NOTIF="notification-notify-example.sh"

pass=0; fail=0
ok()  { pass=$((pass + 1)); printf '  PASS %s\n' "$1"; }
bad() { fail=$((fail + 1)); printf '  FAIL %s\n' "$1"; }

# run_hook <script> <json-stdin> -> echoes the hook's exit code
run_hook() {
  printf '%s' "$2" | bash "$HERE/$1" >/dev/null 2>&1
  printf '%s' "$?"
}

if ! command -v jq >/dev/null 2>&1; then
  printf 'SKIP: jq not installed — hook self-tests need jq. Install jq to run them.\n'
  exit 0
fi

# --- PreToolUse (fail-closed) -------------------------------------------------
# block case: bare root delete -> exit 2
ec="$(run_hook "$PRE" '{"tool_name":"Bash","tool_input":{"command":"rm -rf /"}}')"
[ "$ec" = "2" ] && ok "PreToolUse blocks 'rm -rf /' (exit 2)" || bad "PreToolUse should block 'rm -rf /' (got $ec)"

# block case: top-level system dir delete -> exit 2
ec="$(run_hook "$PRE" '{"tool_name":"Bash","tool_input":{"command":"rm -rf /etc"}}')"
[ "$ec" = "2" ] && ok "PreToolUse blocks 'rm -rf /etc' (exit 2)" || bad "PreToolUse should block 'rm -rf /etc' (got $ec)"

# pass case: safe command -> exit 0
ec="$(run_hook "$PRE" '{"tool_name":"Bash","tool_input":{"command":"ls -la"}}')"
[ "$ec" = "0" ] && ok "PreToolUse allows 'ls -la' (exit 0)" || bad "PreToolUse should allow 'ls -la' (got $ec)"

# pass case: relative-path delete is allowed (denylist gates only root-ish paths —
# documents the illustrative scope; real use should extend it)
ec="$(run_hook "$PRE" '{"tool_name":"Bash","tool_input":{"command":"rm -rf node_modules"}}')"
[ "$ec" = "0" ] && ok "PreToolUse allows 'rm -rf node_modules' (illustrative scope, exit 0)" || bad "PreToolUse should allow relative delete (got $ec)"

# pass case: non-Bash tool is ignored -> exit 0
ec="$(run_hook "$PRE" '{"tool_name":"Read","tool_input":{"file_path":"/etc/hosts"}}')"
[ "$ec" = "0" ] && ok "PreToolUse ignores non-Bash tools (exit 0)" || bad "PreToolUse should ignore Read (got $ec)"

# fail-closed: malformed input must BLOCK, never silently allow -> exit 2
ec="$(run_hook "$PRE" 'not json at all')"
[ "$ec" = "2" ] && ok "PreToolUse fails closed on malformed input (exit 2)" || bad "PreToolUse should fail closed (got $ec)"

# fail-closed: empty stdin must BLOCK, never silently allow -> exit 2
ec="$(run_hook "$PRE" '')"
[ "$ec" = "2" ] && ok "PreToolUse fails closed on empty stdin (exit 2)" || bad "PreToolUse should fail closed on empty stdin (got $ec)"

# --- Notification (fail-open) -------------------------------------------------
# valid input -> exit 0
ec="$(run_hook "$NOTIF" '{"message":"build finished"}')"
[ "$ec" = "0" ] && ok "Notification exits 0 on valid input" || bad "Notification should exit 0 on valid input (got $ec)"

# fail-open: malformed input must NOT disrupt the session -> exit 0
ec="$(run_hook "$NOTIF" 'not json at all')"
[ "$ec" = "0" ] && ok "Notification fails open on malformed input (exit 0)" || bad "Notification should fail open (got $ec)"

printf '  -> %d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
