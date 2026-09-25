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

# a11y is a design-time decision, not a post-hoc audit. If these drop out of /build, the
# kit silently returns to "accessibility gets added later" — the failure mode that produced
# a whole app of unnamed controls.
assert_contains "$BUILD" 'a11y を計画段階で決める' \
  "/build keeps a11y in Phase 2 (designed, not retrofitted)"
assert_contains "$BUILD" 'scripts/a11y-static-check.py' \
  "/build keeps the a11y gate in Phase 4 verification"
assert_contains "$BUILD" 'a11y の後付け' \
  "/build keeps a11y-retrofit in the failure-mode list"

# Human-in-the-loop steering. /build has a person in the loop: a one-line plan before the first
# action and a short recap at the end, and no "shall I continue?" stops on steps that need no input.
assert_contains "$BUILD" '着手前に 1 行の計画' \
  "/build keeps the one-line plan before the first action"
assert_contains "$BUILD" '終わりに短いまとめ' \
  "/build keeps the short recap at the end"
assert_contains "$BUILD" 'Findings は merge を止める指摘だけ' \
  "/build Phase 4 keeps the blocking-findings-only reviewer contract"
# The landing point when a person interrupts an unattended /oneshot run. If these drop out, an
# interrupt either throws the worktree away or resumes the dialogue under the unattended
# "keep going" instruction — the two failure modes this section exists to prevent.
assert_contains "$BUILD" '/build --from oneshot/state.json' \
  "/build documents the --from oneshot/state.json entry point"
assert_contains "$BUILD" 'worktree も途中の commit も捨てず' \
  "/build resumes an interrupted oneshot without discarding the worktree or commits"
assert_contains "$BUILD" 'oneshot/STOP を置いたか確かめる' \
  "/build confirms the unattended run was stopped before touching the worktree"
assert_contains "$BUILD" '| implement / loop | Phase 3 |' \
  "/build maps the oneshot phase to the /build phase to resume from"
assert_contains "$BUILD" '無人用の常設指示は適用しない' \
  "/build does not carry the unattended standing instruction into the dialogue"
CODEX_BUILD="$KIT_ROOT/skills/codex-build/SKILL.md"
AG_BUILD="$KIT_ROOT/skills/antigravity-build/SKILL.md"
assert_contains "$CODEX_BUILD" '/build --from oneshot/state.json' \
  "codex-build adapter carries the --from oneshot/state.json resume section"
assert_contains "$AG_BUILD" '/build --from oneshot/state.json' \
  "antigravity-build adapter carries the --from oneshot/state.json resume section"

t_summary
