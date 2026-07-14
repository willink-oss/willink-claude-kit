#!/usr/bin/env bash
# Locks the token-codegen-gate: the codegen-drift-check.sh backing script must exist, be
# valid bash, and its hermetic --self-test (check_target backup/generate/compare/restore +
# cfg_py extraction, non-destructive) must pass. If that truth table rots, the drift gate
# silently stops catching "artifact != source", so it is its own regression class.
# shellcheck source=scripts/test/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

S="$KIT_ROOT/scripts"

assert_file_exists "$S/codegen-drift-check.sh"
assert_cmd_ok "bash -n '$S/codegen-drift-check.sh'" "codegen-drift-check.sh is valid bash"
assert_cmd_ok "bash '$S/codegen-drift-check.sh' --self-test" "codegen-drift-check.sh --self-test passes"

# skill + example config ship alongside the script
assert_file_exists "$KIT_ROOT/skills/token-codegen-gate/SKILL.md"
assert_file_exists "$KIT_ROOT/examples/codegen-drift.config.example"

t_summary
