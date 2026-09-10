#!/bin/bash
# =============================================================
# review-gate.sh — UserPromptSubmit hook（per-task 改善レビュー・原則 P1/020）
#
# 責任者 指示 2026-07-04:「各タスク実施時に hook で review が都度動くように。
# 一旦コスト度外視で運用し実測で戦略を練る」。
# 各タスク開始時に Wave1 の決定論ゲート（常駐 context 予算・ナレッジ索引 drift）を
# 実走し、red があれば additionalContext で Claude に是正を促す。毎回 jsonl に記録し、
# 発火頻度・所要 ms を後で実測して blocking 昇格 / 間引きを判断する。
#
# 設計判断（既存 pre-status-verify-guard.sh と同じ・2026-06-01 の設計を踏襲）:
#   - Stop hook は block(exit 2) でしか feedback できず過検出の罰が大きい → 不採用。
#   - UserPromptSubmit + additionalContext は fail-open advisory。over-fire しても
#     「数行の reminder が context に入るだけ」= 副作用ほぼゼロ・絶対にブロックしない。
#   - full suite（media-score / advisory-tally / rule-promotion / ci-required-audit）は
#     月次 /harness-review と SessionEnd に置き、per-task は fast な回帰ゲートのみ。
#
# Exit: 常に 0（fail-open・advisory only・タスク完了を絶対に妨げない）
# Portable: macOS BSD 互換（grep -P / date %N 不使用・時間計測は python3）
# =============================================================
set -uo pipefail

# --- kill-switch（安全弁・不具合時に即無効化できる） ---
[ "${REVIEW_GATE_DISABLED:-0}" = "1" ] && exit 0

# --- リポジトリルート解決（.claude/hooks/ の 2 つ上） ---
# 検査対象のリポジトリ（= 利用者のプロジェクト）を解決する。
#
# ⚠️ plugin として配ると `$0` は **plugin のキャッシュ配下**になる（2026-09-10 実測）。
#    `dirname "$0"/../..` を「リポジトリのルート」として使うと、設定もログも
#    plugin の中を指す。設定は永久に見つからず、ログは全プロジェクトで共有される。
#    兄弟ファイル（`_advisory-log.sh` 等）を引くのに `$0` を使うのは正しい。
#    **プロジェクトを指したいときだけ**これを使う。
_project_dir() {
  if [ -n "${CLAUDE_PROJECT_DIR:-}" ]; then printf '%s' "$CLAUDE_PROJECT_DIR"; return; fi
  git rev-parse --show-toplevel 2>/dev/null || pwd
}

ROOT="$(_project_dir)" || exit 0
[ -n "$ROOT" ] || exit 0

# --- sentinel: Wave1 が入った crew でのみ動く（他リポ/未整備は fail-open） ---
# ⚠️ **提示するゲート一覧は組織ごとに違う**ので設定から読む（2026-09-09 パラメータ化）。
#    ハードコードのまま配ると、受け取った側では存在しないスクリプトを案内することになる。
#    書式は 1 行 1 ゲートの TSV: <名前>\t<コマンド>\t<赤いときの案内>
#    `#` 始まりと空行は無視する。同梱の review-gates.example.tsv を参照。
GATES_FILE="${REVIEW_GATES_FILE:-$ROOT/.claude/review-gates.tsv}"
if [ ! -f "$GATES_FILE" ]; then
  # 黙って終わらない。設定が無いことを 1 行で出す（免除が静かに恒久化しないように）。
  printf '%s\n' "[review-gate] ゲート一覧が未設定のため何も検査していません: $GATES_FILE" >&2
  exit 0
fi

# --- 依存（fail-open） ---
command -v jq >/dev/null 2>&1 || exit 0

# --- stdin JSON を消費（UserPromptSubmit payload・prompt 長のみ記録に使う） ---
INPUT=""
[ -t 0 ] || INPUT=$(cat 2>/dev/null || true)
PROMPT_LEN=$(printf '%s' "$INPUT" | jq -r '(.prompt // "") | length' 2>/dev/null || echo 0)
[ -n "$PROMPT_LEN" ] || PROMPT_LEN=0

nowms() { python3 -c 'import time;print(int(time.time()*1000))' 2>/dev/null || echo 0; }

# --- ゲート定義: "名前|コマンド|red時の是正ヒント" ---
# 各ゲートは fast・read-only・低 false-positive の回帰ゲートに限る。
run_gate() {
  # $1=name $2=cmd ; グローバル: FINDINGS / GATE_JSON に追記
  local name="$1" cmd="$2" t0 t1 rc
  t0=$(nowms)
  eval "$cmd" >/dev/null 2>&1; rc=$?
  t1=$(nowms)
  local ms=$(( t1 - t0 )); [ "$ms" -ge 0 ] || ms=0
  GATE_JSON="${GATE_JSON:+$GATE_JSON,}$(jq -nc --arg n "$name" --argjson e "$rc" --argjson m "$ms" '{name:$n,exit:$e,ms:$m}')"
  if [ "$rc" -ne 0 ]; then
    local cause; cause=$(classify_red "$name")
    case "$cause" in
      local-stale)
        # 規約違反ではない。指摘ではなく同期の案内にする（毎回同じ是正を促さない）
        FINDINGS="${FINDINGS}${FINDINGS:+$'\n'}- ${name}: ⚠️ **ローカル checkout が古いだけ**（origin/main では緑）。是正不要 — 同期で消える: git pull --rebase origin main"
        ;;
      incoming)
        FINDINGS="${FINDINGS}${FINDINGS:+$'\n'}- ${name}: ${3:-red}（origin/main は緑 = **あなたの未コミット変更が原因**）"
        ;;
      behind-unverified)
        FINDINGS="${FINDINGS}${FINDINGS:+$'\n'}- ${name}: ${3:-red}（⚠️ checkout が origin/main より遅れています。ローカル固有の可能性 — 同期して再判定）"
        ;;
      *)
        FINDINGS="${FINDINGS}${FINDINGS:+$'\n'}- ${name}: ${3:-red}"
        ;;
    esac
    GATE_CAUSE="${GATE_CAUSE:+$GATE_CAUSE,}$(jq -nc --arg n "$name" --arg c "$cause" '{name:$n,cause:$c}')"
    FIND_N=$(( FIND_N + 1 ))
  fi
}

# --- red の原因分類（2026-08-18 追加） -------------------------------------
# なぜ必要か（実測）: 8/11〜8/17 の 239 プロンプトで budget ゲートの red は 0 件。
# ところが 8/18 は 13/13 = **100% で red** になった。原因は規約違反ではなく、
# 共有 checkout が origin/main より 26 コミット遅れていたこと。
# 「リポの状態」を測るべきゲートが「手元のツリー」を測っていたため、
# 直しようのない指摘が毎プロンプト出続けた（狼少年化の入口）。
#
# 分類:
#   real          … origin/main でも red = 本物の違反
#   local-stale   … origin は green で、当該ファイルにローカル変更も無い = 同期すれば消える
#   incoming      … origin は green だがローカル変更あり = これから入れようとしている違反
classify_red() {
  # $1=gate名 ; echo で分類を返す（判定不能なら unknown）
  local name="$1" behind dirty
  command -v git >/dev/null 2>&1 || { echo unknown; return; }
  git -C "$ROOT" rev-parse --verify --quiet origin/main >/dev/null 2>&1 || { echo unknown; return; }
  behind=$(git -C "$ROOT" rev-list --count HEAD..origin/main 2>/dev/null || echo 0)
  [ "${behind:-0}" -gt 0 ] || { echo real; return; }

  case "$name" in
    # ⚠️ 組織固有のゲート名で分岐する特別扱いは置かない（2026-09-09）。
    #    「このゲートは安く測り直せる」はリポごとに違うので、既定は下の * に落として
    #    遅れの事実だけを添える。安く測り直せるゲートが在るなら、この case を足す。
    *)
      # 個別の測り直しが高い gate は、遅れの事実だけを添えて判断を人に委ねる
      echo behind-unverified
      ;;
  esac
}

FINDINGS=""
GATE_JSON=""
GATE_CAUSE=""
FIND_N=0
SUITE_T0=$(nowms)

# 設定ファイルの各行を実行する。**ここにゲート名を書かない**。
while IFS=$'\t' read -r _g_name _g_cmd _g_hint; do
  case "$_g_name" in ''|\#*) continue ;; esac
  [ -z "$_g_cmd" ] && continue
  run_gate "$_g_name" "$_g_cmd" "$_g_hint"
done < "$GATES_FILE"

# --- finding があれば additionalContext で是正を促す（無ければ無音・context を汚さない）---
if [ -n "$FINDINGS" ]; then
  CTX="🔎 [review-gate] このタスク開始時点で以下の決定論ゲートが red（自己申告禁止・都度是正）:
${FINDINGS}
full suite（media-score / advisory-tally / rule-promotion / ci-required-audit）は /harness-review。ゲート = scripts/*・自己判定でなく --check の exit code で判断（原則 P1/020）。"
  "$(dirname "$0")/_advisory-log.sh" review-gate "red-gate" || true
  jq -nc --arg ctx "$CTX" \
    '{hookSpecificOutput:{hookEventName:"UserPromptSubmit",additionalContext:$ctx}}'
fi

exit 0
