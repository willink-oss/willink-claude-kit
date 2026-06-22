#!/usr/bin/env bash
# scripts/test/run.sh — kit regression / integrity test runner.
#
# Runs every scripts/test/test_*.sh, prints per-file + suite summary, and exits
# non-zero if any test file failed. This is the single entry point used by:
#   - developers locally:  bash scripts/test/run.sh
#   - CI (.github/workflows/test.yml)
#   - the autonomous test-quality loop (crew loop-test-cycle / CYCLE-PROMPT-test-kit)
#
# The suite locks the kit's *integrity invariants* (this is a prompt/plugin kit, not
# application code): adapter sync, plugin/marketplace manifests, agent guard phrases,
# repo structure, release version pins. New invariants are added as test_*.sh files
# by the test-quality loop over time (regression coverage grows monotonically).
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

total=0
failed=0
failed_files=""

shopt -s nullglob 2>/dev/null || true
for t in "$HERE"/test_*.sh; do
  [ -f "$t" ] || continue
  total=$((total + 1))
  name="$(basename "$t")"
  printf '\n=== %s ===\n' "$name"
  if bash "$t"; then
    :
  else
    failed=$((failed + 1))
    failed_files="$failed_files $name"
  fi
done

printf '\n========================================\n'
if [ "$total" -eq 0 ]; then
  printf 'SUITE: no test_*.sh files found under %s\n' "$HERE"
  exit 2
fi
printf 'SUITE: %d test file(s), %d failed\n' "$total" "$failed"
if [ "$failed" -ne 0 ]; then
  printf 'FAILED FILES:%s\n' "$failed_files"
  exit 1
fi
printf 'ALL GREEN\n'
