#!/usr/bin/env bash
# The kit ships example hooks (examples/hooks/) as a copy-paste pattern for writing and
# self-testing Claude Code hooks safely. Lock that they exist, are valid bash, and that
# their block+pass self-test passes. Structural checks always run (bash-only); the
# jq-dependent behavioral run is gated on jq so the core suite stays dependency-light.
# On macOS CI this run also proves the hooks' BSD-grep portability for real.
# shellcheck source=scripts/test/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

HOOKS="$KIT_ROOT/examples/hooks"

assert_dir_exists "$HOOKS"
for h in pretooluse-block-example.sh notification-notify-example.sh test-hooks.sh; do
  assert_file_exists "$HOOKS/$h"
  assert_cmd_ok "bash -n '$HOOKS/$h'" "$h is syntactically valid bash"
done

if command -v jq >/dev/null 2>&1; then
  assert_cmd_ok "bash '$HOOKS/test-hooks.sh'" \
    "examples/hooks/test-hooks.sh passes (block + allow + fail-closed cases)"
else
  printf '  NOTE jq not installed — ran structural checks only, skipped behavioral self-test\n'
fi

t_summary
