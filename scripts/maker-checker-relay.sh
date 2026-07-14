#!/usr/bin/env bash
# =============================================================
# maker-checker-relay.sh — a goal-loop wrapper for Maker↔Checker relay.
#
# Separates the Generator (implementer = Maker) from the Verifier (a READ-ONLY
# reviewer = Checker — the kit's dev-reviewer agent, a /review session, or a
# human) and relays between them until BOTH "Maker's tests are green" AND
# "Checker has zero blocking findings". The truth of "done" lives in a
# deterministic gate's exit code — the implementer never self-declares "fixed".
#
#   stop primitive:  scripts/goal-loop.sh  (exit 0=MET / 1=CONTINUE / 2=CAP)
#   pass/fail gate (--check): this script's --gate (relay_gate below is the single source of truth)
#
# Modes:
#   --gate         : evaluate the pass/fail gate (this is what goal-loop's --check runs)
#                    exit 0 = pass (tests green AND zero Checker blockers) / exit 1 = fail
#   --print-check  : print the --check string to hand to goal-loop (wired to --gate)
#   --tick|--relay : advance the relay one step (calls goal-loop once, instructs Maker/Checker)
#   --reset        : reset goal-loop's attempt counter
#   --self-test    : verify the pass/fail truth table + wiring + syntax on hermetic fixtures
#
# Args:
#   --test <cmd>            Maker DoD (exit 0 = tests green). Required for gate/relay.
#   --review-out <file>     file the Checker writes findings to (default review.out)
#   --blocker-pattern <re>  line pattern marking one blocking finding (default BLOCKER; grep -E)
#   --goal <desc>           goal description (default "maker-checker relay")
#   --max <N>               attempt cap (default 3)
#   --state <file>          attempt counter file (default .goal-loop-maker-checker.state)
#
# Design: bash + goal-loop.sh + BSD/GNU grep safe (no grep -P). Mutates no git state.
# =============================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
GOAL_LOOP="$SCRIPT_DIR/goal-loop.sh"   # stop primitive = scripts/goal-loop.sh

TEST_CMD=""
REVIEW_OUT="review.out"
BLOCKER_PATTERN="BLOCKER"
GOAL="maker-checker relay"
MAX=3
STATE=".goal-loop-maker-checker.state"
MODE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --test)            TEST_CMD="$2"; shift 2 ;;
    --review-out)      REVIEW_OUT="$2"; shift 2 ;;
    --blocker-pattern) BLOCKER_PATTERN="$2"; shift 2 ;;
    --goal)            GOAL="$2"; shift 2 ;;
    --max)             MAX="$2"; shift 2 ;;
    --state)           STATE="$2"; shift 2 ;;
    --gate)            MODE="gate"; shift ;;
    --print-check)     MODE="print-check"; shift ;;
    --tick|--relay)    MODE="tick"; shift ;;
    --reset)           MODE="reset"; shift ;;
    --self-test)       MODE="self-test"; shift ;;
    *) echo "maker-checker-relay: unknown arg: $1" >&2; exit 3 ;;
  esac
done

# UTF-8-safe single-quote wrapping (embed values into the --check string safely).
shq() {
  printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

# ---- pass/fail logic (single source of truth; shared by --gate and self-test) ----
# relay_gate <test_cmd> <review_out> <blocker_pattern>
#   Maker DoD:   test_cmd exits 0 (green)
#   Checker DoD: review_out exists AND has 0 blocker_pattern lines
#   pass (exit 0) only when both hold; either missing → exit 1 (continue).
relay_gate() {
  gate_test="$1"; gate_review="$2"; gate_pattern="$3"
  # Maker: tests green? (unspecified = fail = continue)
  [ -n "$gate_test" ] || return 1
  eval "$gate_test" >/dev/null 2>&1 || return 1
  # Checker: not reviewed yet (no file) = fail = continue (never "complete" before review)
  [ -f "$gate_review" ] || return 1
  # count blocking findings. grep -c: 0=matched, 1=no match, >=2=ERROR (e.g. a malformed
  # --blocker-pattern regex). On error, FAIL CLOSED (treat as "not done") — a safety gate
  # must never pass because its own matcher broke.
  gate_n="$(grep -cE "$gate_pattern" "$gate_review" 2>/dev/null)"; grc=$?
  [ "$grc" -le 1 ] || return 1
  case "$gate_n" in ''|*[!0-9]*) return 1 ;; esac
  [ "$gate_n" -eq 0 ] || return 1
  return 0
}

case "$MODE" in
  gate)
    [ -n "$TEST_CMD" ] || { echo "maker-checker-relay: --gate requires --test" >&2; exit 3; }
    relay_gate "$TEST_CMD" "$REVIEW_OUT" "$BLOCKER_PATTERN"
    exit $?
    ;;

  print-check)
    [ -n "$TEST_CMD" ] || { echo "maker-checker-relay: --print-check requires --test" >&2; exit 3; }
    # a string goal-loop can use directly as --check (wired to --gate; no duplicated logic)
    printf 'bash %s --gate --test %s --review-out %s --blocker-pattern %s\n' \
      "$(shq "$SCRIPT_DIR/maker-checker-relay.sh")" \
      "$(shq "$TEST_CMD")" \
      "$(shq "$REVIEW_OUT")" \
      "$(shq "$BLOCKER_PATTERN")"
    exit 0
    ;;

  reset)
    "$GOAL_LOOP" --reset --state "$STATE"
    exit $?
    ;;

  tick)
    [ -n "$TEST_CMD" ] || { echo "maker-checker-relay: --tick requires --test" >&2; exit 3; }
    [ -x "$GOAL_LOOP" ] || { echo "maker-checker-relay: goal-loop.sh not found: $GOAL_LOOP" >&2; exit 3; }
    CHECK="bash $(shq "$SCRIPT_DIR/maker-checker-relay.sh") --gate --test $(shq "$TEST_CMD") --review-out $(shq "$REVIEW_OUT") --blocker-pattern $(shq "$BLOCKER_PATTERN")"
    # call goal-loop once (stop wiring: --reset/--check/--max/--state)
    "$GOAL_LOOP" --goal "$GOAL" --check "$CHECK" --max "$MAX" --state "$STATE"
    rc=$?
    case "$rc" in
      0)
        echo "✅ RELAY COMPLETE: tests green AND 0 '${BLOCKER_PATTERN}' findings (${REVIEW_OUT}). Commit/PR."
        ;;
      1)
        echo "🔁 RELAY CONTINUE — split Maker and Checker, advance one step:"
        echo "  1) Maker (implementer): do ONE increment toward green tests. Do NOT self-review."
        echo "     DoD: $TEST_CMD"
        echo "  2) Checker (read-only reviewer — e.g. the dev-reviewer agent, /review, or a human):"
        echo "     review the diff and write findings to $REVIEW_OUT; mark each blocking finding with"
        echo "     a line containing '$BLOCKER_PATTERN' (omit non-blocking nits)."
        echo "  3) Re-run: bash scripts/maker-checker-relay.sh --tick --test ... --review-out $REVIEW_OUT"
        ;;
      2)
        echo "🛑 CAP REACHED (over ${MAX}) — stop and escalate to a human. Record the unmet goal"
        echo "   (\"maker-checker relay unmet: ${GOAL}\") wherever you track blockers."
        ;;
      *)
        echo "maker-checker-relay: goal-loop.sh unexpected exit: $rc" >&2
        ;;
    esac
    exit "$rc"
    ;;

  self-test)
    fail=0
    tmp="$(mktemp -d "${TMPDIR:-/tmp}/mcr-selftest.XXXXXX")"
    trap 'rm -f "$tmp"/* 2>/dev/null; rmdir "$tmp" 2>/dev/null' EXIT

    self="$SCRIPT_DIR/maker-checker-relay.sh"

    # (1) syntax
    if ! bash -n "$self"; then
      echo "self-test: ❌ bash -n (syntax) failed" >&2; fail=1
    fi
    # (2) references the stop primitive scripts/goal-loop.sh
    if ! grep -q "goal-loop.sh" "$self"; then
      echo "self-test: ❌ does not reference goal-loop.sh" >&2; fail=1
    fi
    # (3) stop wiring (--reset/--check/--max/--state) present in the source
    for f in "\-\-reset" "\-\-check" "\-\-max" "\-\-state"; do
      grep -qE "$f" "$self" || { echo "self-test: ❌ stop wiring $f missing" >&2; fail=1; }
    done

    # (4) verify the pass/fail truth table on hermetic fixtures
    #     review-out fixture: clean = 0 findings / dirty = 2 BLOCKER lines
    clean="$tmp/clean.out"; dirty="$tmp/dirty.out"; missing="$tmp/none.out"
    printf 'reviewed: looks good, no blocking issues\nnit: rename var\n' > "$clean"
    printf 'BLOCKER: null deref at line 42\nnit: spacing\nBLOCKER: missing auth check\n' > "$dirty"
    # missing is intentionally not created

    assert_gate() { # <expected exit> <desc> <test_cmd> <review_out>
      exp="$1"; desc="$2"; tc="$3"; ro="$4"
      if relay_gate "$tc" "$ro" "BLOCKER"; then got=0; else got=1; fi
      if [ "$got" != "$exp" ]; then
        echo "self-test: ❌ truth table [$desc] expected=$exp got=$got" >&2; fail=1
      fi
    }
    # green × 0 findings          → pass (0)
    assert_gate 0 "green+clean"       "true"  "$clean"
    # green × 2 BLOCKERs          → fail (1) (Checker rejects)
    assert_gate 1 "green+blockers"    "true"  "$dirty"
    # red × 0 findings            → fail (1) (Maker not done)
    assert_gate 1 "red+clean"         "false" "$clean"
    # red × 2 BLOCKERs            → fail (1)
    assert_gate 1 "red+blockers"      "false" "$dirty"
    # green × no review (no file) → fail (1) (don't complete before review)
    assert_gate 1 "green+noreview"    "true"  "$missing"
    # no --test                   → fail (1)
    assert_gate 1 "no-test-cmd"       ""      "$clean"
    # malformed --blocker-pattern (invalid ERE) → FAIL CLOSED, not a silent pass
    if relay_gate "true" "$clean" "("; then _bp=0; else _bp=1; fi
    [ "$_bp" = "1" ] || { echo "self-test: ❌ truth table [malformed-pattern] should fail closed" >&2; fail=1; }

    # (5) --print-check output is eval-able and matches the truth table (end-to-end wiring)
    pc_green="$("$self" --print-check --test true --review-out "$clean" --blocker-pattern BLOCKER)"
    pc_block="$("$self" --print-check --test true --review-out "$dirty" --blocker-pattern BLOCKER)"
    if eval "$pc_green" >/dev/null 2>&1; then :; else
      echo "self-test: ❌ print-check(green+clean) did not pass" >&2; fail=1; fi
    if eval "$pc_block" >/dev/null 2>&1; then
      echo "self-test: ❌ print-check(green+blockers) wrongly passed" >&2; fail=1; fi

    if [ "$fail" -eq 0 ]; then
      echo "✅ self-test PASS: bash -n / goal-loop.sh ref / stop wiring (--reset/--check/--max/--state) /"
      echo "   pass-fail truth table 7 cases (incl. malformed-pattern fail-closed) / --print-check 2 cases all OK"
      exit 0
    else
      echo "❌ self-test FAIL" >&2
      exit 1
    fi
    ;;

  *)
    echo "maker-checker-relay: no mode. Use one of --gate / --print-check / --tick / --reset / --self-test" >&2
    exit 3
    ;;
esac
