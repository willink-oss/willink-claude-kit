#!/usr/bin/env bash
# =============================================================
# goal-loop.sh — ゴール指向ループの停止プリミティブ（自前の /goal）
#
# Claude blog "Getting started with loops" の goal-based loop（決定論的な
# 成功条件で停止・最大ターンでキャップ）を、native /goal 非搭載環境向けに
# 再現する最小ハーネス。「回す仕組み」ではなく「止める/検証する仕組み」を提供する。
#
# エージェントは各作業イテレーションの冒頭で本スクリプトを呼び、exit code で分岐する:
#   exit 0 → ✅ GOAL MET   : ゴール達成。ループ終了
#   exit 1 → 🔁 CONTINUE   : 未達 & 試行上限内。もう1周작業する
#   exit 2 → 🛑 CAP REACHED: 上限到達。停止して人間へエスカレーション（原則 P2/試行上限）
#
# 使い方:
#   goal-loop.sh --goal "<説明>" --check "<shell cmd>" --max <N> --state <file>
#     --check : exit 0 でゴール達成とみなす決定論的コマンド（例: カバレッジ閾値・テスト緑）
#     --max   : 最大試行回数（記事の "stop after N tries"）
#     --state : 試行回数の永続先（省略時 .goal-loop-state）
#   goal-loop.sh --reset --state <file>   # 状態リセット
#
# 設計:
#   - 決定論的（--check の exit code のみが真実。自己申告で「達成」と書かない）
#   - fail-safe: --check 自体がエラー(非0)なら「未達」として CONTINUE/CAP に倒す
#   - 依存は POSIX shell のみ・BSD grep 互換
# =============================================================
set -uo pipefail

GOAL=""; CHECK=""; MAX=5; STATE=".goal-loop-state"; RESET=0; SELFTEST=0
while [ $# -gt 0 ]; do
  case "$1" in
    --goal)  GOAL="$2"; shift 2 ;;
    --check) CHECK="$2"; shift 2 ;;
    --max)   MAX="$2"; shift 2 ;;
    --state) STATE="$2"; shift 2 ;;
    --reset) RESET=1; shift ;;
    --self-test) SELFTEST=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 3 ;;
  esac
done

# -------------------------------------------------------------
# --self-test: hermetic（一時ディレクトリのみ・実データに触れない）
#
# 「ハードコードされた成功」を禁じるため、達成/継続/上限/リセット/
# check 自体のエラー の 5 挙動をすべて exit code で assert する。
# 上限ケースを入れていないと、永遠に CONTINUE を返す実装がテストに通る。
# -------------------------------------------------------------
if [ "$SELFTEST" = "1" ]; then
  self="${BASH_SOURCE[0]}"
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/goal-loop-st.XXXXXX")" || exit 1
  trap 'rm -rf "$tmp"' EXIT
  st="$tmp/state"
  fails=0
  run() { bash "$self" --goal t --check "$1" --max "$2" --state "$st" >/dev/null 2>&1; echo $?; }
  assert() { # assert <label> <expected> <actual>
    if [ "$2" = "$3" ]; then printf '  ok   %s (exit %s)\n' "$1" "$3"
    else printf '  FAIL %s: expected exit %s, got %s\n' "$1" "$2" "$3"; fails=$((fails+1)); fi
  }

  assert "達成 → GOAL MET"        0 "$(run true 3)"
  assert "未達 1 回目 → CONTINUE"  1 "$(run false 3)"
  assert "未達 2 回目 → CONTINUE"  1 "$(run false 3)"
  assert "未達 3 回目 → CONTINUE"  1 "$(run false 3)"
  assert "上限超過 → CAP"          2 "$(run false 3)"
  # 達成すると state がリセットされ、次の未達は 1 回目に戻る
  assert "達成で state リセット"    0 "$(run true 3)"
  assert "リセット後 → CONTINUE"   1 "$(run false 3)"
  # check 自体がエラー終了（コマンド不在）でも「達成」に倒れない
  assert "check がエラー → 未達扱い" 1 "$(run 'command-that-does-not-exist-xyz' 3)"
  # --check 欠落は引数不正（0 と区別する）
  bash "$self" --goal t --state "$st" >/dev/null 2>&1
  assert "--check 欠落 → 引数不正"  3 "$?"

  if [ "$fails" -eq 0 ]; then echo "goal-loop.sh --self-test: PASS (9 ケース)"; exit 0; fi
  echo "goal-loop.sh --self-test: FAIL ($fails/9)"; exit 1
fi

if [ "$RESET" = "1" ]; then rm -f "$STATE"; echo "goal-loop: state reset ($STATE)"; exit 0; fi
[ -n "$CHECK" ] || { echo "goal-loop: --check is required" >&2; exit 3; }

# 決定論的ゴール判定
if eval "$CHECK" >/dev/null 2>&1; then
  rm -f "$STATE"
  echo "✅ GOAL MET: ${GOAL:-<goal>}  （--check 通過・状態リセット）"
  exit 0
fi

# 未達 → 試行カウント
attempt=$(cat "$STATE" 2>/dev/null || echo 0)
attempt=$((attempt + 1))
echo "$attempt" > "$STATE"

if [ "$attempt" -gt "$MAX" ]; then
  echo "🛑 CAP REACHED: ${GOAL:-<goal>} — ${attempt} 回目で上限(${MAX})超過。停止してエスカレーション"
  echo "   （記事の 'stop after N tries' / 原則 P2 attempt cap。無限ループ防止）"
  exit 2
fi

echo "🔁 CONTINUE (attempt ${attempt}/${MAX}): ${GOAL:-<goal>}"
echo "   → --check 未通過。あと 1 周作業して再度 goal-loop を回す"
exit 1
