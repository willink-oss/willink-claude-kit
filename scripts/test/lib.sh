#!/usr/bin/env bash
# scripts/test/lib.sh — minimal, dependency-free assertion library for the kit
# regression / integrity suite. Each test_*.sh sources this, calls assert_* helpers,
# then ends with `t_summary` (its exit code becomes the test file's exit code).
#
# Portability: pure bash + coreutils + grep -F/-E (no grep -P, no GNU-only flags),
# so the same tests run on macOS (dev) and ubuntu-latest (CI).
set -uo pipefail

# Repo root, derived from this file's location (scripts/test/lib.sh -> ../..).
KIT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

_T_PASS=0
_T_FAIL=0
_t_ok()  { _T_PASS=$((_T_PASS + 1)); printf '  \033[32mPASS\033[0m %s\n' "$1"; }
_t_bad() { _T_FAIL=$((_T_FAIL + 1)); printf '  \033[31mFAIL\033[0m %s\n' "$1"; }

# assert_file_exists <path> [msg]
assert_file_exists() {
  if [ -f "$1" ]; then _t_ok "${2:-file exists: $1}"; else _t_bad "${2:-missing file: $1}"; fi
}

# assert_dir_exists <path> [msg]
assert_dir_exists() {
  if [ -d "$1" ]; then _t_ok "${2:-dir exists: $1}"; else _t_bad "${2:-missing dir: $1}"; fi
}

# assert_contains <file> <fixed-string> [msg] — substring match (grep -F, BSD/GNU safe)
assert_contains() {
  if grep -qF -- "$2" "$1" 2>/dev/null; then _t_ok "${3:-'$2' present in $(basename "$1")}"; else _t_bad "${3:-'$2' MISSING in $1}"; fi
}

# assert_not_contains <file> <fixed-string> [msg] — assert a substring is ABSENT.
# For locking out anti-patterns that must never reappear (e.g. a copy-pasteable docs
# snippet that silently disables the kit).
assert_not_contains() {
  if grep -qF -- "$2" "$1" 2>/dev/null; then _t_bad "${3:-'$2' MUST NOT appear in $1}"; else _t_ok "${3:-'$2' absent from $(basename "$1")}"; fi
}

# assert_grep <file> <ERE> [msg] — regex match (grep -E)
assert_grep() {
  if grep -qE -- "$2" "$1" 2>/dev/null; then _t_ok "${3:-/$2/ in $(basename "$1")}"; else _t_bad "${3:-/$2/ NOT in $1}"; fi
}

# assert_match <string> <ERE> [msg] — regex match against a literal string
assert_match() {
  if printf '%s' "$1" | grep -qE -- "$2" 2>/dev/null; then _t_ok "${3:-/$2/ matches}"; else _t_bad "${3:-/$2/ no match: '$1'}"; fi
}

# assert_eq <actual> <expected> [msg]
assert_eq() {
  if [ "$1" = "$2" ]; then _t_ok "${3:-eq: '$1'}"; else _t_bad "${3:-expected '$2' got '$1'}"; fi
}

# assert_cmd_ok "<command>" [msg] — eval a command, expect exit 0
assert_cmd_ok() {
  if eval "$1" >/dev/null 2>&1; then _t_ok "${2:-ok: $1}"; else _t_bad "${2:-FAILED (exit !=0): $1}"; fi
}

# t_summary — print per-file tally, return 0 iff every assertion passed.
t_summary() {
  printf '  -> %d passed, %d failed\n' "$_T_PASS" "$_T_FAIL"
  [ "$_T_FAIL" -eq 0 ]
}
