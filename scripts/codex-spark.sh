#!/usr/bin/env bash
# codex-spark.sh — bounded workを GPT-5.3-Codex-Spark の独立 usage laneへ委譲する。
#
# Usage:
#   scripts/codex-spark.sh [--read-only|--write] [--cd DIR] -- "task"
#   printf '%s' "task" | scripts/codex-spark.sh [--read-only|--write]
#   scripts/codex-spark.sh --self-test
#
# Default:
#   --read-only. --write explicitly selects the workspace-write sandbox.
#
# Exit codes:
#   0  = codex exec completed
#   64 = invalid arguments / not a Git worktree
#   65 = nested Spark delegation refused
#   69 = codex CLI unavailable

set -euo pipefail

SPARK_MODEL="gpt-5.3-codex-spark"
SPARK_EFFORT="low"
SANDBOX="read-only"
WORKDIR="$PWD"
PROMPT=""
SCRIPT_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/$(basename "${BASH_SOURCE[0]}")"

usage() {
  sed -n '2,18p' "$0"
}

die_usage() {
  echo "❌ $*" >&2
  usage >&2
  exit 64
}

run_self_test() {
  local out rc

  out=$(CODEX_SPARK_DRY_RUN=1 "$SCRIPT_PATH" --cd "$PWD" -- "bounded read-only task")
  [[ "$out" == *"model=${SPARK_MODEL}"* ]] || { echo "self-test: model mismatch" >&2; return 1; }
  [[ "$out" == *"effort=${SPARK_EFFORT}"* ]] || { echo "self-test: effort mismatch" >&2; return 1; }
  [[ "$out" == *"sandbox=read-only"* ]] || { echo "self-test: read-only mismatch" >&2; return 1; }
  [[ "$out" == *"prompt=present"* ]] || { echo "self-test: prompt missing" >&2; return 1; }

  out=$(CODEX_SPARK_DRY_RUN=1 "$SCRIPT_PATH" --write --cd "$PWD" -- "bounded write task")
  [[ "$out" == *"sandbox=workspace-write"* ]] || { echo "self-test: write mismatch" >&2; return 1; }

  rc=0
  CODEX_SPARK_ACTIVE=1 CODEX_SPARK_DRY_RUN=1 "$SCRIPT_PATH" -- "nested task" >/dev/null 2>&1 || rc=$?
  [[ "$rc" -eq 65 ]] || { echo "self-test: recursion guard mismatch (rc=$rc)" >&2; return 1; }

  echo "codex-spark self-test: PASS"
}

if [[ "${CODEX_SPARK_ACTIVE:-0}" == "1" ]]; then
  echo "❌ Spark worker からの再委譲を拒否しました（nested delegation）" >&2
  exit 65
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --write)
      SANDBOX="workspace-write"
      shift
      ;;
    --read-only)
      SANDBOX="read-only"
      shift
      ;;
    --cd)
      [[ $# -ge 2 ]] || die_usage "--cd にはディレクトリが必要です"
      WORKDIR="$2"
      shift 2
      ;;
    --self-test)
      run_self_test
      exit $?
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    -*)
      die_usage "不明なオプション: $1"
      ;;
    *)
      break
      ;;
  esac
done

if [[ $# -gt 0 ]]; then
  PROMPT="$*"
elif [[ ! -t 0 ]]; then
  PROMPT="$(</dev/stdin)"
fi

[[ -n "${PROMPT//[[:space:]]/}" ]] || die_usage "委譲するタスクが空です"
[[ -d "$WORKDIR" ]] || die_usage "作業ディレクトリが存在しません: $WORKDIR"
WORKDIR="$(cd "$WORKDIR" && pwd -P)"
git -C "$WORKDIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || die_usage "Git worktree 内で実行してください: $WORKDIR"

if [[ "${CODEX_SPARK_DRY_RUN:-0}" == "1" ]]; then
  printf 'model=%s\neffort=%s\nsandbox=%s\nworkdir=%s\nprompt=present\n' \
    "$SPARK_MODEL" "$SPARK_EFFORT" "$SANDBOX" "$WORKDIR"
  exit 0
fi

command -v codex >/dev/null 2>&1 || {
  echo "❌ codex CLI が見つかりません。親エージェントで作業を継続してください。" >&2
  exit 69
}

if [[ "$SANDBOX" == "workspace-write" ]]; then
  MODE_RULE="指定された対象ファイルだけを最小差分で変更し、可能なら焦点を絞った検証を実行してください。"
else
  MODE_RULE="読み取り専用です。ファイルを変更せず、調査結果または提案だけを返してください。"
fi

FULL_PROMPT="あなたは親エージェントから呼ばれた bounded Codex-Spark worker です。

必須制約:
- 下記タスクだけを扱い、範囲を広げない。
- ${MODE_RULE}
- scripts/codex-spark.sh の再実行、別エージェントへの委譲、モデル変更をしない。
- git commit / push、deploy、外部サービスへの書き込み、secret・認証・権限の変更をしない。
- 完了時は変更ファイル、実行した検証、残る不確実性を簡潔に返す。成功を自己申告だけで断定しない。

--- 委譲タスク ---
${PROMPT}"

echo "▶ ${SPARK_MODEL} (${SANDBOX}) に bounded task を委譲します" >&2
CODEX_SPARK_ACTIVE=1 codex exec \
  --ephemeral \
  --color never \
  --config 'shell_environment_policy.set.CODEX_SPARK_ACTIVE="1"' \
  --config "model_reasoning_effort=\"${SPARK_EFFORT}\"" \
  --model "$SPARK_MODEL" \
  --sandbox "$SANDBOX" \
  --cd "$WORKDIR" \
  "$FULL_PROMPT"
