#!/bin/bash
# =============================================================
# post-file-eval.sh — PostToolUse hook for Write / Edit tools
# Evaluator相当: ファイル書き込み後にコンテンツを検査して通知する。
# Exit: 常に 0 (fail-open・通知のみ。Generatorの流れを止めない)
# Portable: macOS BSD grep compatible (no -P, no \s)
#
# 検査1: CLAUDE.md が 180 行を超えたら警告 (file-management.md ルール)
# 検査2: 書き込みファイルにシークレットパターンを検出したら警告
#         (pre-commit-quality.sh と同じパターン・同じ allowlist pragma)
#
# 対象ツール: Write / Edit (Glob/Read/Bash は無視)
# =============================================================
set -uo pipefail

# --- Dependency check: jq 不在時は通知スキップ (fail-open) ---
if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

# --- Read hook JSON from stdin ---
INPUT=$(cat)

TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)
FILE_PATH=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)

# --- Only handle Write / Edit tool calls ---
case "$TOOL_NAME" in
  Write|Edit) ;;
  *) exit 0 ;;
esac

# --- File path must be parseable ---
if [ -z "$FILE_PATH" ]; then
  exit 0
fi

# --- File must exist (PostToolUse fires after write) ---
if [ ! -f "$FILE_PATH" ]; then
  exit 0
fi

BASENAME=$(basename "$FILE_PATH")

# =============================================================
# 検査 1: CLAUDE.md 行数チェック
# =============================================================
if [ "$BASENAME" = "CLAUDE.md" ]; then
  LINE_COUNT=$(wc -l < "$FILE_PATH" 2>/dev/null | tr -d '[:space:]')
  if [ -n "$LINE_COUNT" ] && [ "$LINE_COUNT" -gt 180 ] 2>/dev/null; then
    "$(dirname "$0")/_advisory-log.sh" post-file-eval "claude-md-too-long" "lines=${LINE_COUNT}" || true
    cat >&2 <<EOF
================================================
 [post-file-eval] CLAUDE.md 行数超過警告
================================================
 ファイル: $FILE_PATH
 現在行数: ${LINE_COUNT} 行 (上限: 180 行)

 CLAUDE.md が 200 行を超えると git pre-commit hook で BLOCK されます。
 詳細ルールは .claude/rules/ に分割することを推奨します。

 ※ fail-open: 書き込み自体は妨げません
================================================
EOF
  fi
fi

# =============================================================
# 検査 2: シークレットパターン検出
# (pre-commit-quality.sh と同じパターン・同じ pragma)
# =============================================================
# Skip binary files by extension
case "$BASENAME" in
  *.png|*.jpg|*.jpeg|*.gif|*.ico|*.woff|*.woff2|*.ttf|*.eot|*.pdf|*.zip) exit 0 ;;
esac

# Skip .env.example (keys only, no values by convention)
case "$BASENAME" in
  .env.example|.env.sample) exit 0 ;;
esac

SECRET_PATTERNS='(AKIA[0-9A-Z]{16}|(^|[^A-Za-z0-9_-])sk-[A-Za-z0-9_-]*[A-Za-z0-9_]{20,}|ghp_[a-zA-Z0-9]{36}|gho_[a-zA-Z0-9]{36}|ghs_[a-zA-Z0-9]{36}|github_pat_[a-zA-Z0-9_]{50,}|AIza[0-9A-Za-z_-]{35}|xox[baprs]-[0-9a-zA-Z-]{10,})'

RAW_MATCHES=$(grep -nE "$SECRET_PATTERNS" "$FILE_PATH" 2>/dev/null)
if [ -z "$RAW_MATCHES" ]; then
  exit 0
fi

# Filter out lines with allowlist pragma
REAL_MATCHES=""
while IFS= read -r match_line; do
  [ -z "$match_line" ] && continue
  if printf '%s' "$match_line" | grep -qF 'pragma: allowlist secret'; then
    continue
  fi
  if [ -z "$REAL_MATCHES" ]; then
    REAL_MATCHES="$match_line"
  else
    REAL_MATCHES="$REAL_MATCHES
$match_line"
  fi
done <<HEREDOC
$RAW_MATCHES
HEREDOC

if [ -n "$REAL_MATCHES" ]; then
  "$(dirname "$0")/_advisory-log.sh" post-file-eval "secret-suspected" || true
  cat >&2 <<WARN
================================================
 [post-file-eval] シークレット検出警告
================================================
 ファイル: $FILE_PATH

 以下の行にシークレットパターンが含まれている可能性があります:
$(printf '%s' "$REAL_MATCHES" | head -5 | sed 's/^/   /')

 対処方法:
   1. 実際のシークレット値なら → 環境変数 / AWS Secrets Manager に移動
   2. テスト用ダミー値なら → 行末に "# pragma: allowlist secret" を追加
   3. 誤検知なら → 上記 pragma で除外してください

 ※ fail-open: 書き込み自体は妨げません
================================================
WARN
fi

exit 0
