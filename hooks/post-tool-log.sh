#!/bin/bash
# =============================================================
# post-tool-log.sh — PostToolUse hook (all tools)
# Tool observability: 呼び出し系列を .claude/logs/YYYY-MM-DD-tools.jsonl に記録
# 月次ハーネスレビューで dead weight / 過剰呼び出しを検出する。
# Exit: 常に 0 (fail-open・通知のみ。流れを止めない)
# Portable: macOS BSD 互換 (no -P, no \s)
# =============================================================
set -uo pipefail

# --- Read hook JSON from stdin ---
INPUT=$(cat)

# --- ブランチ pin の記録（2026-09-04 追加・fail-open）---
#   「このセッションが作業していたブランチ」を session_id 単位で覚える。
#   pre-bash-safety.sh の Pattern 10 が commit 直前にこれと HEAD を突き合わせ、
#   並行セッションが共有 checkout を switch した場合にコミットを止める。
#   ⚠️ ここは jq 判定より前に置く（guard は python3 フォールバックを持つので、
#      jq が無い環境でも pin は記録できる。ログだけ諦める）。
_BPG="$(git rev-parse --show-toplevel 2>/dev/null)/scripts/branch-pin-guard.sh"
if [ -x "$_BPG" ]; then
  printf '%s' "$INPUT" | "$_BPG" --record >/dev/null 2>&1 || true
fi

# --- jq 不在時は通知スキップ (fail-open) ---
if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)
if [ -z "$TOOL_NAME" ]; then
  exit 0
fi

# --- Optional fields (failure mode tolerated) ---
MATCHER=$(printf '%s' "$INPUT" | jq -r '.matcher // empty' 2>/dev/null)
# session_id と対象ファイルは「このセッションで読んだか」を後段のガードが判定するのに要る。
# パスは秘密ではないが、内容は一切記録しない。
SESSION_ID=$(printf '%s' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
FILE_PATH=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
EXIT_CODE=$(printf '%s' "$INPUT" | jq -r '.tool_response.exit_code // .tool_response.exitCode // empty' 2>/dev/null)

# --- ブラウザ操作の「指し方」だけを記録する（2026-08-14 の公開事故の計測用） ---
#
#   note の編集画面「公開に進む」と、遷移先の公開設定画面「投稿する」が同一座標に
#   描かれていたため、座標クリック 1 回で遷移と公開が連続して起き、責任者 未承認のまま
#   記事が公開された。ref（要素参照）で押していれば起こらない事故だった。
#
#   ブロッキングゲートは tool_input だけでは書けない（フックはページの中身も URL も
#   見えないので、その座標に何があるか判定できない）。まず「座標で押したのか ref で
#   押したのか」を数えられるようにして、advisory→blocking 昇格の判断材料を作る。
#
#   記録するのは action と指し方のみ。座標値・入力テキスト・URL は記録しない。
ACTION=""
CLICK_MODE=""
case "$TOOL_NAME" in
  *claude-in-chrome__computer)
    ACTION=$(printf '%s' "$INPUT" | jq -r '.tool_input.action // empty' 2>/dev/null)
    HAS_REF=$(printf '%s' "$INPUT" | jq -r 'if (.tool_input.ref // "") == "" then "0" else "1" end' 2>/dev/null)
    HAS_COORD=$(printf '%s' "$INPUT" | jq -r 'if (.tool_input.coordinate // null) == null then "0" else "1" end' 2>/dev/null)
    if [ "$HAS_REF" = "1" ]; then
      CLICK_MODE="ref"
    elif [ "$HAS_COORD" = "1" ]; then
      CLICK_MODE="coordinate"
    fi
    ;;
esac

# --- 「live を測ったか」の署名だけを記録する（2026-08-22 追加） ---
#
#   本ログは 1 日 400 行超を記録しながら、コマンド本文も exit_code も入っておらず
#   （実測 2026-08-21: 402 行すべて exit_code=""）、「何を測ったか」を後から一切追えなかった。
#   Lab の公開状態を 13 日古い文書から誤報した回も、ログには「Bash を 308 回叩いた」
#   としか残っていない。観測している顔をして中身を測っていない状態だった。
#
#   ⚠️ コマンド本文は記録しない（台帳はコミットされるため）。**固定の署名 allowlist に
#   当たったかどうか**だけを残す。curl はホスト名までにし、パス・クエリは残さない
#   （個人情報を URL パラメータに置かない原則）。
MEASURE=""
MEASURE_HOST=""
if [ "$TOOL_NAME" = "Bash" ]; then
  CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
  if [ -n "$CMD" ]; then
    case "$CMD" in
      *"gh pr"*|*"gh run"*|*"gh api"*|*"gh issue"*|*"gh workflow"*|*"gh release"*|*"gh repo"*|*"gh search"*)
        MEASURE="gh" ;;
      *"aws "*)        MEASURE="aws" ;;
      *curl*)          MEASURE="curl" ;;
      *systemctl*)     MEASURE="systemctl" ;;
      *"git ls-files"*|*"git log"*|*"git cat-file"*|*"git rev-parse"*|*"git cherry"*|*"git branch"*)
        MEASURE="git" ;;
      *host-liveness-verdict*|*prod-content-check*|*x-post-gate*|*state-doc-freshness*|*repo-sync-check*|*content-gate-run*|*routine-sweep*)
        MEASURE="gate" ;;
    esac
    if [ "$MEASURE" = "curl" ]; then
      # スキーム付き URL のホスト部だけを取り出す（パス・クエリは捨てる）
      MEASURE_HOST=$(printf '%s' "$CMD" \
        | sed -n 's|.*https\{0,1\}://\([A-Za-z0-9._-]\{1,\}\).*|\1|p' | head -1)
    fi
  fi
elif [ "${TOOL_NAME#mcp__plugin_github_github__}" != "$TOOL_NAME" ]; then
  MEASURE="github-mcp"
elif [ "$TOOL_NAME" = "WebFetch" ]; then
  MEASURE="webfetch"
  MEASURE_HOST=$(printf '%s' "$INPUT" | jq -r '.tool_input.url // empty' 2>/dev/null \
    | sed -n 's|.*https\{0,1\}://\([A-Za-z0-9._-]\{1,\}\).*|\1|p' | head -1)
fi

# --- Locate repo root (fallback: cwd) ---
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
LOG_DIR="$REPO_ROOT/.claude/logs"
mkdir -p "$LOG_DIR" 2>/dev/null || exit 0

DATE=$(date +%Y-%m-%d)
TS=$(date +%Y-%m-%dT%H:%M:%S%z)
LOG_FILE="$LOG_DIR/$DATE-tools.jsonl"

# --- Emit JSON line (jq で anglely escape) ---
jq -nc \
  --arg ts "$TS" \
  --arg tool "$TOOL_NAME" \
  --arg matcher "$MATCHER" \
  --arg exit_code "$EXIT_CODE" \
  --arg session "$SESSION_ID" \
  --arg file "$FILE_PATH" \
  --arg action "$ACTION" \
  --arg click_mode "$CLICK_MODE" \
  --arg measure "$MEASURE" \
  --arg measure_host "$MEASURE_HOST" \
  '{ts:$ts, tool:$tool, matcher:$matcher, session:$session, file:$file}
   + (if $exit_code == "" then {} else {exit_code:$exit_code} end)
   + (if $action == "" then {} else {action:$action} end)
   + (if $click_mode == "" then {} else {click_mode:$click_mode} end)
   + (if $measure == "" then {} else {measure:$measure} end)
   + (if $measure_host == "" then {} else {measure_host:$measure_host} end)' \
  >> "$LOG_FILE" 2>/dev/null

exit 0
