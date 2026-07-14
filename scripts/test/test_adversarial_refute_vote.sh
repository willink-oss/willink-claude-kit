#!/usr/bin/env bash
# Locks the adversarial-refute-vote gate: the refute-vote.py engine must exist, compile, and
# its hermetic --self-test (8-case decision truth table) must pass. The value here is the
# strict-majority + fail-safe contract -- a majority refutation must STOP adoption (exit 1),
# and zero valid votes must ABSTAIN (exit 2), never silently fall through to ADOPT (exit 0).
# If that exit-code contract rots, the gate would let a refuted claim through, so it is its
# own regression class. We do NOT rely on --self-test alone (that would be a rubber-stamp):
# the three end-to-end asserts below drive real fixture files through the real CLI and check
# the exit code the caller would branch on.
# shellcheck source=scripts/test/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

S="$KIT_ROOT/scripts"
ENGINE="$S/refute-vote.py"

# --- presence + syntax + hermetic self-test ---
assert_file_exists "$ENGINE"
assert_cmd_ok "python3 -m py_compile '$ENGINE'" "refute-vote.py compiles (py_compile)"
assert_cmd_ok "python3 '$ENGINE' --self-test" "refute-vote.py --self-test passes (8-case truth table)"

# skill ships alongside the engine
assert_file_exists "$KIT_ROOT/skills/adversarial-refute-vote/SKILL.md"

# --- end-to-end exit-code contract on inline fixtures (isolated temp dir) ---
TMP="$(mktemp -d 2>/dev/null || mktemp -d -t refutevote)"
trap 'rm -rf "$TMP"' EXIT

run_vote() { python3 "$ENGINE" --votes "$1" >/dev/null 2>&1; printf '%s' "$?"; }

# majority refuted (2 of 3) -> STOP -> exit 1
printf '%s' '[{"refuted":true},{"refuted":true},{"refuted":false}]' > "$TMP/majority.json"
ec="$(run_vote "$TMP/majority.json")"
assert_eq "$ec" "1" "majority-refuted votes -> exit 1 (stop)"

# minority refuted (1 of 3) -> ADOPT -> exit 0
printf '%s' '[{"refuted":false},{"refuted":false},{"refuted":true}]' > "$TMP/minority.json"
ec="$(run_vote "$TMP/minority.json")"
assert_eq "$ec" "0" "minority-refuted votes -> exit 0 (adopt)"

# no valid refuted:bool -> ABSTAIN -> exit 2 (never falls to adopt)
printf '%s' '[{"reason":"no verdict"},{"comment":"n/a"}]' > "$TMP/novote.json"
ec="$(run_vote "$TMP/novote.json")"
assert_eq "$ec" "2" "no valid votes -> exit 2 (abstain, not adopt)"

t_summary
