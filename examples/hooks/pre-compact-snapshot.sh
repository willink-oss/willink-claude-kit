#!/usr/bin/env bash
# pre-compact-snapshot.sh — PreCompact advisory, FAIL-OPEN.
#
# Shared-state pattern: just before /compact, persist the working state (git branch,
# recent commits, staged/dirty files) so the post-compaction session resumes cleanly.
# stdout is injected as additionalContext into the compaction summary; stderr is a
# one-line user-visible notice.
#
# Wire it up in .claude/settings.json:
#   "hooks": {
#     "PreCompact": [
#       { "hooks": [{ "type": "command", "command": ".claude/hooks/pre-compact-snapshot.sh" }] }
#     ]
#   }
#
# Contract (see docs/hooks-guide.md): PreCompact is FAIL-OPEN (exit 0 only). Fires only on
# /compact — roughly 0-2 times per session, so it is not noisy.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SNAPSHOT_FILE="$SCRIPT_DIR/.compact-snapshot"
NOW=$(date '+%Y-%m-%d %H:%M')

# git context (safe outside a repo)
BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
RECENT_COMMITS=$(git log --oneline -3 2>/dev/null || echo "(git log unavailable)")
STAGED=$(git diff --cached --name-only 2>/dev/null | head -5 || true)
DIRTY=$(git diff --name-only 2>/dev/null | head -5 || true)

# --- persistent snapshot (readable by the post-compaction session) ---
{
  printf '[compact-snapshot]\n'
  printf 'timestamp: %s\n' "$NOW"
  printf 'branch: %s\n' "$BRANCH"
  printf 'recent-commits:\n'
  printf '%s\n' "$RECENT_COMMITS" | sed 's/^/  /'
  if [ -n "$STAGED" ]; then printf 'staged-files:\n'; printf '%s\n' "$STAGED" | sed 's/^/  /'; fi
  if [ -n "$DIRTY" ];  then printf 'dirty-files:\n';  printf '%s\n' "$DIRTY"  | sed 's/^/  /'; fi
} > "$SNAPSHOT_FILE" 2>/dev/null || true

# --- stdout: injected as additionalContext into the /compact summary ---
printf '=== work state at compaction (%s) ===\n' "$NOW"
printf 'Branch: %s\n' "$BRANCH"
printf 'Recent commits:\n'
printf '%s\n' "$RECENT_COMMITS" | sed 's/^/  /'
if [ -n "$STAGED" ]; then printf 'Staged (uncommitted):\n'; printf '%s\n' "$STAGED" | sed 's/^/  /'; fi
if [ -n "$DIRTY" ];  then printf 'Dirty (modified, not staged):\n'; printf '%s\n' "$DIRTY" | sed 's/^/  /'; fi
printf 'Snapshot saved: %s\n' "$SNAPSHOT_FILE"
printf 'After compaction, read this file to resume work.\n'

# --- stderr: one-line user notice ---
printf '[pre-compact-snapshot] snapshot saved: branch=%s | %s\n' "$BRANCH" "$SNAPSHOT_FILE" >&2

exit 0
