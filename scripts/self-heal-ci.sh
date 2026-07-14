#!/usr/bin/env bash
# =============================================================
# self-heal-ci.sh — a goal-loop wrapper that self-heals a red CI.
#
# When CI is red (the latest run's conclusion != success), it relays
# "fix → re-verify" until CI is green, BOUNDED by an attempt cap. The stop /
# counting discipline is delegated to scripts/goal-loop.sh — this wrapper only
# drives detection and the fix cycle, it never loops forever on its own.
#
# The goal test is a SINGLE deterministic point: the latest `gh run list -L1`
# conclusion == "success".
#   - success                                              → ✅ green (goal met, stop)
#   - failure/cancelled/timed_out/null(in-progress)/""(fetch failed)
#                                                          → not met (fail-safe: treated as red)
#
# Modes:
#   self-heal-ci.sh                 : run the self-heal loop (default)
#   self-heal-ci.sh --repo O/R      : target repository (defaults to the cwd repo)
#   self-heal-ci.sh --max N         : attempt cap (default 5)
#   self-heal-ci.sh --ci-check      : exit 0 if latest run is green, exit 1 if red
#                                     (this is the body of goal-loop's --check)
#   self-heal-ci.sh --self-test     : gh-independent deterministic self-test (exit 0 = PASS)
#
# Options:
#   --escalate-file <path>  : on CAP, append the escalation line here instead of stdout
#   --state <path>          : where the attempt counter persists (SELF_HEAL_STATE env also works)
#
# Design:
#   - Stop / counting is delegated entirely to goal-loop.sh (this wrapper drives
#     detection + repair only).
#   - DRY_RUN=1 or `claude` not on PATH → gate-only (detect once and exit; the caller
#     repairs and re-runs).
#   - Fail-safe: a failed gh fetch (empty output) is treated as "red (unknown)", never
#     as "0 runs / green" (empty output != zero).
#   - Dependencies: bash + gh (only at run time). --self-test is fully gh-independent.
# =============================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SELF="$SCRIPT_DIR/$(basename "$0")"
GOAL_LOOP="$SCRIPT_DIR/goal-loop.sh"   # stop primitive = scripts/goal-loop.sh

REPO=""
MAX="${SELF_HEAL_MAX:-5}"
MODE="run"
STATE="${SELF_HEAL_STATE:-.self-heal-ci.state}"
ESCALATE_FILE="${SELF_HEAL_ESCALATE_FILE:-}"   # empty = escalate to stdout
CYCLE_PROMPT="${SELF_HEAL_PROMPT:-${CLAUDE_PLUGIN_ROOT:-.}/skills/self-heal-ci/CYCLE-PROMPT.md}"
LOOP_MODEL="${LOOP_MODEL:-}"                    # empty = let `claude` pick its default model

# --------------------------------------------------------------
# conclusion decision logic (gh-independent; the core predicate the fixtures test)
#   Green only when the conclusion is exactly "success". Everything else
#   (failure/cancelled/timed_out/null=in-progress/""=fetch failed) is red.
# --------------------------------------------------------------
ci_conclusion_ok() {
  [ "${1:-}" = "success" ]
}

# Fetch the latest run's conclusion from gh (body of --ci-check / ci_check).
ci_latest_conclusion() {
  if [ -n "$REPO" ]; then
    gh run list --repo "$REPO" -L1 --json conclusion --jq '.[0].conclusion' 2>/dev/null
  else
    gh run list -L1 --json conclusion --jq '.[0].conclusion' 2>/dev/null
  fi
}

# CI green test (exit 0 = green / exit 1 = red). Called by goal-loop's --check.
ci_check() {
  local c
  c="$(ci_latest_conclusion)"
  ci_conclusion_ok "$c"
}

# --------------------------------------------------------------
# argument parsing
# --------------------------------------------------------------
while [ $# -gt 0 ]; do
  case "$1" in
    --repo)          REPO="${2:?--repo needs a value}"; shift 2 ;;
    --max)           MAX="${2:?--max needs a value}"; shift 2 ;;
    --state)         STATE="${2:?--state needs a value}"; shift 2 ;;
    --escalate-file) ESCALATE_FILE="${2:?--escalate-file needs a value}"; shift 2 ;;
    --ci-check)      MODE="ci-check"; shift ;;
    --self-test)     MODE="self-test"; shift ;;
    --run)           MODE="run"; shift ;;
    *) echo "self-heal-ci: unknown arg: $1" >&2; exit 3 ;;
  esac
done

# --------------------------------------------------------------
# --ci-check: the body of goal-loop's --check (green=0 / red=1)
# --------------------------------------------------------------
if [ "$MODE" = "ci-check" ]; then
  ci_check && exit 0 || exit 1
fi

# --------------------------------------------------------------
# escalation on CAP (attempt cap reached).
#   Default sink is stdout; --escalate-file / SELF_HEAL_ESCALATE_FILE appends
#   the line to a generic file instead. No project-specific path is hardcoded.
# --------------------------------------------------------------
escalate_cap() {
  local msg="🛑 [self-heal-ci $(date '+%Y-%m-%d %H:%M')] CI stayed red within the attempt cap (${MAX}) — self-heal stopped, human intervention needed (repo=${REPO:-cwd})"
  if [ -n "$ESCALATE_FILE" ]; then
    if printf -- '- %s\n' "$msg" >> "$ESCALATE_FILE" 2>/dev/null; then
      echo "→ escalation appended to: $ESCALATE_FILE"
    else
      echo "→ could not write $ESCALATE_FILE — escalation: $msg"
    fi
  else
    printf -- '- %s\n' "$msg"
  fi
}

# --------------------------------------------------------------
# one automated repair cycle (Claude headless, PR-only).
#   Never mutates git beyond opening a PR (no self-merge / tag / push to main —
#   that boundary is enforced by the CYCLE-PROMPT the fix agent reads).
# --------------------------------------------------------------
do_claude_fix() {
  local ts prompt to_bin
  ts="$(date +%Y%m%d-%H%M%S)"
  prompt="Read ${CYCLE_PROMPT} and follow it to heal the red CI of ${REPO:-this repository} for exactly ONE cycle (cycle=${ts}). One cycle = one fix. Stop at opening a feature branch + PR. Do NOT self-merge / push tags / push to main."
  to_bin="$(command -v gtimeout || command -v timeout || true)"
  if [ -n "$LOOP_MODEL" ]; then
    if [ -n "$to_bin" ]; then
      "$to_bin" 1800 claude -p "$prompt" --model "$LOOP_MODEL" --output-format text
    else
      claude -p "$prompt" --model "$LOOP_MODEL" --output-format text
    fi
  else
    if [ -n "$to_bin" ]; then
      "$to_bin" 1800 claude -p "$prompt" --output-format text
    else
      claude -p "$prompt" --output-format text
    fi
  fi
}

# --------------------------------------------------------------
# run (default): loop via goal-loop until green
# --------------------------------------------------------------
run_heal_loop() {
  if [ ! -x "$GOAL_LOOP" ]; then
    echo "self-heal-ci: goal-loop.sh not found / not executable: $GOAL_LOOP" >&2
    exit 3
  fi

  mkdir -p "$(dirname "$STATE")"
  local GOAL CHECK_CMD rc
  GOAL="CI is green (latest run conclusion=success)"
  CHECK_CMD="bash \"$SELF\" --ci-check${REPO:+ --repo \"$REPO\"}"

  # reset the attempt counter at the start of a new loop
  bash "$GOAL_LOOP" --reset --state "$STATE" >/dev/null 2>&1 || true

  while :; do
    bash "$GOAL_LOOP" --goal "$GOAL" --check "$CHECK_CMD" --max "$MAX" --state "$STATE"
    rc=$?
    case "$rc" in
      0) echo "self-heal-ci: ✅ CI green (repo=${REPO:-cwd})"; exit 0 ;;
      2) echo "self-heal-ci: 🛑 attempt cap reached — escalating"; escalate_cap; exit 2 ;;
      1)
        # CI red & under cap → try to repair. If we can't auto-repair, gate-only back to caller.
        if [ "${DRY_RUN:-0}" = "1" ] || ! command -v claude >/dev/null 2>&1; then
          echo "self-heal-ci: 🔁 CI red (gate-only: DRY_RUN or claude not on PATH) — repair externally and re-run"
          exit 1
        fi
        echo "self-heal-ci: 🔧 CI red — running one automated repair cycle"
        do_claude_fix || echo "self-heal-ci: (repair cycle rc=$? — will re-verify next round)"
        ;;
      *) echo "self-heal-ci: goal-loop unexpected rc=$rc" >&2; exit "$rc" ;;
    esac
  done
}

# =============================================================
# --self-test: fully gh-independent deterministic verification (no hardcoded pass)
#   1) bash -n (own syntax)
#   2) references goal-loop.sh / it exists (the delegated stop primitive)
#   3) verifies the ci_conclusion_ok predicate on fixtures (only "success" is green)
#   4) verifies the --ci-check path end-to-end with a fake gh (hermetic fixture)
# =============================================================
run_self_test() {
  local fail=0

  # 1) syntax
  if bash -n "$SELF" 2>/dev/null; then
    echo "ok   - bash -n (syntax OK)"
  else
    echo "FAIL - bash -n (syntax error)"; fail=1
  fi

  # 2) references goal-loop.sh + it exists
  if grep -q 'goal-loop.sh' "$SELF"; then
    echo "ok   - references goal-loop.sh"
  else
    echo "FAIL - no goal-loop.sh reference"; fail=1
  fi
  if [ -f "$GOAL_LOOP" ]; then
    echo "ok   - goal-loop.sh exists: $GOAL_LOOP"
  else
    echo "FAIL - goal-loop.sh missing: $GOAL_LOOP"; fail=1
  fi

  # 3) conclusion predicate fixture verification (only "success" is green)
  if ci_conclusion_ok "success"; then
    echo "ok   - ci_conclusion_ok success → green(0)"
  else
    echo "FAIL - ci_conclusion_ok success did not go green"; fail=1
  fi
  local bad
  for bad in failure cancelled timed_out startup_failure skipped neutral action_required null "" "SUCCESS"; do
    if ci_conclusion_ok "$bad"; then
      echo "FAIL - ci_conclusion_ok '$bad' wrongly judged green"; fail=1
    else
      echo "ok   - ci_conclusion_ok '${bad:-<empty>}' → red(1)"
    fi
  done

  # 4) --ci-check end-to-end with a fake gh (hermetic)
  local tmp; tmp="$(mktemp -d)"
  _mk_fake_gh() {
    # $1 = conclusion string to emit (may be empty)
    cat > "$tmp/gh" <<EOF
#!/bin/bash
# fake gh: emulate \`run list ... --jq '.[0].conclusion'\` with a fixed fixture
printf '%s\n' "$1"
EOF
    chmod +x "$tmp/gh"
  }

  _mk_fake_gh "success"
  if PATH="$tmp:$PATH" bash "$SELF" --ci-check; then
    echo "ok   - --ci-check (fake gh: success) → exit0"
  else
    echo "FAIL - --ci-check (fake gh: success) was not exit0"; fail=1
  fi

  _mk_fake_gh "failure"
  if PATH="$tmp:$PATH" bash "$SELF" --ci-check; then
    echo "FAIL - --ci-check (fake gh: failure) became exit0"; fail=1
  else
    echo "ok   - --ci-check (fake gh: failure) → exit1"
  fi

  _mk_fake_gh ""   # gh fetch failed / empty output → red (empty output != zero)
  if PATH="$tmp:$PATH" bash "$SELF" --ci-check; then
    echo "FAIL - --ci-check (fake gh: empty) wrongly judged green"; fail=1
  else
    echo "ok   - --ci-check (fake gh: empty=fetch failed) → exit1 (fail-safe)"
  fi

  rm -rf "$tmp"

  echo "---"
  if [ "$fail" = 0 ]; then
    echo "self-heal-ci self-test: ALL PASS"
    return 0
  else
    echo "self-heal-ci self-test: FAIL"
    return 1
  fi
}

if [ "$MODE" = "self-test" ]; then
  run_self_test
  exit $?
fi

# default: self-heal loop
run_heal_loop
