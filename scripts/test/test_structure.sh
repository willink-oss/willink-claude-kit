#!/usr/bin/env bash
# Required files / directories exist. A missing adapter skill or command file breaks
# one of the three platforms (Claude / Codex / Antigravity) without any JSON error,
# so structural presence is its own regression class.
# shellcheck source=scripts/test/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# canonical plugin
assert_file_exists "$KIT_ROOT/.claude-plugin/plugin.json"
assert_file_exists "$KIT_ROOT/.claude-plugin/marketplace.json"
assert_file_exists "$KIT_ROOT/.codex-plugin/plugin.json"

# command + adapters
assert_file_exists "$KIT_ROOT/commands/build.md"
assert_file_exists "$KIT_ROOT/skills/codex-build/SKILL.md"
assert_file_exists "$KIT_ROOT/skills/antigravity-build/SKILL.md"

# the 4 subagents
for a in dev-explorer dev-planner dev-reviewer dev-tester; do
  assert_file_exists "$KIT_ROOT/agents/$a.md"
done

# tooling + docs
assert_file_exists "$KIT_ROOT/scripts/check_sync.py"
assert_file_exists "$KIT_ROOT/CHANGELOG.md"
assert_file_exists "$KIT_ROOT/README.md"
assert_file_exists "$KIT_ROOT/LICENSE"

t_summary
