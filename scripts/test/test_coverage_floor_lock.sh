#!/usr/bin/env bash
# Locks the coverage-floor-lock gate: the backing script must exist, be valid bash, and its
# own hermetic self-test (below-floor / floor-lowering / per-format parsing truth table) must
# pass. The floor-lowering detection is the anti-gaming value here — if that truth table rots,
# a quietly-lowered floor would slip through, so it is its own regression class.
# shellcheck source=scripts/test/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

S="$KIT_ROOT/scripts"

assert_file_exists "$S/coverage-floor-check.sh"
assert_cmd_ok "bash -n '$S/coverage-floor-check.sh'" "coverage-floor-check.sh is valid bash"
assert_cmd_ok "bash '$S/coverage-floor-check.sh' --self-test" "coverage-floor-check.sh --self-test passes"

# skill + example config present
assert_file_exists "$KIT_ROOT/skills/coverage-floor-lock/SKILL.md"
assert_file_exists "$KIT_ROOT/examples/coverage-floor/coverage-floor.json"

t_summary
