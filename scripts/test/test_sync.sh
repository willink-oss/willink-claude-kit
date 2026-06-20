#!/usr/bin/env bash
# Regression gate around the canonical sync / parity / release-integrity check.
# Wraps scripts/check_sync.py --check so adapter drift (Codex/Antigravity hashes,
# plugin parity, CHANGELOG <-> version-pin integrity) is caught by the suite too.
# shellcheck source=scripts/test/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

assert_file_exists "$KIT_ROOT/scripts/check_sync.py"
assert_cmd_ok "python3 '$KIT_ROOT/scripts/check_sync.py' --check" \
  "check_sync.py --check exits 0 (adapter sync + plugin parity + release integrity)"

t_summary
