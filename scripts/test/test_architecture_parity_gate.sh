#!/usr/bin/env bash
# Locks the architecture-parity-gate: a config-driven, python-stdlib-only gate that
# detects dependency-direction and per-layer naming violations in a code tree. Its
# deterministic --self-test encodes the truth table (compliant tree -> 0 violations,
# violating tree -> 2 dependency + 2 naming, exclude, graceful bad-regex); if that rots,
# the gate silently stops catching real architecture drift, so it is its own regression class.
# shellcheck source=scripts/test/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

S="$KIT_ROOT/scripts"

assert_file_exists "$S/arch-parity-check.py"
assert_file_exists "$KIT_ROOT/skills/architecture-parity-gate/SKILL.md"
assert_file_exists "$KIT_ROOT/examples/arch-parity.config.example.json"

assert_cmd_ok "python3 -m py_compile '$S/arch-parity-check.py'" "arch-parity-check.py compiles"
assert_cmd_ok "python3 '$S/arch-parity-check.py' --self-test" "arch-parity-check.py --self-test passes"

# the shipped example config must be valid JSON so users can copy it straight in
assert_cmd_ok "python3 -c 'import json,sys; json.load(open(sys.argv[1]))' '$KIT_ROOT/examples/arch-parity.config.example.json'" \
  "example config is valid JSON"

t_summary
