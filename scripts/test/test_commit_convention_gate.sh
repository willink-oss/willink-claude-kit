#!/usr/bin/env bash
# Locks the commit-convention-gate engine: it must exist, be valid bash, and its own
# hermetic self-test (the prefix / not_empty / has_why truth table) must pass. This gate
# turns the "no empty commit messages" rule into a deterministic check; if its truth table
# rots, empty/why-less commits slip through, so it is its own regression class.
# shellcheck source=scripts/test/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

S="$KIT_ROOT/scripts"

assert_file_exists "$S/commit-convention-check.sh"
assert_cmd_ok "bash -n '$S/commit-convention-check.sh'" "commit-convention-check.sh is valid bash"
assert_cmd_ok "bash '$S/commit-convention-check.sh' --self-test" \
  "commit-convention-check.sh --self-test passes (prefix / not_empty / has_why truth table)"

t_summary
