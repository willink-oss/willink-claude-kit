#!/usr/bin/env bash
# The kit ships example git pre-commit hooks (examples/git-hooks/) as a copy-paste pattern
# for blocking secrets / oversized files / BSD-incompatible shell before they reach history.
# Lock that they exist, are valid bash, and that their block+pass self-test passes. The
# behavioral run needs git (always present in CI); structural checks are bash-only.
# shellcheck source=scripts/test/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

GH="$KIT_ROOT/examples/git-hooks"

assert_dir_exists "$GH"
for f in pre-commit pre-commit-quality.sh pre-commit-shell-lint.sh test-git-hooks.sh README.md; do
  assert_file_exists "$GH/$f"
done
for f in pre-commit pre-commit-quality.sh pre-commit-shell-lint.sh test-git-hooks.sh; do
  assert_cmd_ok "bash -n '$GH/$f'" "$f is syntactically valid bash"
done

if command -v git >/dev/null 2>&1; then
  assert_cmd_ok "bash '$GH/test-git-hooks.sh'" \
    "examples/git-hooks/test-git-hooks.sh passes (block + pass cases)"
else
  printf '  NOTE git not installed — ran structural checks only, skipped behavioral self-test\n'
fi

t_summary
