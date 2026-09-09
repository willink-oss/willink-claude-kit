#!/usr/bin/env bash
# _advisory-log.sh — advisory 発火を 1 行 JSON で記録する共有ライタ（M0 §6 T3 / 原則 P1 B3）
#
# なぜ共有にするか:
#   発火ログは post-commit-verify.sh に**インラインで 1 本だけ**入っていた。
#   そのため ①他の 6 本の advisory は一度も数えられておらず ②唯一の書き手が
#   `jq -nc ... || true` で、**jq が無い環境では黙って何も書かない**。
#   `advisory-fire-tally` skill は在るのに測る対象が無い、という状態が続いていた。
#   書き手を 1 本に集約し、jq 依存を外し、配線の有無を機械で見えるようにする。
#
# 使い方（呼ぶ側は fail-open のまま・戻り値を見ない）:
#   "$(dirname "$0")/_advisory-log.sh" <hook> <warn> [key=value ...]
#
#   例: "$(dirname "$0")/_advisory-log.sh" post-commit-verify "size:800行" \
#         commit_type=feat lines=812 files=9
#
# 出力先:
#   ADVISORY_LOG_FILE（テスト用の差し替え）> <hooks の 1 つ上>/logs/advisory-fires.jsonl
#   ログは .claude/logs/（gitignore 済・ローカル）。
#
# 配線の可視化（**ここが本ファイルの肝**）:
#   呼ばれた時点で必ずログファイルを作る（発火 0 件でも空ファイルが残る）。
#   これにより「ファイルが無い = 配線されていない（❓）」と
#   「ファイルは在るが 0 行 = 配線済みで発火 0（測れた 0）」を区別できる。
#   欠如をゼロ件と読まないための構造で、scripts/harness-loop-status.py がこれを見る。
#
# 失敗しても呼び出し元を止めない（advisory は fail-open が前提）。
set -u

_adv_log_file() {
  if [ -n "${ADVISORY_LOG_FILE:-}" ]; then
    printf '%s' "$ADVISORY_LOG_FILE"
    return
  fi
  local base
  base="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)" || return 1
  printf '%s/logs/advisory-fires.jsonl' "$base"
}

# JSON 文字列のエスケープ。jq に依存しない（単一 CLI 依存で黙って落ちるのを避ける）。
# " \ と制御文字だけを扱えば JSON として妥当。
_adv_json_escape() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\r'/\\r}"
  s="${s//$'\t'/\\t}"
  printf '%s' "$s"
}

# 数値なら裸で、それ以外は文字列として出す
_adv_json_value() {
  case "$1" in
    ''|*[!0-9-]*) printf '"%s"' "$(_adv_json_escape "$1")" ;;
    *)            printf '%s' "$1" ;;
  esac
}

advisory_log() {
  local hook="${1:-unknown}" warn="${2:-}"
  shift 2 2>/dev/null || true

  local file
  file="$(_adv_log_file)" || return 0
  mkdir -p "$(dirname "$file")" 2>/dev/null || return 0
  # **発火が無くてもファイルは作る**（配線済みの証拠を残す）
  : >>"$file" 2>/dev/null || return 0

  # warn が空 = 発火していない。配線の証拠だけ残して行は足さない。
  [ -z "$warn" ] && return 0

  local line
  line="{\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\""
  line="$line,\"hook\":\"$(_adv_json_escape "$hook")\""
  line="$line,\"warn\":\"$(_adv_json_escape "$warn")\""
  local kv k v
  for kv in "$@"; do
    case "$kv" in
      *=*) k="${kv%%=*}"; v="${kv#*=}" ;;
      *)   continue ;;
    esac
    [ -z "$k" ] && continue
    line="$line,\"$(_adv_json_escape "$k")\":$(_adv_json_value "$v")"
  done
  line="$line}"

  printf '%s\n' "$line" >>"$file" 2>/dev/null || true
  return 0
}

# 直接実行されたら引数で 1 回書いて終わる（source しても使える）
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  advisory_log "$@"
  exit 0
fi
