#!/usr/bin/env bash
# Locks the goal-loop autonomy core: the stop primitive's exit codes (0=MET / 1=CONTINUE /
# 2=CAP), and the generator + relay wrappers' own hermetic self-tests. These primitives are
# the kit's "no self-report" stop discipline — if their truth tables rot, the whole
# guarantee (a loop that terminates and only stops when a DETERMINISTIC check passes) is
# gone, so they are their own regression class.
# shellcheck source=scripts/test/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

S="$KIT_ROOT/scripts"

# --- presence + valid bash + command/skill files ---
for f in goal-loop.sh goal-loop-template.sh maker-checker-relay.sh; do
  assert_file_exists "$S/$f"
  assert_cmd_ok "bash -n '$S/$f'" "$f is syntactically valid bash"
done
assert_file_exists "$KIT_ROOT/commands/goal-loop.md"
assert_file_exists "$KIT_ROOT/skills/maker-checker-relay/SKILL.md"

# --- BSD-grep portability: no grep -P in actual code (exclude comments) ---
for f in goal-loop.sh goal-loop-template.sh maker-checker-relay.sh; do
  if grep -vE '^[[:space:]]*#' "$S/$f" | grep -qE 'grep[[:space:]]+(-[a-zA-Z]+[[:space:]]+)*-[a-zA-Z]*P'; then
    _t_bad "$f uses non-portable 'grep -P'"
  else
    _t_ok "$f has no 'grep -P' in code"
  fi
done

# --- goal-loop.sh exit-code contract (isolated temp state) ---
TMP="$(mktemp -d 2>/dev/null || mktemp -d -t goalloop)"
trap 'rm -rf "$TMP"' EXIT
ST="$TMP/gl.state"

run_gl() { bash "$S/goal-loop.sh" --goal demo --check "$1" --max 2 --state "$ST" >/dev/null 2>&1; printf '%s' "$?"; }
ec="$(run_gl true)";  assert_eq "$ec" "0" "goal-loop: --check pass → exit 0 (GOAL MET)"
ec="$(run_gl false)"; assert_eq "$ec" "1" "goal-loop: --check fail, attempt 1/2 → exit 1 (CONTINUE)"
ec="$(run_gl false)"; assert_eq "$ec" "1" "goal-loop: --check fail, attempt 2/2 → exit 1 (CONTINUE)"
ec="$(run_gl false)"; assert_eq "$ec" "2" "goal-loop: over cap → exit 2 (CAP REACHED)"
# a passing check resets the counter
ec="$(run_gl true)";  assert_eq "$ec" "0" "goal-loop: pass after cap → exit 0 (state reset)"

# --- the two wrappers' own hermetic self-tests (truth tables live inside them) ---
assert_cmd_ok "bash '$S/goal-loop-template.sh' --self-test" \
  "goal-loop-template.sh --self-test passes (generate → bash -n → wiring)"
assert_cmd_ok "bash '$S/maker-checker-relay.sh' --self-test" \
  "maker-checker-relay.sh --self-test passes (truth table 7 + print-check 2)"

# a hostile --name must be rejected (exit 3), not injected into the generated STATE path
ec="$(bash "$S/goal-loop-template.sh" --name 'x$(touch "$TMP/PWNED")y' --check true >/dev/null 2>&1; printf '%s' "$?")"
assert_eq "$ec" "3" "goal-loop-template rejects an unsafe --name"
assert_cmd_ok "[ ! -e '$TMP/PWNED' ]" "goal-loop-template did not execute the injected --name"

t_summary
