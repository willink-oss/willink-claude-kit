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

# command + canonical/adapter skills
assert_file_exists "$KIT_ROOT/commands/build.md"
assert_file_exists "$KIT_ROOT/skills/dev-standards/SKILL.md"
assert_file_exists "$KIT_ROOT/skills/codex-build/SKILL.md"
assert_file_exists "$KIT_ROOT/skills/antigravity-build/SKILL.md"

# a11y: the design-time contract, the deterministic gate, and the runtime templates.
# All three are referenced from dev-standards / build.md / the agents, so a missing one
# turns the a11y wiring into dangling references.
assert_file_exists "$KIT_ROOT/skills/a11y-standards/SKILL.md"
assert_file_exists "$KIT_ROOT/skills/a11y-static-gate/SKILL.md"
assert_file_exists "$KIT_ROOT/scripts/a11y-static-check.py"
assert_file_exists "$KIT_ROOT/examples/a11y/flutter/a11y_smoke_test.dart"
assert_file_exists "$KIT_ROOT/examples/a11y/web/eslint-a11y.config.md"
assert_file_exists "$KIT_ROOT/docs/a11y-guide.md"

# downstream extension scaffold (consumers copy this into project-standards/)
assert_file_exists "$KIT_ROOT/examples/project-standards-template/SKILL.md"

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
