#!/usr/bin/env bash
# The canonical /build flow (commands/build.md) carries the SAME failure-mode invariants
# that the 4 subagents enforce individually (Generator-Verifier separation, no early
# victory, no telephone game, no agent flooding). test_agent_guards.sh locks the guard
# phrases on the agent side; this file locks the equivalent invariants on the /build side
# so a silent weakening of the flow itself is caught too. Locked verbatim — any edit to
# the wording must be a deliberate, reviewed change.
# shellcheck source=scripts/test/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

BUILD="$KIT_ROOT/commands/build.md"

assert_file_exists "$BUILD"
assert_grep "$BUILD" '^description:' "build.md has frontmatter description:"

# Critical failure-mode guards (verbatim regression locks). If a refactor drops any of
# these, /build regresses to the exact failure modes the kit is designed to prevent.
assert_contains "$BUILD" 'subagent には委譲しない' \
  "/build keeps the no-telephone-game guard (Phase 3 implemented by main, not a subagent)"
assert_contains "$BUILD" 'dev-tester は full suite 完走必須' \
  "/build keeps the no-early-victory guard (dev-tester runs the full suite)"
assert_contains "$BUILD" 'Phase 3/5 を subagent 化しない' \
  "/build keeps the telephone-game guard in the failure-mode table"
assert_contains "$BUILD" '4 agent に厳選（追加禁止）' \
  "/build keeps the options-flooding guard (4 agents, no additions)"

t_summary
