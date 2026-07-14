#!/usr/bin/env bash
# Locks the judge-rubric-vote gate: aggregate N judge votes into a majority verdict that only
# stands when agreement clears a threshold. Its whole value is that a split panel FAILS (hung)
# and that "no votes" is observe=exit2, NOT a fail-open exit0 — if that truth table rots, a
# `&&` caller could read a missing votes file or a split panel as consensus and adopt a verdict
# that was never agreed. So the exit-code mapping (pass=0 / hung=1 / observe=2) is its own
# regression class and is verified end-to-end here, not just via --self-test.
# shellcheck source=scripts/test/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

S="$KIT_ROOT/scripts"
J="$S/judge-vote.py"

assert_file_exists "$J"
assert_cmd_ok "python3 -m py_compile '$J'" "judge-vote.py compiles"
assert_cmd_ok "python3 '$J' --self-test" "judge-vote.py --self-test passes"

# --- end-to-end exit-code asserts on hermetic mktemp fixtures ---------------
TMP="$(mktemp -d "${TMPDIR:-/tmp}/judge-vote-test.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

printf '%s\n' '[{"verdict":"pass","score":0.9},{"verdict":"pass","score":0.8},{"verdict":"pass","score":0.95}]' > "$TMP/unanimous.json"
printf '%s\n' '[{"verdict":"pass","score":0.6},{"verdict":"fail","score":0.4},{"verdict":"hold","score":0.5}]' > "$TMP/split.json"

# unanimous -> pass -> exit 0
python3 "$J" --votes "$TMP/unanimous.json" >/dev/null 2>&1
assert_eq "$?" "0" "unanimous votes exit 0 (pass)"

# split panel -> hung -> exit 1
python3 "$J" --votes "$TMP/split.json" >/dev/null 2>&1
assert_eq "$?" "1" "split votes exit 1 (hung/fail)"

# missing file -> observe -> exit 2 (fail-closed, NOT a fail-open 0)
python3 "$J" --votes "$TMP/does-not-exist.json" >/dev/null 2>&1
assert_eq "$?" "2" "missing votes file exit 2 (observe)"

# threshold is live: same 2-of-3 votes pass at 0.66 but fail at 0.70
printf '%s\n' '[{"verdict":"pass","score":0.8},{"verdict":"pass","score":0.7},{"verdict":"fail","score":0.3}]' > "$TMP/straddle.json"
python3 "$J" --votes "$TMP/straddle.json" --agree 0.66 >/dev/null 2>&1
assert_eq "$?" "0" "2-of-3 at --agree 0.66 exit 0 (pass)"
python3 "$J" --votes "$TMP/straddle.json" --agree 0.70 >/dev/null 2>&1
assert_eq "$?" "1" "2-of-3 at --agree 0.70 exit 1 (fail)"

# a non-finite score (Infinity/NaN) must not leak into --json output: those are not
# RFC-8259 tokens and jq / strict JSON.parse reject them. It is dropped like a missing score.
printf '%s\n' '[{"verdict":"pass","score":Infinity},{"verdict":"pass","score":0.5}]' > "$TMP/nonfinite.json"
python3 "$J" --votes "$TMP/nonfinite.json" --json > "$TMP/nonfinite.out" 2>/dev/null
assert_cmd_ok "! grep -qE 'Infinity|NaN' '$TMP/nonfinite.out'" "non-finite score dropped from --json (no Infinity/NaN token)"

t_summary
