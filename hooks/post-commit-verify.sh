#!/bin/bash
# =============================================================
# post-commit-verify.sh — PostToolUse hook for Bash (git commit のみ)
# Generator-Verifier パターン: 運用者 (Generator) がコミット生成後、
# commit type と diff size の整合性を明示的基準でチェック。
# 不整合があれば git reset HEAD^ を提案してループを回す。
#
# Exit: 常に 0 (fail-open・通知のみ)
# Noise: git commit 成功時のみ。基準未超過なら無音
# Portable: macOS BSD grep compatible (no -P, no \s)
# =============================================================
set -uo pipefail

# --- Dependency check ---
if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

# --- Read hook JSON from stdin ---
INPUT=$(cat)

TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)
COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
EXIT_CODE=$(printf '%s' "$INPUT" | jq -r '.tool_response.exit_code // .tool_response.exitCode // empty' 2>/dev/null)

# Not a Bash tool call → skip
[ "$TOOL_NAME" != "Bash" ] && exit 0

# Not a successful call → skip
[ "$EXIT_CODE" != "0" ] && exit 0

# Not a git commit → skip
GIT_COMMIT_PATTERN='(^|[[:space:]])git[[:space:]]+commit([[:space:]]|$)'
if ! printf '%s' "$COMMAND" | grep -qE "$GIT_COMMIT_PATTERN"; then
  exit 0
fi

# --- git hooksPath チェック (1 回限り警告・設定後は無音) ---
HOOKS_PATH=$(git config core.hooksPath 2>/dev/null || echo "")
if [ "$HOOKS_PATH" != ".githooks" ]; then
  cat >&2 <<EOF
================================================
 [post-commit-verify] git hooksPath 未設定
================================================
 core.hooksPath が '.githooks' に設定されていません
 現在の値: ${HOOKS_PATH:-"(未設定)"}
 commit-msg / pre-commit フックが動作していない可能性があります。
 対処: git config core.hooksPath .githooks
================================================
EOF
fi

# --- 直前コミットの type と diff stats 取得 ---
LAST_MSG=$(git log --format="%s" -1 2>/dev/null || true)
[ -z "$LAST_MSG" ] && exit 0

# commit type 抽出 (Conventional Commits: type(scope)!: ...)
COMMIT_TYPE=""
if printf '%s' "$LAST_MSG" | grep -qE '^[a-z]+(\([^)]+\))?!?:'; then
  COMMIT_TYPE=$(printf '%s' "$LAST_MSG" | grep -oE '^[a-z]+')
fi

# diff stats (HEAD^ が存在しない場合は初回コミット → スキップ)
DIFF_STAT=$(git diff HEAD^ HEAD --shortstat 2>/dev/null || true)
[ -z "$DIFF_STAT" ] && exit 0

CHANGED_LINES=$(printf '%s' "$DIFF_STAT" | awk '
  {ins=0; del=0;
   for(i=1;i<=NF;i++){
     if($(i+1)~/^insertion/) ins=$i;
     if($(i+1)~/^deletion/)  del=$i;
   }} END{print ins+del+0}')
FILE_COUNT=$(printf '%s' "$DIFF_STAT" | awk '{print $1+0}')

# --- Generator-Verifier: type-aware 閾値チェック ---
# これが「明示的基準」(explicit criteria) — 平坦な 500 行から type-aware に昇格
WARN=""
case "${COMMIT_TYPE:-unknown}" in
  docs|learn|ops|pm)
    if [ "${CHANGED_LINES:-0}" -gt 300 ] 2>/dev/null; then
      WARN="docs/ops 系コミットで 300 行超 (${CHANGED_LINES}行) — 分割を検討"
    fi
    ;;
  fix|chore|style|config)
    if [ "${FILE_COUNT:-0}" -gt 10 ] 2>/dev/null; then
      WARN="fix/chore 系コミットで ${FILE_COUNT} ファイル変更 — スコープが広すぎる可能性"
    elif [ "${CHANGED_LINES:-0}" -gt 200 ] 2>/dev/null; then
      WARN="fix 系コミットで 200 行超 (${CHANGED_LINES}行) — refactor か feat が適切かもしれない"
    fi
    ;;
  feat|refactor)
    if [ "${CHANGED_LINES:-0}" -gt 800 ] 2>/dev/null; then
      WARN="feat/refactor 系コミットで 800 行超 (${CHANGED_LINES}行) — 分割を検討"
    fi
    ;;
esac

[ -z "$WARN" ] && exit 0

# --- 原則 P1 B3: 発火ログ（昇格審査用・過検出率の観測） ---
# 月次 /harness-review が集計し「同種 true-positive 2 回以上 → H3(BLOCK) 昇格 /
# 過検出優勢 → 閾値調整」を判定する。ログは .claude/logs/（gitignore 済・ローカル）。
# テストは ADVISORY_LOG_FILE で出力先を差し替える。書込失敗は無視（fail-open 維持）。
# 書き手は _advisory-log.sh に集約した（jq 依存を外し、7 本すべてを同じ形で数える）
"$(dirname "$0")/_advisory-log.sh" post-commit-verify "$WARN" \
  "commit_type=${COMMIT_TYPE:-unknown}" "lines=${CHANGED_LINES:-0}" "files=${FILE_COUNT:-0}" || true

# --- Evaluator スタイル通知 ---
cat >&2 <<EOF
================================================
 Generator-Verifier (post-commit-verify.sh)
================================================
 Commit:  $LAST_MSG
 Type:    ${COMMIT_TYPE:-unknown}
 変更:    ${CHANGED_LINES:-?}行 / ${FILE_COUNT:-?}ファイル
 評価:    $WARN

 推奨アクション:
   git reset HEAD^ でコミットを取り消し、分割して再コミット
   例: feat: X と refactor: Y を別コミットに

 ※ fail-open: 通知のみ。コミットは維持されています
================================================
EOF

exit 0
