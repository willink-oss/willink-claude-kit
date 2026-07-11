#!/usr/bin/env bash
# Locks the fanout-verify-synth gate: the "may we synthesize?" stop primitive for a
# fanned-out, adversarially verified claim set. Its value is the deterministic verdict truth
# table (all-verified→ADOPT / any unverified→HOLD / any refuted→CONFLICT, and a bare
# "verified" label with too few sources is demoted to held) — verified by its own hermetic
# --self-test plus end-to-end exit-code asserts here. If this rots, an unverified or refuted
# claim set could be declared synthesis-ready, so it is its own regression class.
# shellcheck source=scripts/test/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

S="$KIT_ROOT/scripts"
FVS="$S/fanout-verify-synth.sh"

# --- presence + valid bash + self-test ---
assert_file_exists "$FVS"
assert_cmd_ok "bash -n '$FVS'" "fanout-verify-synth.sh is valid bash"
assert_cmd_ok "bash '$FVS' --self-test" "fanout-verify-synth.sh --self-test passes"

# --- end-to-end exit-code contract on real fixtures (not just --self-test) ---
TMP="$(mktemp -d 2>/dev/null || mktemp -d -t fvs)"
trap 'rm -rf "$TMP"' EXIT

run_fvs() { bash "$FVS" --in "$1" ${2:+--min-sources "$2"} >/dev/null 2>&1; printf '%s' "$?"; }

# all verified with sources → ADOPT (0)
cat > "$TMP/adopt.json" <<'J'
{ "topic": "t", "claims": [
  {"id":"c1","status":"verified","sources":["https://a"]},
  {"id":"c2","status":"verified","sources":["https://b","https://c"]}
]}
J
assert_eq "$(run_fvs "$TMP/adopt.json")" "0" "all-verified-with-sources → exit 0 (ADOPT)"

# one unverified claim → HOLD (1)
cat > "$TMP/hold.json" <<'J'
{ "topic": "t", "claims": [
  {"id":"c1","status":"verified","sources":["https://a"]},
  {"id":"c2","status":"unverified","sources":[]}
]}
J
assert_eq "$(run_fvs "$TMP/hold.json")" "1" "one-unverified → exit 1 (HOLD)"

# one refuted claim → CONFLICT (2)
cat > "$TMP/conflict.json" <<'J'
{ "topic": "t", "claims": [
  {"id":"c1","status":"verified","sources":["https://a"]},
  {"id":"c2","status":"refuted","sources":["https://x"]}
]}
J
assert_eq "$(run_fvs "$TMP/conflict.json")" "2" "one-refuted → exit 2 (CONFLICT)"

# a bare "verified" label without evidence is demoted → HOLD (1), and --min-sources 0 relaxes it → ADOPT (0)
cat > "$TMP/nosource.json" <<'J'
{ "topic": "t", "claims": [ {"id":"c1","status":"verified","sources":[]} ] }
J
assert_eq "$(run_fvs "$TMP/nosource.json")" "1" "verified-but-no-source → exit 1 (demoted to HOLD)"
assert_eq "$(run_fvs "$TMP/nosource.json" 0)" "0" "--min-sources 0 relaxes the same input → exit 0 (ADOPT)"

# a value-less trailing --in / --min-sources must fail-safe to exit 3, never hang the
# parser (a `... && synthesize` caller would otherwise stall forever). Bounded so a
# regression to the hang cannot stall CI: if it has to be killed, rc is 137 != 3 → FAIL.
bounded_run() { # <seconds> <args...> -> prints the exit code (137 if it had to be killed)
  local secs="$1"; shift
  bash "$FVS" "$@" >/dev/null 2>&1 & local p=$!
  ( sleep "$secs"; kill -9 "$p" 2>/dev/null ) & local k=$!
  wait "$p" 2>/dev/null; local rc=$?
  kill "$k" 2>/dev/null; wait "$k" 2>/dev/null
  printf '%s' "$rc"
}
assert_eq "$(bounded_run 5 --in "$TMP/adopt.json" --min-sources)" "3" "trailing value-less --min-sources → exit 3 (no hang)"
assert_eq "$(bounded_run 5 --min-sources 1 --in)" "3" "trailing value-less --in → exit 3 (no hang)"

t_summary
