#!/bin/bash
# =============================================================
# pre-status-verify-guard.sh — UserPromptSubmit hook
# 状態報告 / 進捗 / PR 状態 / 死活を要求するプロンプトを検出したら、
# live-state 実測チェックリストを additionalContext として Claude に注入する。
# 「文書ベースの状態推定」ミス (common-mistakes.md・8 例) の再発防止。
#
# 設計判断 (2026-06-01):
#   - Stop hook (post-message PR-state check) は block(exit 2) でしか model に
#     feedback できず、過検出でワークフローを止める risk が高い → 不採用。
#   - UserPromptSubmit + additionalContext は fail-open の advisory で、
#     over-fire しても「数行の reminder が context に入るだけ」= 副作用ほぼゼロ。
#     失敗が起きる瞬間 (= 状態を聞かれた時) に正確に発火する。
#   - 詳細: assets/knowledge/2026-06-01-doc-state-estimation-hook-design.md
#
# Exit: 常に 0 (fail-open, advisory only — 絶対にブロックしない)
# Portable: macOS BSD grep ERE 互換 (-P / Perl エスケープ不使用)
# 参考: .claude/rules/common-mistakes.md「文書ベースの状態推定」
# =============================================================
set -uo pipefail

# --- Dependency check (fail-open) ---
if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

# --- Read hook JSON from stdin ---
INPUT=$(cat)
PROMPT=$(printf '%s' "$INPUT" | jq -r '.prompt // empty' 2>/dev/null)

# 空プロンプトはスキップ
[ -z "$PROMPT" ] && exit 0

# --- 状態報告 / 進捗 / PR 状態 / 死活を求めるプロンプトのキーワード (BSD ERE 互換) ---
# additive-context のみ・block しないため、broad に倒しても副作用は小さい。
STATUS_KEYWORDS='standup|スタンドアップ|朝会|週次報告|状況|状態|ステータス|進捗|どうなって|どうなった|残り|残タスク|残作業|残時間|ブロッカー|blocker|稼働|死活|uptime|health|リリース状態|デプロイ済|デプロイ状態|マージ済|マージ対象|マージ可能|マージ待ち|レビュー待ち|review 待ち|open PR|PR 一覧|PR の状態|報告して|レポート|完了した|完了して|done\?|公開済|公開状態|非公開|メンバーシップ|有料|価格|料金|募集|プラン|会員|購読者|フォロワー|在庫|残数|承認済|配信済|投稿済|下書き|回って|動いて|生きて'

if printf '%s' "$PROMPT" | grep -qiE "$STATUS_KEYWORDS"; then
  REMINDER='[pre-status-verify-guard] 状態報告 / 進捗 / PR 状態 / 死活を出力する前に live-state を実測すること（文書は plan・live が state）:
- PR 状態: `gh pr view <N> --json state,mergedAt`（standup 等の記録を別メッセージへ転載・再パッケージする時も「再」実測する。「実測したことがある」≠「実測した」）
- サービス / 資産: `curl -o /dev/null -s -w "%{http_code}"`（favicon / Stripe / SNS / LP / pm）
- 外部システム障害仮説: `gh api` / `aws` CLI を 1 度叩いてから記録（[hypothesis]→[evidence]→[verdict] の 3 ブロック）。「動いていない」≠「壊れている」
- config / routine / active 表系の文書: Read は limit 指定なしで全件読む（末尾・中盤の状態表を見落とさない）
詳細: .claude/rules/common-mistakes.md「文書ベースの状態推定」(8 例の再発防止)'
  "$(dirname "$0")/_advisory-log.sh" pre-status-verify-guard "live-state-required" || true
  # additionalContext は jq で構築 → JSON 妥当性を保証 (format 起因の握りつぶし防止)
  jq -nc --arg ctx "$REMINDER" \
    '{hookSpecificOutput:{hookEventName:"UserPromptSubmit",additionalContext:$ctx}}'
fi

exit 0
