#!/usr/bin/env bash
# =============================================================
# goal-loop.sh — the stop primitive for goal-directed loops.
#
# Claude Code ships a builtin `/goal` that keeps working across turns until a
# condition is met — but the model itself judges "met". This primitive adds the
# discipline that makes a loop trustworthy: a DETERMINISTIC success check and a
# hard attempt cap. It does not run the loop; it decides whether to STOP.
#
# Call it at the TOP of each work iteration and branch on the exit code:
#   exit 0 → ✅ GOAL MET    : the deterministic --check passed. Stop.
#   exit 1 → 🔁 CONTINUE    : not met, still under the cap. Do one more iteration.
#   exit 2 → 🛑 CAP REACHED : hit the attempt cap. Stop and escalate to a human.
#
# Usage:
#   goal-loop.sh --goal "<desc>" --check "<shell cmd>" --max <N> --state <file>
#     --check : deterministic command; exit 0 == goal met (coverage threshold,
#               green tests, lint=0, score >= T). NOT a self-reported judgement.
#     --max   : maximum attempts before CAP.
#     --state : where the attempt counter persists (default .goal-loop-state).
#   goal-loop.sh --reset --state <file>   # reset the counter
#
# Design:
#   - Deterministic: only the --check exit code is truth. The model never writes
#     "done" itself — that is the whole point (no self-report).
#   - Fail-safe: if --check itself errors (non-zero), that counts as "not met"
#     (CONTINUE/CAP), never as met.
#   - Dependencies: POSIX shell only, BSD/GNU grep safe.
# =============================================================
set -uo pipefail

GOAL=""; CHECK=""; MAX=5; STATE=".goal-loop-state"; RESET=0
while [ $# -gt 0 ]; do
  case "$1" in
    --goal)  GOAL="$2"; shift 2 ;;
    --check) CHECK="$2"; shift 2 ;;
    --max)   MAX="$2"; shift 2 ;;
    --state) STATE="$2"; shift 2 ;;
    --reset) RESET=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 3 ;;
  esac
done

if [ "$RESET" = "1" ]; then rm -f "$STATE"; echo "goal-loop: state reset ($STATE)"; exit 0; fi
[ -n "$CHECK" ] || { echo "goal-loop: --check is required" >&2; exit 3; }

# Deterministic goal test.
if eval "$CHECK" >/dev/null 2>&1; then
  rm -f "$STATE"
  echo "✅ GOAL MET: ${GOAL:-<goal>}  (--check passed; state reset)"
  exit 0
fi

# Not met → increment attempt counter.
attempt=$(cat "$STATE" 2>/dev/null || echo 0)
attempt=$((attempt + 1))
echo "$attempt" > "$STATE"

if [ "$attempt" -gt "$MAX" ]; then
  echo "🛑 CAP REACHED: ${GOAL:-<goal>} — attempt ${attempt} exceeded the cap (${MAX}). Stop and escalate."
  echo "   (a 'stop after N tries' attempt cap; prevents an infinite loop.)"
  exit 2
fi

echo "🔁 CONTINUE (attempt ${attempt}/${MAX}): ${GOAL:-<goal>}"
echo "   → --check not passed. Do ONE iteration of work, then run goal-loop again."
exit 1
