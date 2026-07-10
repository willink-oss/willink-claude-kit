#!/usr/bin/env bash
# Self-test for the example hooks — asserts BOTH a block case and a pass case for each,
# plus each hook's fail policy on malformed input (docs/hooks-guide.md §4).
#
# Self-contained on purpose: no external test library, so you can copy examples/hooks/
# into your project wholesale and `bash test-hooks.sh` still works. The kit's CI runs
# this via scripts/test/test_hooks.sh, on Linux (GNU grep) AND macOS (BSD grep).
#
# Requires jq (the hooks parse stdin JSON via jq). If jq is absent it SKIPS (exit 0)
# rather than failing, to stay friendly in dependency-light environments.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

pass=0; fail=0
ok()  { pass=$((pass + 1)); printf '  PASS %s\n' "$1"; }
bad() { fail=$((fail + 1)); printf '  FAIL %s\n' "$1"; }

# run_hook <script> <json-stdin> [cwd] -> echoes the hook's exit code
run_hook() {
  local cwd="${3:-$HERE}"
  ( cd "$cwd" && printf '%s' "$2" | bash "$HERE/$1" ) >/dev/null 2>&1
  printf '%s' "$?"
}

if ! command -v jq >/dev/null 2>&1; then
  printf 'SKIP: jq not installed — hook self-tests need jq. Install jq to run them.\n'
  exit 0
fi

TMP="$(mktemp -d 2>/dev/null || mktemp -d -t hooks)"
trap 'rm -rf "$TMP"; rm -f "$HERE/.compact-snapshot"' EXIT

# --- pretooluse-block-example.sh (teaching template, fail-closed) -------------
PRE="pretooluse-block-example.sh"
ec="$(run_hook "$PRE" '{"tool_name":"Bash","tool_input":{"command":"rm -rf /"}}')"
[ "$ec" = "2" ] && ok "block-example blocks 'rm -rf /' (exit 2)" || bad "block-example should block 'rm -rf /' (got $ec)"
ec="$(run_hook "$PRE" '{"tool_name":"Bash","tool_input":{"command":"ls -la"}}')"
[ "$ec" = "0" ] && ok "block-example allows 'ls -la' (exit 0)" || bad "block-example should allow 'ls -la' (got $ec)"
ec="$(run_hook "$PRE" 'not json')"
[ "$ec" = "2" ] && ok "block-example fails closed on malformed input (exit 2)" || bad "block-example should fail closed (got $ec)"

# --- pre-bash-safety.sh (production denylist, fail-closed) ---------------------
BSH="pre-bash-safety.sh"
if [ -f "$HERE/$BSH" ]; then
  ec="$(run_hook "$BSH" '{"tool_name":"Bash","tool_input":{"command":"rm -rf /"}}')"
  [ "$ec" = "2" ] && ok "pre-bash-safety blocks 'rm -rf /' (exit 2)" || bad "pre-bash-safety should block 'rm -rf /' (got $ec)"
  ec="$(run_hook "$BSH" '{"tool_name":"Bash","tool_input":{"command":"git push origin main"}}')"
  [ "$ec" = "2" ] && ok "pre-bash-safety blocks direct push to main (exit 2)" || bad "pre-bash-safety should block push to main (got $ec)"
  ec="$(run_hook "$BSH" '{"tool_name":"Bash","tool_input":{"command":"git reset --hard HEAD~1"}}')"
  [ "$ec" = "2" ] && ok "pre-bash-safety blocks 'git reset --hard' (exit 2)" || bad "pre-bash-safety should block reset --hard (got $ec)"
  # pass: destructive-looking text INSIDE a quoted string is stripped before scanning
  ec="$(run_hook "$BSH" '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"docs: warn about rm -rf /\""}}')"
  [ "$ec" = "0" ] && ok "pre-bash-safety allows 'rm -rf /' quoted in a commit message (exit 0)" || bad "pre-bash-safety should allow quoted mention (got $ec)"
  ec="$(run_hook "$BSH" '{"tool_name":"Bash","tool_input":{"command":"rm -rf node_modules"}}')"
  [ "$ec" = "0" ] && ok "pre-bash-safety allows 'rm -rf node_modules' (exit 0)" || bad "pre-bash-safety should allow relative delete (got $ec)"
  ec="$(run_hook "$BSH" '')"
  [ "$ec" = "2" ] && ok "pre-bash-safety fails closed on empty stdin (exit 2)" || bad "pre-bash-safety should fail closed on empty (got $ec)"
fi

# --- pre-file-protect.sh (Write/Edit guard, fail-closed) ----------------------
PFP="pre-file-protect.sh"
if [ -f "$HERE/$PFP" ]; then
  ec="$(run_hook "$PFP" '{"tool_name":"Write","tool_input":{"file_path":"/proj/.env"}}')"
  [ "$ec" = "2" ] && ok "pre-file-protect blocks .env write (exit 2)" || bad "pre-file-protect should block .env (got $ec)"
  ec="$(run_hook "$PFP" '{"tool_name":"Write","tool_input":{"file_path":"/proj/config/id_rsa"}}')"
  [ "$ec" = "2" ] && ok "pre-file-protect blocks id_rsa write (exit 2)" || bad "pre-file-protect should block id_rsa (got $ec)"
  ec="$(run_hook "$PFP" '{"tool_name":"Write","tool_input":{"file_path":"/proj/.env.example"}}')"
  [ "$ec" = "0" ] && ok "pre-file-protect allows .env.example (exit 0)" || bad "pre-file-protect should allow .env.example (got $ec)"
  ec="$(run_hook "$PFP" '{"tool_name":"Write","tool_input":{"file_path":"/proj/src/app.ts"}}')"
  [ "$ec" = "0" ] && ok "pre-file-protect allows normal source file (exit 0)" || bad "pre-file-protect should allow src/app.ts (got $ec)"
  ec="$(run_hook "$PFP" 'not json')"
  [ "$ec" = "2" ] && ok "pre-file-protect fails closed on malformed input (exit 2)" || bad "pre-file-protect should fail closed (got $ec)"
fi

# --- post-build-eval.sh (advisory, fail-open) ---------------------------------
PBE="post-build-eval.sh"
if [ -f "$HERE/$PBE" ]; then
  ec="$(run_hook "$PBE" '{"tool_name":"Bash","tool_input":{"command":"npm test"},"tool_response":{"exit_code":1}}' "$TMP")"
  [ "$ec" = "0" ] && ok "post-build-eval exits 0 on failing test (advisory)" || bad "post-build-eval should fail open on failing test (got $ec)"
  ec="$(run_hook "$PBE" '{"tool_name":"Read","tool_input":{"file_path":"x"}}' "$TMP")"
  [ "$ec" = "0" ] && ok "post-build-eval exits 0 for non-Bash tool" || bad "post-build-eval should exit 0 for non-Bash (got $ec)"
  ec="$(run_hook "$PBE" 'not json' "$TMP")"
  [ "$ec" = "0" ] && ok "post-build-eval fails open on malformed input (exit 0)" || bad "post-build-eval should fail open (got $ec)"
fi

# --- post-tool-log.sh (observability, fail-open; run in TMP to avoid polluting repo) ---
PTL="post-tool-log.sh"
if [ -f "$HERE/$PTL" ]; then
  ec="$(run_hook "$PTL" '{"tool_name":"Bash","matcher":"Bash","tool_response":{"exit_code":0}}' "$TMP")"
  [ "$ec" = "0" ] && ok "post-tool-log exits 0 on valid input" || bad "post-tool-log should exit 0 (got $ec)"
  if ls "$TMP"/.claude/logs/*-tools.jsonl >/dev/null 2>&1; then ok "post-tool-log wrote a JSONL log line"; else bad "post-tool-log should write a JSONL log"; fi
  ec="$(run_hook "$PTL" 'not json' "$TMP")"
  [ "$ec" = "0" ] && ok "post-tool-log fails open on malformed input (exit 0)" || bad "post-tool-log should fail open (got $ec)"
fi

# --- pre-compact-snapshot.sh (advisory, fail-open) ----------------------------
PCS="pre-compact-snapshot.sh"
if [ -f "$HERE/$PCS" ]; then
  ec="$(run_hook "$PCS" '{}' "$TMP")"
  [ "$ec" = "0" ] && ok "pre-compact-snapshot exits 0 (fail-open)" || bad "pre-compact-snapshot should exit 0 (got $ec)"
fi

# --- notification-notify-example.sh (advisory, fail-open) ---------------------
NOTIF="notification-notify-example.sh"
ec="$(run_hook "$NOTIF" '{"message":"build finished"}')"
[ "$ec" = "0" ] && ok "notification exits 0 on valid input" || bad "notification should exit 0 (got $ec)"
ec="$(run_hook "$NOTIF" 'not json')"
[ "$ec" = "0" ] && ok "notification fails open on malformed input (exit 0)" || bad "notification should fail open (got $ec)"

printf '  -> %d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
