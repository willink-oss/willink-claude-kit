#!/bin/bash
# =============================================================
# pre-compact-snapshot.sh — PreCompact hook
# Shared State パターン: /compact 直前に作業状態を永続ファイルに書き出し、
# 圧縮後セッションが文脈を引き継げるようにする。
#
# Fail semantics: fail-open (exit 0 のみ)
# Noise: /compact 発火時のみ。1 セッション = 0〜2 回
#
# stdout → additionalContext として /compact summary に注入される
# stderr → ユーザー可視の 1 行通知
# =============================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SNAPSHOT_FILE="$SCRIPT_DIR/.compact-snapshot"
NOW=$(date '+%Y-%m-%d %H:%M')

# git コンテキスト取得 (リポジトリ外でも安全に失敗)
BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
RECENT_COMMITS=$(git log --oneline -3 2>/dev/null || echo "(git log unavailable)")
STAGED=$(git diff --cached --name-only 2>/dev/null | head -5 || true)
DIRTY=$(git diff --name-only 2>/dev/null | head -5 || true)

# --- 永続スナップショット書き込み (Shared State) ---
# このファイルは compaction 後のセッションが読み取れる
{
  printf '[compact-snapshot]\n'
  printf 'timestamp: %s\n' "$NOW"
  printf 'branch: %s\n' "$BRANCH"
  printf 'recent-commits:\n'
  printf '%s\n' "$RECENT_COMMITS" | sed 's/^/  /'
  if [ -n "$STAGED" ]; then
    printf 'staged-files:\n'
    printf '%s\n' "$STAGED" | sed 's/^/  /'
  fi
  if [ -n "$DIRTY" ]; then
    printf 'dirty-files:\n'
    printf '%s\n' "$DIRTY" | sed 's/^/  /'
  fi
} > "$SNAPSHOT_FILE" 2>/dev/null || true

# --- stdout: additionalContext として /compact summary に注入 ---
printf '=== 運用者 work state at compaction (%s) ===\n' "$NOW"
printf 'Branch: %s\n' "$BRANCH"
printf 'Recent commits:\n'
printf '%s\n' "$RECENT_COMMITS" | sed 's/^/  /'
if [ -n "$STAGED" ]; then
  printf 'Staged (uncommitted):\n'
  printf '%s\n' "$STAGED" | sed 's/^/  /'
fi
if [ -n "$DIRTY" ]; then
  printf 'Dirty (modified, not staged):\n'
  printf '%s\n' "$DIRTY" | sed 's/^/  /'
fi
printf 'Snapshot saved: %s\n' "$SNAPSHOT_FILE"
printf 'After compaction, read .claude/hooks/.compact-snapshot to resume work.\n'

# --- stderr: ユーザー可視 1 行通知 ---
printf '[pre-compact-snapshot] スナップショット保存完了: branch=%s | %s\n' "$BRANCH" "$SNAPSHOT_FILE" >&2

exit 0
