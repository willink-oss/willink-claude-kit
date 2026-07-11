#!/usr/bin/env bash
# Locks the self-heal-ci gate: the goal-loop wrapper that drives a red CI to green under an
# attempt cap. Its value is the deterministic conclusion truth table (only gh conclusion=success
# is green; empty/other is red, fail-safe) — verified gh-independently by its own --self-test on
# a hermetic fake gh. If that rots, "self-heal" could declare a red CI green, so it is its own
# regression class.
# shellcheck source=scripts/test/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

S="$KIT_ROOT/scripts"

assert_file_exists "$S/self-heal-ci.sh"
assert_cmd_ok "bash -n '$S/self-heal-ci.sh'" "self-heal-ci.sh is valid bash"
assert_cmd_ok "bash '$S/self-heal-ci.sh' --self-test" "self-heal-ci.sh --self-test passes"

t_summary
