#!/usr/bin/env bash
# =============================================================
# pre-commit-shell-lint.sh — git pre-commit gate for staged shell scripts.
# Mechanizes two rules that are otherwise just docs (docs/hooks-guide.md §3):
#   1. BSD-incompatible grep (`grep -P`, or Perl escapes `\s` / `\x27` on a grep line)
#      -> BLOCK. Comment lines are excluded; add `# pragma: allowlist bsd-grep` to a
#         line to allow it individually (e.g. a message that mentions grep -P).
#   2. shellcheck --severity=error -> BLOCK.
#      If shellcheck is not installed, SKIP with a notice (a single-CLI dependency
#      must not become a hard SPOF that blocks every commit). `brew install shellcheck`.
#
# Scope: staged *.sh and .githooks/* files (checks the STAGED content via `git show :file`).
# Exit: 0 = PASS, 1 = BLOCK.
# Portability: POSIX ERE (grep -E) only — no grep -P, no \s.  # pragma: allowlist bsd-grep
# =============================================================
set -uo pipefail

STAGED=$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null || true)
[ -z "$STAGED" ] && exit 0

# shell scripts only (*.sh + .githooks/ top level)
SHELL_FILES=$(printf '%s\n' "$STAGED" | grep -E '\.sh$|^\.githooks/[^/]+$' || true)
[ -z "$SHELL_FILES" ] && exit 0

FAILED=0

# --- 1. BSD grep compatibility (deterministic, no external CLI) ---
while IFS= read -r f; do
  [ -z "$f" ] && continue
  CONTENT=$(git show ":$f" 2>/dev/null || true)
  [ -z "$CONTENT" ] && continue
  # scan real code only: drop comment lines and pragma-allowlisted lines
  CODE=$(printf '%s\n' "$CONTENT" \
    | grep -vE '^[[:space:]]*#' \
    | grep -v 'pragma: allowlist bsd-grep' || true)

  # grep -P (also combined flags like -qP). Word-boundary so pgrep -P is not a false hit.
  VIOLATION=$(printf '%s\n' "$CODE" | grep -nE '(^|[^a-zA-Z0-9_])grep[[:space:]]+(-[a-zA-Z]+[[:space:]]+)*-[a-zA-Z]*P' || true)
  if [ -n "$VIOLATION" ]; then
    echo "❌ [$f] BSD-incompatible: 'grep -P' does not work on macOS (use ERE + [[:space:]])" >&2  # pragma: allowlist bsd-grep
    printf '%s\n' "$VIOLATION" | head -3 >&2
    FAILED=1
  fi

  # Perl escapes \s / \x27 on a grep line.
  # (ERE '\\s' = literal backslash + s; '\\\\s' would require two backslashes and miss real \s.)
  VIOLATION=$(printf '%s\n' "$CODE" | grep -n 'grep' | grep -E '\\s|\\x27' || true)  # pragma: allowlist bsd-grep
  if [ -n "$VIOLATION" ]; then
    echo "❌ [$f] BSD-incompatible: Perl escape in a grep pattern (\\s -> [[:space:]])" >&2  # pragma: allowlist bsd-grep
    printf '%s\n' "$VIOLATION" | head -3 >&2
    FAILED=1
  fi
done <<EOF_FILES
$SHELL_FILES
EOF_FILES

# --- 2. shellcheck (error severity only; skip if not installed) ---
if command -v shellcheck >/dev/null 2>&1; then
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    TMP=$(mktemp "${TMPDIR:-/tmp}/shell-lint.XXXXXX")
    git show ":$f" > "$TMP" 2>/dev/null || { rm -f "$TMP"; continue; }
    if ! OUT=$(shellcheck --severity=error --shell=bash "$TMP" 2>&1); then
      echo "❌ [$f] shellcheck error:" >&2
      printf '%s\n' "$OUT" | grep -vE '^In /' | head -10 >&2
      FAILED=1
    fi
    rm -f "$TMP"
  done <<EOF_FILES2
$SHELL_FILES
EOF_FILES2
else
  echo "ℹ️  shellcheck not installed — skipping error-level check (brew install shellcheck; BSD-grep check still ran)" >&2
fi

exit $FAILED
